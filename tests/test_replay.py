from __future__ import annotations

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from greenhouse.config import DEFAULT_CONFIG
from greenhouse.importer import import_legacy_logs
from greenhouse.replay import run_replay


FIXTURE = Path(__file__).parent / "fixtures" / "replay_snapshots.csv"


class ReplayTests(unittest.TestCase):
    def test_adaptive_local_replay_uses_shared_output_contract(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as directory:
            results, summary = run_replay(
                FIXTURE,
                Path(directory),
                controller_id="adaptive_local",
                config=config,
                run_id="adaptive-local-test",
            )
            decisions = (Path(directory) / "decisions.csv").read_text(
                encoding="utf-8"
            )
            with (Path(directory) / "decisions.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                first_row = next(csv.DictReader(handle))

        self.assertEqual(len(results), 3)
        self.assertTrue(
            all(result.controller_id == "adaptive_local" for result in results)
        )
        self.assertEqual(summary["controller_id"], "adaptive_local")
        self.assertIn("adaptive-local-test,adaptive_local", decisions)
        self.assertFalse(json.loads(first_row["diagnostics"])["weather_used"])

    def test_replay_is_deterministic_and_reports_metrics(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first_path = Path(first_directory)
            second_path = Path(second_directory)
            _, first_summary = run_replay(
                FIXTURE,
                first_path,
                controller_id="legacy",
                config=config,
                run_id="test-run",
            )
            _, second_summary = run_replay(
                FIXTURE,
                second_path,
                controller_id="legacy",
                config=config,
                run_id="test-run",
            )
            self.assertEqual(
                (first_path / "decisions.csv").read_bytes(),
                (second_path / "decisions.csv").read_bytes(),
            )
            self.assertEqual(
                (first_path / "metrics.json").read_bytes(),
                (second_path / "metrics.json").read_bytes(),
            )

        metrics = first_summary["metrics"]
        self.assertEqual(metrics["cycles"], 3)
        self.assertEqual(metrics["observed_seconds"], 600)
        self.assertEqual(metrics["resources"]["watering_seconds"], 10)
        self.assertEqual(metrics["resources"]["estimated_water_ml"], 250)
        self.assertEqual(metrics["resources"]["exhaust_switches"], 2)

    def test_importer_matches_nearest_sensor_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            climate = directory_path / "climate.csv"
            sensors = directory_path / "sensors.csv"
            climate.write_text(
                "timestamp,temperature_c,humidity_percent\n"
                "2026-07-24T12:00:00,25,55\n",
                encoding="utf-8",
            )
            sensors.write_text(
                "timestamp,soil1_percent,soil2_percent,soil3_percent,light_percent\n"
                "2026-07-24T12:00:30,42,,,75\n",
                encoding="utf-8",
            )
            imported = import_legacy_logs(climate, sensors)

        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0].soil_moisture_percent[0], 42)
        self.assertEqual(imported[0].light_percent, 75)


if __name__ == "__main__":
    unittest.main()
