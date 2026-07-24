from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta

from greenhouse.config import DEFAULT_CONFIG, validate_config
from greenhouse.controllers import (
    BaselineHysteresisController,
    registered_controller_ids,
)
from greenhouse.models import ActuatorState, ControlContext, SensorSnapshot
from greenhouse.runtime import ControlEngine


NOW = datetime(2026, 7, 24, 12)


def snapshot(
    temperature: float | None = 20,
    humidity: float | None = 40,
    soil: tuple[float | None, ...] = (50, None, None),
    *,
    timestamp: datetime = NOW,
) -> SensorSnapshot:
    return SensorSnapshot(timestamp, temperature, humidity, soil, 60)


def context(
    state: ActuatorState | None = None,
    *,
    now: datetime = NOW,
    transitions: dict[str, datetime] | None = None,
    last_watering_at: datetime | None = None,
    watering_due: bool = False,
    controller_state: dict[str, object] | None = None,
) -> ControlContext:
    return ControlContext(
        actuator_state=state or ActuatorState(),
        last_transition_at=transitions or {},
        last_watering_at=last_watering_at,
        watering_check_due=watering_due,
        now=now,
        controller_state=controller_state or {},
    )


class BaselineHysteresisControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.controller = BaselineHysteresisController()

    def test_controller_is_registered_active_and_valid_by_default(self) -> None:
        self.assertIn("baseline_hysteresis", registered_controller_ids())
        self.assertEqual(
            self.config["controller"]["active"], "baseline_hysteresis"
        )
        self.assertEqual(validate_config(self.config), [])

    def test_exhaust_uses_distinct_on_and_off_thresholds(self) -> None:
        switched_on = self.controller.decide(
            snapshot(28, 40), context(), self.config
        )
        held_in_band = self.controller.decide(
            snapshot(26, 45),
            context(ActuatorState(exhaust=True)),
            self.config,
        )
        switched_off = self.controller.decide(
            snapshot(25, 40),
            context(
                ActuatorState(exhaust=True),
                transitions={"exhaust": NOW - timedelta(seconds=121)},
            ),
            self.config,
        )

        self.assertTrue(switched_on.exhaust)
        self.assertTrue(held_in_band.exhaust)
        self.assertIn("exhaust_hold_on_hysteresis", held_in_band.reasons)
        self.assertFalse(switched_off.exhaust)

    def test_exhaust_minimum_on_and_off_times_are_enforced(self) -> None:
        held_on = self.controller.decide(
            snapshot(20, 30),
            context(
                ActuatorState(exhaust=True),
                transitions={"exhaust": NOW - timedelta(seconds=60)},
            ),
            self.config,
        )
        held_off = self.controller.decide(
            snapshot(30, 60),
            context(
                transitions={"exhaust": NOW - timedelta(seconds=60)}
            ),
            self.config,
        )
        allowed_on = self.controller.decide(
            snapshot(30, 60),
            context(
                transitions={"exhaust": NOW - timedelta(seconds=120)}
            ),
            self.config,
        )

        self.assertTrue(held_on.exhaust)
        self.assertIn("exhaust_hold_on_minimum_time", held_on.reasons)
        self.assertEqual(
            held_on.diagnostics["lock_remaining_seconds"]["exhaust"], 60
        )
        self.assertFalse(held_off.exhaust)
        self.assertIn("exhaust_hold_off_minimum_time", held_off.reasons)
        self.assertTrue(allowed_on.exhaust)

    def test_hard_minimum_temperature_bypasses_minimum_on_time(self) -> None:
        decision = self.controller.decide(
            snapshot(17.9, 100),
            context(
                ActuatorState(exhaust=True),
                transitions={"exhaust": NOW - timedelta(seconds=5)},
            ),
            self.config,
        )
        self.assertFalse(decision.exhaust)
        self.assertIn("exhaust_off_below_min_temperature", decision.reasons)

    def test_circulation_hysteresis_and_locks_are_independent(self) -> None:
        held_on = self.controller.decide(
            snapshot(23, 40),
            context(ActuatorState(circulation=True)),
            self.config,
        )
        locked_off = self.controller.decide(
            snapshot(25, 50),
            context(
                transitions={"circulation": NOW - timedelta(seconds=30)}
            ),
            self.config,
        )
        switched_off = self.controller.decide(
            snapshot(22, 38),
            context(
                ActuatorState(circulation=True),
                transitions={"circulation": NOW - timedelta(seconds=121)},
            ),
            self.config,
        )

        self.assertTrue(held_on.circulation)
        self.assertFalse(locked_off.circulation)
        self.assertIn("circulation_hold_off_minimum_time", locked_off.reasons)
        self.assertFalse(switched_off.circulation)

    def test_invalid_air_requests_both_fans_off(self) -> None:
        decision = self.controller.decide(
            snapshot(None, None),
            context(ActuatorState(True, True, False)),
            self.config,
        )
        self.assertFalse(decision.exhaust)
        self.assertFalse(decision.circulation)

    def test_watering_requires_rearm_and_elapsed_cooldown(self) -> None:
        engine = ControlEngine(self.controller, self.config)
        first = engine.step(
            snapshot(soil=(20, None, None)),
            now=NOW,
            watering_check_due=True,
        )
        self.assertEqual(first.watering_started_seconds, 10)
        self.assertFalse(engine.controller_state["watering_armed"])

        after_pulse = NOW + timedelta(seconds=11)
        blocked_by_hysteresis = engine.step(
            snapshot(soil=(20, None, None), timestamp=after_pulse),
            now=after_pulse,
            watering_check_due=True,
        )
        self.assertEqual(blocked_by_hysteresis.watering_started_seconds, 0)
        self.assertIn(
            "watering_blocked_hysteresis",
            blocked_by_hysteresis.requested.reasons,
        )

        wet_time = NOW + timedelta(seconds=60)
        engine.step(
            snapshot(soil=(45, None, None), timestamp=wet_time),
            now=wet_time,
        )
        self.assertTrue(engine.controller_state["watering_armed"])

        dry_during_cooldown = NOW + timedelta(seconds=120)
        blocked_by_cooldown = engine.step(
            snapshot(soil=(20, None, None), timestamp=dry_during_cooldown),
            now=dry_during_cooldown,
            watering_check_due=True,
        )
        self.assertIn(
            "watering_blocked_cooldown", blocked_by_cooldown.requested.reasons
        )

        after_cooldown = NOW + timedelta(seconds=3600)
        second = engine.step(
            snapshot(soil=(20, None, None), timestamp=after_cooldown),
            now=after_cooldown,
            watering_check_due=True,
        )
        self.assertEqual(second.watering_started_seconds, 10)

    def test_all_enabled_sensors_must_cross_rearm_threshold(self) -> None:
        self.config["soil_sensors"][1]["enabled"] = True
        disarmed = {
            "watering_armed": False,
            "last_observed_watering_at": NOW.isoformat(timespec="seconds"),
        }
        one_sensor_not_wet = self.controller.decide(
            snapshot(soil=(45, 44, None)),
            context(last_watering_at=NOW, controller_state=disarmed),
            self.config,
        )
        all_sensors_wet = self.controller.decide(
            snapshot(soil=(45, 45, None)),
            context(last_watering_at=NOW, controller_state=disarmed),
            self.config,
        )

        self.assertFalse(one_sensor_not_wet.controller_state["watering_armed"])
        self.assertTrue(all_sensors_wet.controller_state["watering_armed"])

    def test_controller_state_survives_engine_restart(self) -> None:
        first_engine = ControlEngine(self.controller, self.config)
        first_engine.step(
            snapshot(soil=(20, None, None)),
            now=NOW,
            watering_check_due=True,
        )
        after_pulse = NOW + timedelta(seconds=11)
        first_engine.step(
            snapshot(soil=(20, None, None), timestamp=after_pulse),
            now=after_pulse,
        )
        persisted = {"control_runtime": first_engine.export_state()}

        restored = ControlEngine(
            BaselineHysteresisController(), self.config
        )
        restored.restore(persisted)
        after_cooldown = NOW + timedelta(seconds=3601)
        result = restored.step(
            snapshot(soil=(20, None, None), timestamp=after_cooldown),
            now=after_cooldown,
            watering_check_due=True,
        )

        self.assertFalse(restored.controller_state["watering_armed"])
        self.assertEqual(result.watering_started_seconds, 0)
        self.assertIn("watering_blocked_hysteresis", result.requested.reasons)

    def test_fan_minimum_time_survives_engine_restart(self) -> None:
        first_engine = ControlEngine(self.controller, self.config)
        switched_on = first_engine.step(
            snapshot(30, 60),
            now=NOW,
        )
        self.assertTrue(switched_on.state.exhaust)
        persisted = {"control_runtime": first_engine.export_state()}

        restored = ControlEngine(
            BaselineHysteresisController(),
            self.config,
            initial_state=ActuatorState(exhaust=True, circulation=True),
        )
        restored.restore(persisted)
        after_60_seconds = NOW + timedelta(seconds=60)
        held = restored.step(
            snapshot(20, 30, timestamp=after_60_seconds),
            now=after_60_seconds,
        )

        self.assertTrue(held.state.exhaust)
        self.assertIn("exhaust_hold_on_minimum_time", held.requested.reasons)

    def test_manual_watering_also_disarms_hysteresis(self) -> None:
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

    def test_rejected_watering_request_does_not_disarm_controller(self) -> None:
        self.config["safety"]["max_daily_watering_seconds"] = 60
        engine = ControlEngine(self.controller, self.config)
        engine._watering_day = NOW.date().isoformat()
        engine.daily_watering_seconds = 60
        result = engine.step(
            snapshot(soil=(20, None, None)),
            now=NOW,
            watering_check_due=True,
        )

        self.assertEqual(result.requested.watering_seconds, 10)
        self.assertEqual(result.applied.watering_seconds, 0)
        self.assertNotIn("watering_armed", engine.controller_state)


if __name__ == "__main__":
    unittest.main()
