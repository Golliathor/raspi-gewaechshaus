from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta

from greenhouse.config import DEFAULT_CONFIG, validate_config
from greenhouse.controllers import (
    BaselineFixedController,
    registered_controller_ids,
)
from greenhouse.models import ActuatorState, ControlContext, SensorSnapshot
from greenhouse.runtime import ControlEngine


NOW = datetime(2026, 7, 24, 12)


def snapshot(
    temperature: float | None,
    humidity: float | None,
    soil: tuple[float | None, ...] = (50, None, None),
    *,
    timestamp: datetime = NOW,
) -> SensorSnapshot:
    return SensorSnapshot(timestamp, temperature, humidity, soil, 60)


def context(
    state: ActuatorState | None = None,
    *,
    watering_due: bool = False,
) -> ControlContext:
    return ControlContext(
        actuator_state=state or ActuatorState(),
        watering_check_due=watering_due,
    )


class BaselineFixedControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.controller = BaselineFixedController()

    def test_controller_is_registered_and_active_by_default(self) -> None:
        self.assertIn("baseline_fixed", registered_controller_ids())
        self.assertEqual(self.config["controller"]["active"], "baseline_fixed")
        self.assertEqual(validate_config(self.config), [])

    def test_fans_follow_fixed_thresholds_inclusively(self) -> None:
        at_temperature_threshold = self.controller.decide(
            snapshot(28, 20), context(), self.config
        )
        at_humidity_threshold = self.controller.decide(
            snapshot(18, 50), context(), self.config
        )
        circulation_threshold = self.controller.decide(
            snapshot(24, 20), context(), self.config
        )

        self.assertTrue(at_temperature_threshold.exhaust)
        self.assertTrue(at_humidity_threshold.exhaust)
        self.assertTrue(circulation_threshold.circulation)

    def test_previous_state_does_not_create_hysteresis(self) -> None:
        input_snapshot = snapshot(23, 44)
        from_off = self.controller.decide(
            input_snapshot, context(ActuatorState(False, False, False)), self.config
        )
        from_on = self.controller.decide(
            input_snapshot, context(ActuatorState(True, True, False)), self.config
        )

        self.assertFalse(from_off.exhaust)
        self.assertFalse(from_off.circulation)
        self.assertEqual(from_off.exhaust, from_on.exhaust)
        self.assertEqual(from_off.circulation, from_on.circulation)
        self.assertIn("exhaust_off_below_fixed_thresholds", from_on.reasons)
        self.assertIn("circulation_off_below_fixed_thresholds", from_on.reasons)

    def test_high_humidity_does_not_exhaust_below_minimum_temperature(self) -> None:
        decision = self.controller.decide(
            snapshot(17.9, 100), context(), self.config
        )
        self.assertFalse(decision.exhaust)
        self.assertTrue(decision.circulation)

    def test_watering_uses_fixed_duration_only_when_due_and_dry(self) -> None:
        self.config["watering_seconds"] = 99
        self.config["soil_sensors"][0]["dry_below_percent"] = 5
        self.config["controllers"]["baseline_fixed"]["watering_seconds"] = 7
        not_due = self.controller.decide(
            snapshot(20, 40, (20, None, None)), context(), self.config
        )
        dry = self.controller.decide(
            snapshot(20, 40, (20, None, None)),
            context(watering_due=True),
            self.config,
        )
        wet = self.controller.decide(
            snapshot(20, 40, (35.1, None, None)),
            context(watering_due=True),
            self.config,
        )

        self.assertEqual(not_due.watering_seconds, 0)
        self.assertEqual(
            dry.watering_seconds,
            self.config["controllers"]["baseline_fixed"]["watering_seconds"],
        )
        self.assertEqual(dry.diagnostics["dry_soil_sensor_indices"], (0,))
        self.assertEqual(wet.watering_seconds, 0)

    def test_missing_air_and_soil_values_request_safe_off(self) -> None:
        decision = self.controller.decide(
            snapshot(None, None, (None, None, None)),
            context(ActuatorState(True, True, False), watering_due=True),
            self.config,
        )
        self.assertFalse(decision.exhaust)
        self.assertFalse(decision.circulation)
        self.assertEqual(decision.watering_seconds, 0)
        self.assertIn("watering_off_no_valid_soil", decision.reasons)

    def test_no_controller_lockout_after_completed_watering_pulse(self) -> None:
        engine = ControlEngine(self.controller, self.config)
        first = engine.step(
            snapshot(20, 40, (20, None, None)),
            now=NOW,
            watering_check_due=True,
        )
        second_time = NOW + timedelta(seconds=11)
        second = engine.step(
            snapshot(20, 40, (20, None, None), timestamp=second_time),
            now=second_time,
            watering_check_due=True,
        )

        self.assertEqual(first.watering_started_seconds, 10)
        self.assertEqual(second.watering_started_seconds, 10)
        self.assertTrue(second.state.water_valve)


if __name__ == "__main__":
    unittest.main()
