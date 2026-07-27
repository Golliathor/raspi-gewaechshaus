from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from greenhouse.config import DEFAULT_CONFIG, ProjectPaths
from greenhouse.hardware import ADCBatchReading, ADS1115Reader
from web import automation_daemon
from web.automation_daemon import (
    LocalSensorReader,
    build_snapshot,
    restorable_soil_states,
)


NOW = datetime(2026, 7, 27, 12)


class FakeBatchADC:
    def __init__(self, readings: list[ADCBatchReading]) -> None:
        self.readings = iter(readings)
        self.calls: list[tuple[int, int, float]] = []

    def read_channel_batch(
        self,
        channel: int,
        *,
        sample_count: int,
        sample_interval_seconds: float,
    ) -> ADCBatchReading:
        self.calls.append((channel, sample_count, sample_interval_seconds))
        return next(self.readings)


def sensor_config() -> dict[str, object]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["adc_sample_interval_ms"] = 0
    config["light_sensor"]["enabled"] = False
    config["soil_sensors"][1]["enabled"] = False
    config["soil_sensors"][2]["enabled"] = False
    return config


class ADCBatchTests(unittest.TestCase):
    def test_median_rejects_single_outlier_and_reports_span(self) -> None:
        reader = ADS1115Reader()
        samples = iter((1000, 1002, None, 999, 30000, 1001, 998, 1000, 1003))
        reader.read_channel = lambda channel: next(samples)  # type: ignore[method-assign]

        reading = reader.read_channel_batch(
            0,
            sample_count=9,
            sample_interval_seconds=0,
        )

        self.assertEqual(reading.value, 1000)
        self.assertEqual(reading.minimum, 998)
        self.assertEqual(reading.maximum, 30000)
        self.assertEqual(reading.span, 29002)
        self.assertEqual(reading.valid_samples, 8)
        self.assertEqual(reading.requested_samples, 9)

    def test_batch_is_invalid_when_fewer_than_half_the_samples_succeed(self) -> None:
        reader = ADS1115Reader()
        samples = iter((1000, None, None, 1001, None, None, 999, None, 1002))
        reader.read_channel = lambda channel: next(samples)  # type: ignore[method-assign]

        reading = reader.read_channel_batch(
            0,
            sample_count=9,
            sample_interval_seconds=0,
        )

        self.assertIsNone(reading.value)
        self.assertEqual(reading.valid_samples, 4)


class SoilFilterTests(unittest.TestCase):
    def test_median_is_calibrated_then_smoothed_and_used_by_snapshot(self) -> None:
        config = sensor_config()
        adc = FakeBatchADC(
            [
                ADCBatchReading(19000, 18800, 19200, 9, 9),
                ADCBatchReading(26000, 25800, 26200, 9, 9),
            ]
        )
        reader = LocalSensorReader(initial_adc_address=72)
        reader._adc = adc  # type: ignore[assignment]

        first, _ = reader.read(config)  # type: ignore[arg-type]
        second, light = reader.read(config)  # type: ignore[arg-type]
        snapshot = build_snapshot(
            NOW,
            {
                "timestamp": NOW,
                "temperature_c": 25,
                "humidity_percent": 50,
            },
            second,
            light,
            NOW,
        )

        self.assertEqual(first[0]["unfiltered_moisture_percent"], 50)
        self.assertEqual(first[0]["moisture_percent"], 50)
        self.assertEqual(second[0]["unfiltered_moisture_percent"], 0)
        self.assertEqual(second[0]["moisture_percent"], 40)
        self.assertEqual(second[0]["raw_span"], 400)
        self.assertEqual(snapshot.soil_moisture_percent[0], 40)
        self.assertEqual(adc.calls[0], (0, 9, 0.0))

    def test_invalid_batch_is_not_replaced_by_stale_filtered_value(self) -> None:
        config = sensor_config()
        adc = FakeBatchADC(
            [
                ADCBatchReading(19000, 18800, 19200, 9, 9),
                ADCBatchReading(None, 18000, 28000, 4, 9),
            ]
        )
        reader = LocalSensorReader(initial_adc_address=72)
        reader._adc = adc  # type: ignore[assignment]

        reader.read(config)  # type: ignore[arg-type]
        second, _ = reader.read(config)  # type: ignore[arg-type]

        self.assertIsNone(second[0]["unfiltered_moisture_percent"])
        self.assertIsNone(second[0]["moisture_percent"])

    def test_filter_state_survives_daemon_restart(self) -> None:
        config = sensor_config()
        previous_state = [
            {
                **config["soil_sensors"][0],
                "index": 0,
                "moisture_percent": 40.0,
            }
        ]
        adc = FakeBatchADC(
            [ADCBatchReading(26000, 25900, 26100, 9, 9)]
        )
        reader = LocalSensorReader(
            previous_state,
            initial_adc_address=72,
        )
        reader._adc = adc  # type: ignore[assignment]

        sensors, _ = reader.read(config)  # type: ignore[arg-type]

        self.assertEqual(sensors[0]["unfiltered_moisture_percent"], 0)
        self.assertEqual(sensors[0]["moisture_percent"], 32)

    def test_only_recent_filter_state_is_restored(self) -> None:
        config = sensor_config()
        soil_states = [{"index": 0, "enabled": True, "moisture_percent": 40}]
        fresh = {
            "last_sensor_update": (NOW - timedelta(seconds=60)).isoformat(),
            "soil_sensors": soil_states,
        }
        stale = {
            "last_sensor_update": (NOW - timedelta(minutes=10)).isoformat(),
            "soil_sensors": soil_states,
        }

        self.assertEqual(
            restorable_soil_states(fresh, config, NOW),  # type: ignore[arg-type]
            soil_states,
        )
        self.assertEqual(
            restorable_soil_states(stale, config, NOW),  # type: ignore[arg-type]
            [],
        )

    def test_sensor_csv_keeps_median_raw_and_filtered_percent_separate(self) -> None:
        sensors = [
            {
                "raw_value": 26000,
                "moisture_percent": 40.0,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            with patch.object(automation_daemon, "PATHS", paths):
                automation_daemon.append_sensor_log(
                    NOW,
                    sensors,
                    {
                        "raw_value": 1000,
                        "light_percent": 80,
                        "light_class": "hell",
                    },
                )
            with paths.sensor_csv_path.open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                row = next(csv.DictReader(handle))

        self.assertEqual(row["soil1_raw"], "26000")
        self.assertEqual(row["soil1_percent"], "40.0")


if __name__ == "__main__":
    unittest.main()
