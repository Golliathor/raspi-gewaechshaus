from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from greenhouse.calibration import raw_to_light_percent, raw_to_percent
from greenhouse.config import DEFAULT_CONFIG, load_config, validate_config
from greenhouse.models import SensorSnapshot


class ConfigTests(unittest.TestCase):
    def test_nested_defaults_are_merged_without_sharing_mutable_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "greenhouse_name": "Testhaus",
                        "light_sensor": {"channel": 2},
                        "soil_sensors": [{"name": "Testbeet"}],
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(path)

        self.assertEqual(config["greenhouse_name"], "Testhaus")
        self.assertEqual(config["light_sensor"]["channel"], 2)
        self.assertTrue(config["light_sensor"]["enabled"])
        self.assertEqual(config["soil_sensors"][0]["name"], "Testbeet")
        self.assertEqual(config["soil_sensors"][1]["name"], "Sensor 2")

    def test_default_config_is_valid(self) -> None:
        self.assertEqual(validate_config(DEFAULT_CONFIG), [])

    def test_invalid_safety_and_target_ranges_are_reported(self) -> None:
        config = load_config(Path("/does/not/exist"))
        config["safety"]["sensor_stale_after_seconds"] = 500
        config["safety"]["safe_state_after_seconds"] = 100
        config["targets"]["temperature_min_c"] = 30
        config["targets"]["temperature_max_c"] = 20
        errors = validate_config(config)
        self.assertTrue(any("safe_state_after_seconds" in error for error in errors))
        self.assertTrue(any("temperature_min_c" in error for error in errors))

    def test_invalid_adaptive_parameters_and_controller_are_reported(self) -> None:
        config = load_config(Path("/does/not/exist"))
        adaptive = config["controllers"]["adaptive_local"]
        adaptive["trend_minimum_span_seconds"] = 1000
        adaptive["trend_window_seconds"] = 500
        adaptive["watering_min_seconds"] = 20
        adaptive["watering_base_seconds"] = 10
        adaptive["max_soil_threshold_increase_percent"] = 15
        adaptive["max_temperature_reduction_c"] = 5
        config["controller"]["active"] = "not_registered"
        errors = validate_config(config)
        self.assertTrue(any("trend_minimum_span_seconds" in error for error in errors))
        self.assertTrue(any("watering_min_seconds" in error for error in errors))
        self.assertTrue(any("adaptive Bodenfeuchte" in error for error in errors))
        self.assertTrue(any("Temperaturhysterese" in error for error in errors))
        self.assertTrue(any("controller.active ist unbekannt" in error for error in errors))


class CalibrationAndSnapshotTests(unittest.TestCase):
    def test_calibration_clamps_and_handles_invalid_range(self) -> None:
        self.assertEqual(raw_to_percent(19000, 26000, 12000), 50.0)
        self.assertEqual(raw_to_percent(30000, 26000, 12000), 0.0)
        self.assertEqual(raw_to_light_percent(2000, 26000, 2000), 100.0)
        self.assertIsNone(raw_to_percent(100, 100, 100))

    def test_snapshot_validation_removes_out_of_range_values(self) -> None:
        snapshot = SensorSnapshot(
            timestamp=datetime(2026, 7, 24, 12),
            temperature_c=120,
            humidity_percent=55,
            soil_moisture_percent=(-1, 50, 101),
            light_percent=70,
        ).validated()
        self.assertIsNone(snapshot.temperature_c)
        self.assertEqual(snapshot.soil_moisture_percent, (None, 50.0, None))
        self.assertEqual(snapshot.quality, "invalid")
        self.assertIn("temperature_out_of_range", snapshot.issues)


if __name__ == "__main__":
    unittest.main()
