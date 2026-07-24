from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta

from greenhouse.config import DEFAULT_CONFIG, validate_config
from greenhouse.controllers import AdaptiveLocalController, registered_controller_ids
from greenhouse.models import (
    ActuatorState,
    ControlContext,
    SensorSnapshot,
    WeatherSnapshot,
)
from greenhouse.runtime import ControlEngine
from greenhouse.trends import linear_trend_per_minute


NOW = datetime(2026, 7, 24, 12)


def snapshot(
    temperature: float | None = 20,
    humidity: float | None = 40,
    soil: tuple[float | None, ...] = (50, None, None),
    light: float | None = 0,
    *,
    timestamp: datetime = NOW,
    weather: WeatherSnapshot | None = None,
) -> SensorSnapshot:
    return SensorSnapshot(
        timestamp,
        temperature,
        humidity,
        soil,
        light,
        weather=weather,
    )


def context(
    *,
    state: ActuatorState | None = None,
    history: tuple[SensorSnapshot, ...] = (),
    now: datetime = NOW,
    transitions: dict[str, datetime] | None = None,
    last_watering_at: datetime | None = None,
    watering_due: bool = False,
    controller_state: dict[str, object] | None = None,
) -> ControlContext:
    return ControlContext(
        actuator_state=state or ActuatorState(),
        history=history,
        last_transition_at=transitions or {},
        last_watering_at=last_watering_at,
        watering_check_due=watering_due,
        now=now,
        controller_state=controller_state or {},
    )


class TrendTests(unittest.TestCase):
    def test_linear_trend_ignores_duplicate_timestamps(self) -> None:
        start = NOW - timedelta(minutes=10)
        trend = linear_trend_per_minute(
            (
                (start, 20),
                (start, 21),
                (NOW, 26),
            ),
            now=NOW,
            window_seconds=900,
            minimum_span_seconds=120,
        )
        self.assertAlmostEqual(trend, 0.5)

    def test_linear_trend_requires_minimum_time_span(self) -> None:
        trend = linear_trend_per_minute(
            (
                (NOW - timedelta(seconds=30), 20),
                (NOW, 30),
            ),
            now=NOW,
            window_seconds=900,
            minimum_span_seconds=120,
        )
        self.assertEqual(trend, 0)


class AdaptiveLocalControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.controller = AdaptiveLocalController()

    def test_controller_is_registered_active_and_valid(self) -> None:
        self.assertIn("adaptive_local", registered_controller_ids())
        self.assertEqual(validate_config(self.config), [])

    def test_rising_temperature_turns_exhaust_on_earlier(self) -> None:
        current = snapshot(25, 30)
        rising_history = (
            snapshot(
                20,
                30,
                timestamp=NOW - timedelta(minutes=10),
            ),
        )
        without_trend = self.controller.decide(
            current, context(), self.config
        )
        with_trend = self.controller.decide(
            current,
            context(history=rising_history),
            self.config,
        )

        self.assertFalse(without_trend.exhaust)
        self.assertTrue(with_trend.exhaust)
        self.assertAlmostEqual(
            with_trend.diagnostics["trends_per_minute"]["temperature_c"],
            0.5,
        )
        self.assertEqual(
            with_trend.diagnostics["adaptive_adjustments"][
                "temperature_threshold_reduction_c"
            ],
            3.0,
        )

    def test_bright_light_lowers_temperature_threshold(self) -> None:
        dark = self.controller.decide(
            snapshot(27, 30, light=0), context(), self.config
        )
        bright = self.controller.decide(
            snapshot(27, 30, light=100), context(), self.config
        )

        self.assertFalse(dark.exhaust)
        self.assertTrue(bright.exhaust)
        self.assertEqual(
            bright.diagnostics["adaptive_adjustments"]["light_factor"], 1.0
        )

    def test_rising_humidity_lowers_exhaust_humidity_threshold(self) -> None:
        history = (
            snapshot(
                20,
                40,
                timestamp=NOW - timedelta(minutes=5),
            ),
        )
        decision = self.controller.decide(
            snapshot(20, 45),
            context(history=history),
            self.config,
        )
        self.assertTrue(decision.exhaust)
        self.assertEqual(
            decision.diagnostics["adaptive_adjustments"][
                "effective_exhaust_humidity_on_percent"
            ],
            40.0,
        )

    def test_drying_soil_triggers_earlier_and_increases_duration(self) -> None:
        current = snapshot(25, 40, soil=(37, None, None), light=0)
        static = self.controller.decide(
            current,
            context(watering_due=True),
            self.config,
        )
        drying_history = (
            snapshot(
                25,
                40,
                soil=(45, None, None),
                light=0,
                timestamp=NOW - timedelta(minutes=10),
            ),
        )
        adaptive = self.controller.decide(
            current,
            context(history=drying_history, watering_due=True),
            self.config,
        )

        self.assertEqual(static.watering_seconds, 0)
        self.assertGreater(adaptive.watering_seconds, 0)
        self.assertGreater(
            adaptive.diagnostics["adaptive_adjustments"][
                "effective_soil_moisture_on_percent"
            ],
            37,
        )
        self.assertGreater(
            adaptive.watering_seconds,
            self.config["controllers"]["adaptive_local"][
                "watering_base_seconds"
            ],
        )

    def test_adaptive_watering_duration_is_capped(self) -> None:
        decision = self.controller.decide(
            snapshot(40, 40, soil=(0, None, None), light=100),
            context(watering_due=True),
            self.config,
        )
        self.assertEqual(
            decision.watering_seconds,
            self.config["controllers"]["adaptive_local"][
                "watering_max_seconds"
            ],
        )

    def test_weather_data_is_explicitly_ignored(self) -> None:
        dry_weather = WeatherSnapshot(
            timestamp=NOW,
            outside_temperature_c=35,
            precipitation_mm=0,
            precipitation_probability_percent=0,
            provider="test",
        )
        rainy_weather = WeatherSnapshot(
            timestamp=NOW,
            outside_temperature_c=10,
            precipitation_mm=30,
            precipitation_probability_percent=100,
            provider="test",
        )
        without_weather = self.controller.decide(
            snapshot(26, 45, soil=(34, None, None), light=60),
            context(watering_due=True),
            self.config,
        )
        with_dry_weather = self.controller.decide(
            snapshot(
                26,
                45,
                soil=(34, None, None),
                light=60,
                weather=dry_weather,
            ),
            context(watering_due=True),
            self.config,
        )
        with_rain = self.controller.decide(
            snapshot(
                26,
                45,
                soil=(34, None, None),
                light=60,
                weather=rainy_weather,
            ),
            context(watering_due=True),
            self.config,
        )

        self.assertEqual(without_weather, with_dry_weather)
        self.assertEqual(without_weather, with_rain)
        self.assertFalse(with_rain.diagnostics["weather_used"])

    def test_fan_minimum_time_and_hard_temperature_limit(self) -> None:
        held = self.controller.decide(
            snapshot(20, 30),
            context(
                state=ActuatorState(exhaust=True),
                transitions={"exhaust": NOW - timedelta(seconds=60)},
            ),
            self.config,
        )
        hard_off = self.controller.decide(
            snapshot(17.9, 100),
            context(
                state=ActuatorState(exhaust=True),
                transitions={"exhaust": NOW - timedelta(seconds=5)},
            ),
            self.config,
        )
        self.assertTrue(held.exhaust)
        self.assertIn("exhaust_hold_on_minimum_time", held.reasons)
        self.assertFalse(hard_off.exhaust)

    def test_watering_rearm_cooldown_and_state_survive_restart(self) -> None:
        first_engine = ControlEngine(self.controller, self.config)
        first = first_engine.step(
            snapshot(soil=(20, None, None)),
            now=NOW,
            watering_check_due=True,
        )
        self.assertGreater(first.watering_started_seconds, 0)

        after_pulse = NOW + timedelta(seconds=31)
        first_engine.step(
            snapshot(soil=(20, None, None), timestamp=after_pulse),
            now=after_pulse,
        )
        persisted = {"control_runtime": first_engine.export_state()}
        restored = ControlEngine(AdaptiveLocalController(), self.config)
        restored.restore(persisted)

        after_cooldown = NOW + timedelta(seconds=3601)
        blocked = restored.step(
            snapshot(soil=(20, None, None), timestamp=after_cooldown),
            now=after_cooldown,
            watering_check_due=True,
        )
        self.assertEqual(blocked.watering_started_seconds, 0)
        self.assertIn("watering_blocked_hysteresis", blocked.requested.reasons)

        wet_time = after_cooldown + timedelta(seconds=1)
        restored.step(
            snapshot(soil=(45, None, None), timestamp=wet_time),
            now=wet_time,
        )
        dry_time = wet_time + timedelta(seconds=1)
        second = restored.step(
            snapshot(soil=(20, None, None), timestamp=dry_time),
            now=dry_time,
            watering_check_due=True,
        )
        self.assertGreater(second.watering_started_seconds, 0)

    def test_manual_watering_disarms_adaptive_hysteresis(self) -> None:
        engine = ControlEngine(self.controller, self.config)
        self.assertEqual(engine.request_manual_watering(10, NOW), 10)
        after_pulse = NOW + timedelta(seconds=11)
        result = engine.step(
            snapshot(soil=(20, None, None), timestamp=after_pulse),
            now=after_pulse,
            watering_check_due=True,
        )
        self.assertFalse(engine.controller_state["watering_armed"])
        self.assertIn("watering_blocked_hysteresis", result.requested.reasons)

    def test_safety_rejection_does_not_consume_watering_rearm(self) -> None:
        self.config["safety"]["max_daily_watering_seconds"] = 60
        engine = ControlEngine(self.controller, self.config)
        engine._watering_day = NOW.date().isoformat()
        engine.daily_watering_seconds = 60
        result = engine.step(
            snapshot(soil=(20, None, None)),
            now=NOW,
            watering_check_due=True,
        )
        self.assertGreater(result.requested.watering_seconds, 0)
        self.assertEqual(result.applied.watering_seconds, 0)
        self.assertNotIn("watering_armed", engine.controller_state)


if __name__ == "__main__":
    unittest.main()
