from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta

from greenhouse.config import DEFAULT_CONFIG
from greenhouse.models import WeatherSnapshot
from greenhouse.weather import (
    CachedWeatherProvider,
    OpenMeteoWeatherClient,
    WeatherProviderError,
    weather_from_mapping,
    weather_to_mapping,
)
from web.automation_daemon import build_snapshot, build_weather_service


NOW = datetime(2026, 7, 24, 12)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class SequenceProvider:
    def __init__(self, values: list[WeatherSnapshot | Exception]) -> None:
        self.values = values
        self.calls = 0

    def fetch(self, now: datetime) -> WeatherSnapshot:
        value = self.values[self.calls]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return value


class WeatherClientTests(unittest.TestCase):
    def test_daemon_injects_weather_and_only_builds_enabled_service(self) -> None:
        weather = WeatherSnapshot(NOW, outside_temperature_c=20)
        built = build_snapshot(
            NOW,
            {
                "timestamp": NOW,
                "temperature_c": 25,
                "humidity_percent": 50,
            },
            [{"moisture_percent": 40}],
            {"light_percent": 60},
            NOW,
            weather,
        )
        self.assertEqual(built.weather, weather)

        config = copy.deepcopy(DEFAULT_CONFIG)
        self.assertIsNone(build_weather_service(config))
        config["weather"].update(
            enabled=True,
            latitude=52.52,
            longitude=13.405,
        )
        self.assertIsInstance(
            build_weather_service(config), CachedWeatherProvider
        )

    def test_open_meteo_forecast_is_reduced_to_canonical_snapshot(self) -> None:
        requested: dict[str, object] = {}
        payload = {
            "current": {
                "time": "2026-07-24T12:00",
                "temperature_2m": 24.5,
                "relative_humidity_2m": 48,
            },
            "hourly": {
                "time": [
                    "2026-07-24T11:00",
                    "2026-07-24T12:00",
                    "2026-07-24T13:00",
                    "2026-07-24T14:00",
                    "2026-07-24T15:00",
                ],
                "temperature_2m": [23, 25, 27, 29, 31],
                "precipitation_probability": [0, 20, 70, 40, 90],
                "precipitation": [0, 0.1, 0.5, 1.2, 8],
            },
        }

        def opener(url: str, *, timeout: float) -> FakeResponse:
            requested.update(url=url, timeout=timeout)
            return FakeResponse(payload)

        client = OpenMeteoWeatherClient(
            latitude=52.5,
            longitude=13.4,
            forecast_horizon_hours=3,
            timeout_seconds=4,
            opener=opener,
        )
        snapshot = client.fetch(NOW)

        self.assertIn("latitude=52.5", str(requested["url"]))
        self.assertIn("timezone=auto", str(requested["url"]))
        self.assertEqual(requested["timeout"], 4)
        self.assertEqual(snapshot.outside_temperature_c, 24.5)
        self.assertEqual(snapshot.outside_humidity_percent, 48)
        self.assertEqual(snapshot.precipitation_mm, 1.8)
        self.assertEqual(snapshot.precipitation_probability_percent, 70)

    def test_cache_limits_requests_and_expires_after_provider_failure(self) -> None:
        first = WeatherSnapshot(NOW, outside_temperature_c=20)
        provider = SequenceProvider(
            [first, WeatherProviderError("offline"), WeatherProviderError("offline")]
        )
        cache = CachedWeatherProvider(
            provider,
            refresh_seconds=60,
            max_stale_seconds=180,
        )

        self.assertEqual(cache.get(NOW), (first, None))
        self.assertEqual(cache.get(NOW + timedelta(seconds=30)), (first, None))
        cached, error = cache.get(NOW + timedelta(seconds=70))
        self.assertEqual(cached, first)
        self.assertEqual(error, "offline")
        expired, error = cache.get(NOW + timedelta(seconds=190))
        self.assertIsNone(expired)
        self.assertEqual(error, "offline")
        self.assertEqual(provider.calls, 3)

    def test_weather_state_round_trip(self) -> None:
        original = WeatherSnapshot(
            NOW,
            outside_temperature_c=18,
            precipitation_mm=2.5,
            provider="test",
        )
        self.assertEqual(
            weather_from_mapping(weather_to_mapping(original)),
            original,
        )


if __name__ == "__main__":
    unittest.main()
