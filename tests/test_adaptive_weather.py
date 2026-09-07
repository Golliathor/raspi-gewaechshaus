from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta

from greenhouse.config import DEFAULT_CONFIG, validate_config
from greenhouse.controllers import (
    AdaptiveLocalController,
    AdaptiveWeatherController,
    registered_controller_ids,
)
from greenhouse.models import (
    ActuatorState,
    ControlContext,
    SensorSnapshot,
    WeatherSnapshot,
)
from greenhouse.runtime import ControlEngine


NOW = datetime(2026, 7, 24, 12)


def snapshot(
    temperature: float = 27,
    humidity: float = 45,
    soil: float = 40,
    *,
    weather: WeatherSnapshot | None = None,
) -> SensorSnapshot:
    return SensorSnapshot(
        NOW,
        temperature,
        humidity,
        (soil, None, None),
        0,
        weather=weather,
    )


def context(*, watering_due: bool = False) -> ControlContext:
    return ControlContext(
        actuator_state=ActuatorState(),
        watering_check_due=watering_due,
        now=NOW,
    )


class AdaptiveWeatherControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.local = AdaptiveLocalController()
        self.weather = AdaptiveWeatherController()

    def test_controller_is_registered_active_and_valid(self) -> None:
        self.assertIn("adaptive_weather", registered_controller_ids())
        self.assertEqual(
            self.config["controller"]["active"], "adaptive_weather"
        )
        self.assertEqual(validate_config(self.config), [])

    def test_missing_or_stale_weather_falls_back_to_local_decision(self) -> None:
        local = self.local.decide(snapshot(), context(), self.config)
        missing = self.weather.decide(snapshot(), context(), self.config)
        stale = self.weather.decide(
            snapshot(
                weather=WeatherSnapshot(
                    NOW - timedelta(hours=2),
                    outside_temperature_c=10,
                    outside_humidity_percent=10,
                )
            ),
            context(),
            self.config,
        )

        self.assertEqual(
            (missing.exhaust, missing.circulation, missing.watering_seconds),
            (local.exhaust, local.circulation, local.watering_seconds),
        )
        self.assertEqual(
            (stale.exhaust, stale.circulation, stale.watering_seconds),
            (local.exhaust, local.circulation, local.watering_seconds),
        )
        self.assertEqual(missing.diagnostics["weather_status"], "missing")
        self.assertEqual(stale.diagnostics["weather_status"], "stale")

    def test_cool_dry_outdoor_air_starts_exhaust_earlier(self) -> None:
        current = snapshot(
            weather=WeatherSnapshot(
                NOW,
                outside_temperature_c=17,
                outside_humidity_percent=15,
                provider="test",
            )
        )
        local = self.local.decide(current, context(), self.config)
        weather = self.weather.decide(current, context(), self.config)

        self.assertFalse(local.exhaust)
        self.assertTrue(weather.exhaust)
        self.assertTrue(weather.diagnostics["weather_used"])
        self.assertGreater(
            weather.diagnostics["weather_scores"]["cooling_opportunity"], 0
        )

    def test_hot_humid_outdoor_air_delays_exhaust(self) -> None:
        current = snapshot(
            temperature=28,
            humidity=50,
            weather=WeatherSnapshot(
                NOW,
                outside_temperature_c=40,
                outside_humidity_percent=90,
            ),
        )
        self.assertTrue(self.local.decide(current, context(), self.config).exhaust)
        self.assertFalse(
            self.weather.decide(current, context(), self.config).exhaust
        )

    def test_dehumidifying_opportunity_uses_absolute_humidity(self) -> None:
        current = snapshot(
            temperature=25,
            humidity=70,
            weather=WeatherSnapshot(
                NOW,
                outside_temperature_c=5,
                outside_humidity_percent=90,
            ),
        )
        decision = self.weather.decide(current, context(), self.config)
        self.assertGreater(
            decision.diagnostics["weather_scores"][
                "dehumidifying_opportunity"
            ],
            0,
        )
        self.assertGreater(
            decision.diagnostics["weather_inputs"][
                "inside_absolute_humidity_g_m3"
            ],
            decision.diagnostics["weather_inputs"][
                "outside_absolute_humidity_g_m3"
            ],
        )

    def test_outdoor_heat_increases_watering_demand(self) -> None:
        current = snapshot(
            temperature=26,
            soil=34,
            weather=WeatherSnapshot(
                NOW,
                outside_temperature_c=40,
                outside_humidity_percent=50,
                precipitation_mm=0,
                precipitation_probability_percent=0,
            ),
        )
        local = self.local.decide(
            current, context(watering_due=True), self.config
        )
        weather = self.weather.decide(
            current, context(watering_due=True), self.config
        )
        self.assertGreater(weather.watering_seconds, local.watering_seconds)
        self.assertEqual(
            weather.reasons[-1], "watering_on_adaptive_weather_demand"
        )

    def test_rain_reduces_demand_but_never_blocks_critical_soil(self) -> None:
        rainy = WeatherSnapshot(
            NOW,
            outside_temperature_c=22,
            outside_humidity_percent=70,
            precipitation_mm=5,
            precipitation_probability_percent=100,
        )
        moderate = self.weather.decide(
            snapshot(soil=34, weather=rainy),
            context(watering_due=True),
            self.config,
        )
        critical = self.weather.decide(
            snapshot(soil=19, weather=rainy),
            context(watering_due=True),
            self.config,
        )

        self.assertEqual(moderate.watering_seconds, 0)
        self.assertGreater(critical.watering_seconds, 0)
        self.assertEqual(
            critical.diagnostics["weather_scores"]["rain_effective"], 0
        )

    def test_critical_soil_is_not_blocked_by_persisted_false_rearm(self) -> None:
        decision = self.weather.decide(
            snapshot(soil=19),
            ControlContext(
                actuator_state=ActuatorState(),
                last_watering_at=NOW - timedelta(hours=2),
                watering_check_due=True,
                now=NOW,
                controller_state={"watering_armed": False},
            ),
            self.config,
        )

        self.assertGreater(decision.watering_seconds, 0)
        self.assertEqual(decision.diagnostics["watering_rearm_mode"], "cooldown")
        self.assertNotIn("watering_blocked_hysteresis", decision.reasons)

    def test_reported_persisted_state_starts_another_safe_pulse(self) -> None:
        local_config = self.config["controllers"]["adaptive_local"]
        local_config.update(
            {
                "soil_moisture_on_percent": 40,
                "soil_moisture_off_percent": 50,
                "watering_base_seconds": 1200,
                "watering_min_seconds": 300,
                "watering_max_seconds": 2400,
                "watering_cooldown_seconds": 600,
            }
        )
        self.config["safety"].update(
            {
                "max_watering_pulse_seconds": 1200,
                "max_daily_watering_seconds": 7200,
            }
        )
        now = datetime(2026, 9, 7, 12)
        engine = ControlEngine(self.weather, self.config)
        engine.restore(
            {
                "control_runtime": {
                    "controller_id": "adaptive_weather",
                    "last_watering_at": "2026-09-06T10:12:26",
                    "controller_state": {
                        "last_observed_watering_at": "2026-09-06T10:12:26",
                        "watering_armed": False,
                    },
                }
            }
        )

        result = engine.step(
            SensorSnapshot(now, 25, 45, (24.3, None, None), 0),
            now=now,
            watering_check_due=True,
        )

        self.assertGreater(result.requested.watering_seconds, 1200)
        self.assertEqual(result.watering_started_seconds, 1200)
        self.assertIn("watering_pulse_limited", result.safety_overrides)
        self.assertNotIn(
            "watering_blocked_hysteresis", result.requested.reasons
        )

    def test_invalid_weather_configuration_is_reported(self) -> None:
        self.config["weather"]["enabled"] = True
        self.config["weather"]["latitude"] = 100
        self.config["weather"]["longitude"] = None
        errors = validate_config(self.config)
        self.assertTrue(any("weather.latitude" in error for error in errors))
        self.assertTrue(any("weather.longitude" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
