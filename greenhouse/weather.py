from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from greenhouse.models import WeatherSnapshot, parse_datetime


class WeatherProviderError(RuntimeError):
    """Fehler beim Abruf oder bei der Verarbeitung externer Wetterdaten."""


class WeatherProvider(Protocol):
    def fetch(self, now: datetime) -> WeatherSnapshot:
        ...


class OpenMeteoWeatherClient:
    """Kleiner, abhängigkeitsfreier Client für die Open-Meteo Forecast API."""

    provider_id = "open_meteo"

    def __init__(
        self,
        *,
        latitude: float,
        longitude: float,
        forecast_horizon_hours: int = 6,
        timeout_seconds: float = 10.0,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.forecast_horizon_hours = max(
            1, min(48, int(forecast_horizon_hours))
        )
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.base_url = base_url.rstrip("?")
        self._opener = opener

    def fetch(self, now: datetime) -> WeatherSnapshot:
        query = urlencode(
            {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "current": "temperature_2m,relative_humidity_2m",
                "hourly": "precipitation_probability,precipitation",
                "forecast_days": 2,
                "timezone": "auto",
            }
        )
        try:
            with self._opener(
                f"{self.base_url}?{query}", timeout=self.timeout_seconds
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return self._parse_payload(payload, now)
        except WeatherProviderError:
            raise
        except Exception as exc:
            raise WeatherProviderError(f"Open-Meteo-Abruf fehlgeschlagen: {exc}") from exc

    def _parse_payload(
        self, payload: Mapping[str, Any], now: datetime
    ) -> WeatherSnapshot:
        if payload.get("error"):
            raise WeatherProviderError(
                f"Open-Meteo meldet einen Fehler: {payload.get('reason', 'unbekannt')}"
            )
        current = payload.get("current")
        hourly = payload.get("hourly")
        if not isinstance(current, Mapping) or not isinstance(hourly, Mapping):
            raise WeatherProviderError("Open-Meteo-Antwort enthält keine Wetterdaten")

        weather_time = parse_datetime(current.get("time")) or now
        end = weather_time + timedelta(hours=self.forecast_horizon_hours)
        hourly_times = hourly.get("time", [])
        probabilities = hourly.get("precipitation_probability", [])
        precipitation = hourly.get("precipitation", [])
        selected_probabilities: list[float] = []
        selected_precipitation: list[float] = []

        for index, raw_time in enumerate(hourly_times):
            timestamp = parse_datetime(raw_time)
            if timestamp is None or timestamp < weather_time or timestamp >= end:
                continue
            probability = self._float_at(probabilities, index)
            rain = self._float_at(precipitation, index)
            if probability is not None:
                selected_probabilities.append(probability)
            if rain is not None:
                selected_precipitation.append(max(0.0, rain))

        return WeatherSnapshot(
            timestamp=weather_time,
            outside_temperature_c=self._optional_float(
                current.get("temperature_2m")
            ),
            outside_humidity_percent=self._optional_float(
                current.get("relative_humidity_2m")
            ),
            precipitation_mm=(
                round(sum(selected_precipitation), 3)
                if selected_precipitation
                else None
            ),
            precipitation_probability_percent=(
                max(selected_probabilities)
                if selected_probabilities
                else None
            ),
            provider=self.provider_id,
        )

    @classmethod
    def _float_at(cls, values: Any, index: int) -> float | None:
        if not isinstance(values, list) or index >= len(values):
            return None
        return cls._optional_float(values[index])

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class CachedWeatherProvider:
    """Begrenzt Netzwerkabrufe und liefert bei kurzen Ausfällen den letzten Wert."""

    def __init__(
        self,
        provider: WeatherProvider,
        *,
        refresh_seconds: float = 900.0,
        max_stale_seconds: float = 3600.0,
        cached_snapshot: WeatherSnapshot | None = None,
    ) -> None:
        self.provider = provider
        self.refresh_seconds = max(1.0, float(refresh_seconds))
        self.max_stale_seconds = max(0.0, float(max_stale_seconds))
        self.cached_snapshot = cached_snapshot
        self.last_attempt_at: datetime | None = None
        self.last_error: str | None = None

    def get(
        self, now: datetime
    ) -> tuple[WeatherSnapshot | None, str | None]:
        if (
            self.last_attempt_at is not None
            and (now - self.last_attempt_at).total_seconds()
            < self.refresh_seconds
        ):
            return self._usable_cache(now), self.last_error

        self.last_attempt_at = now
        try:
            self.cached_snapshot = self.provider.fetch(now)
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
        return self._usable_cache(now), self.last_error

    def _usable_cache(self, now: datetime) -> WeatherSnapshot | None:
        if self.cached_snapshot is None:
            return None
        age = max(
            0.0, (now - self.cached_snapshot.timestamp).total_seconds()
        )
        return (
            self.cached_snapshot
            if age <= self.max_stale_seconds
            else None
        )


def weather_to_mapping(snapshot: WeatherSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    result = asdict(snapshot)
    result["timestamp"] = snapshot.timestamp.isoformat(timespec="seconds")
    return result


def weather_from_mapping(
    data: Mapping[str, Any] | None,
) -> WeatherSnapshot | None:
    if not data:
        return None
    timestamp = parse_datetime(data.get("timestamp"))
    if timestamp is None:
        return None

    def optional_float(key: str) -> float | None:
        try:
            value = data.get(key)
            return None if value in (None, "") else float(value)
        except (TypeError, ValueError):
            return None

    return WeatherSnapshot(
        timestamp=timestamp,
        outside_temperature_c=optional_float("outside_temperature_c"),
        outside_humidity_percent=optional_float("outside_humidity_percent"),
        precipitation_mm=optional_float("precipitation_mm"),
        precipitation_probability_percent=optional_float(
            "precipitation_probability_percent"
        ),
        provider=data.get("provider") or None,
    )
