from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta

from greenhouse.config import DEFAULT_CONFIG
from greenhouse.controllers import LegacyController
from greenhouse.models import (
    ActuatorState,
    ControlContext,
    ControlDecision,
    SensorSnapshot,
)
from greenhouse.runtime import ControlEngine
from greenhouse.safety import SafetyLayer


NOW = datetime(2026, 7, 24, 12)


def snapshot(
    temperature: float | None = 20,
    humidity: float | None = 40,
    soil: tuple[float | None, ...] = (50, None, None),
    *,
    timestamp: datetime = NOW,
) -> SensorSnapshot:
    return SensorSnapshot(timestamp, temperature, humidity, soil, 60)


class LegacyControllerCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = LegacyController()
        self.config = copy.deepcopy(DEFAULT_CONFIG)

    def context(
        self,
        *,
        exhaust: bool = False,
        circulation: bool = False,
        watering_due: bool = False,
    ) -> ControlContext:
        return ControlContext(
            ActuatorState(exhaust, circulation, False),
            watering_check_due=watering_due,
        )

    def test_exhaust_uses_existing_on_and_off_hysteresis(self) -> None:
        on = self.controller.decide(
            snapshot(28, 40), self.context(), self.config
        )
        self.assertTrue(on.exhaust)

        held = self.controller.decide(
            snapshot(26, 45), self.context(exhaust=True), self.config
        )
        self.assertTrue(held.exhaust)

        off = self.controller.decide(
            snapshot(25, 40), self.context(exhaust=True), self.config
        )
        self.assertFalse(off.exhaust)

    def test_circulation_uses_existing_on_and_off_hysteresis(self) -> None:
        on = self.controller.decide(
            snapshot(24, 40), self.context(), self.config
        )
        self.assertTrue(on.circulation)
        held = self.controller.decide(
            snapshot(23, 40), self.context(circulation=True), self.config
        )
        self.assertTrue(held.circulation)
        off = self.controller.decide(
            snapshot(22, 38), self.context(circulation=True), self.config
        )
        self.assertFalse(off.circulation)

    def test_watering_is_requested_only_when_check_is_due_and_soil_is_dry(self) -> None:
        not_due = self.controller.decide(
            snapshot(soil=(20, None, None)), self.context(), self.config
        )
        due = self.controller.decide(
            snapshot(soil=(20, None, None)),
            self.context(watering_due=True),
            self.config,
        )
        self.assertEqual(not_due.watering_seconds, 0)
        self.assertEqual(due.watering_seconds, self.config["watering_seconds"])
        self.assertIn("watering_soil_1_dry", due.reasons)


class SafetyAndRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.safety = SafetyLayer()

    def test_safety_limits_pulse_and_daily_amount(self) -> None:
        self.config["safety"]["max_watering_pulse_seconds"] = 20
        self.config["safety"]["max_daily_watering_seconds"] = 30
        result = self.safety.apply(
            ControlDecision(False, False, 50),
            snapshot(),
            ControlContext(ActuatorState(), daily_watering_seconds=15),
            self.config,
            NOW,
        )
        self.assertEqual(result.decision.watering_seconds, 15)
        self.assertEqual(
            result.overrides,
            ("watering_pulse_limited", "daily_watering_limit"),
        )

    def test_safety_blocks_watering_without_valid_soil(self) -> None:
        result = self.safety.apply(
            ControlDecision(False, False, 10),
            snapshot(soil=(None, None, None)),
            ControlContext(ActuatorState()),
            self.config,
            NOW,
        )
        self.assertEqual(result.decision.watering_seconds, 0)
        self.assertIn("watering_blocked_no_valid_soil", result.overrides)

    def test_disabled_soil_value_does_not_make_watering_safe(self) -> None:
        result = self.safety.apply(
            ControlDecision(False, False, 10),
            snapshot(soil=(None, 70, None)),
            ControlContext(ActuatorState()),
            self.config,
            NOW,
        )
        self.assertEqual(result.decision.watering_seconds, 0)
        self.assertIn("watering_blocked_no_valid_soil", result.overrides)

    def test_engine_finishes_watering_without_blocking(self) -> None:
        engine = ControlEngine(LegacyController(), self.config)
        started = engine.step(
            snapshot(soil=(20, None, None)), watering_check_due=True
        )
        self.assertTrue(started.state.water_valve)
        self.assertEqual(
            started.watering_started_seconds, self.config["watering_seconds"]
        )

        running = engine.step(
            snapshot(timestamp=NOW + timedelta(seconds=5)),
            now=NOW + timedelta(seconds=5),
        )
        self.assertTrue(running.state.water_valve)

        stopped = engine.step(
            snapshot(timestamp=NOW + timedelta(seconds=11)),
            now=NOW + timedelta(seconds=11),
        )
        self.assertFalse(stopped.state.water_valve)
        self.assertIn("water_valve:off", stopped.transitions)


if __name__ == "__main__":
    unittest.main()
