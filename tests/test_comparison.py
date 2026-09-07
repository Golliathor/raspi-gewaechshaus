from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta

from greenhouse.config import DEFAULT_CONFIG, validate_config
from greenhouse.controllers import create_controller, registered_controller_ids
from greenhouse.models import ActuatorState, ControlContext, SensorSnapshot
from greenhouse.runtime import ControlEngine


NOW = datetime(2026, 7, 24, 12)
MODEL_IDS = (
    "baseline_fixed",
    "baseline_hysteresis",
    "adaptive_local",
    "adaptive_weather",
)


class ComparisonControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.snapshot = SensorSnapshot(
            timestamp=NOW,
            temperature_c=29,
            humidity_percent=55,
            soil_moisture_percent=(50, None, None),
            light_percent=60,
        )

    def test_all_four_models_are_registered_and_runnable(self) -> None:
        registered = registered_controller_ids()
        for controller_id in MODEL_IDS:
            with self.subTest(controller_id=controller_id):
                self.assertIn(controller_id, registered)
                engine = ControlEngine(
                    create_controller(controller_id),
                    self.config,
                )
                result = engine.step(self.snapshot, now=NOW)
                self.assertEqual(result.controller_id, controller_id)

    def test_fixed_and_hysteresis_baselines_keep_distinct_behavior(self) -> None:
        fixed = create_controller("baseline_fixed").decide(
            SensorSnapshot(NOW, 26, 45, (50, None, None), 50),
            ControlContext(ActuatorState(exhaust=True)),
            self.config,
        )
        hysteresis = create_controller("baseline_hysteresis").decide(
            SensorSnapshot(NOW, 26, 45, (50, None, None), 50),
            ControlContext(
                ActuatorState(exhaust=True),
                last_transition_at={
                    "exhaust": NOW - timedelta(seconds=30),
                },
                now=NOW,
            ),
            self.config,
        )

        self.assertFalse(fixed.exhaust)
        self.assertTrue(hysteresis.exhaust)
        self.assertIn("exhaust_hold_on_hysteresis", hysteresis.reasons)

    def test_baseline_configuration_is_validated_independently(self) -> None:
        self.config["controllers"]["baseline_fixed"][
            "soil_moisture_threshold_percent"
        ] = 101
        self.config["controllers"]["baseline_hysteresis"][
            "exhaust_temperature_off_c"
        ] = 30
        errors = validate_config(self.config)

        self.assertTrue(
            any("baseline_fixed.soil_moisture_threshold_percent" in error for error in errors)
        )
        self.assertTrue(
            any("baseline_hysteresis" in error for error in errors)
        )

    def test_hysteresis_baseline_repeats_dry_pulse_after_cooldown(self) -> None:
        controller_config = self.config["controllers"]["baseline_hysteresis"]
        controller_config["watering_seconds"] = 10
        controller_config["watering_cooldown_seconds"] = 60
        engine = ControlEngine(
            create_controller("baseline_hysteresis"), self.config
        )
        dry = SensorSnapshot(NOW, 20, 40, (20, None, None), 0)

        first = engine.step(dry, now=NOW, watering_check_due=True)
        during = engine.step(
            SensorSnapshot(
                NOW + timedelta(seconds=11), 20, 40, (20, None, None), 0
            ),
            now=NOW + timedelta(seconds=11),
            watering_check_due=True,
        )
        after = engine.step(
            SensorSnapshot(
                NOW + timedelta(seconds=71), 20, 40, (20, None, None), 0
            ),
            now=NOW + timedelta(seconds=71),
            watering_check_due=True,
        )

        self.assertEqual(first.watering_started_seconds, 10)
        self.assertEqual(during.watering_started_seconds, 0)
        self.assertIn("watering_blocked_cooldown", during.requested.reasons)
        self.assertEqual(after.watering_started_seconds, 10)


if __name__ == "__main__":
    unittest.main()
