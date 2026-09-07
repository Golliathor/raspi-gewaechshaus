from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import klima_logger
from greenhouse.config import ProjectPaths
from greenhouse.logtail import tail_csv_source
from web import automation_daemon


class LatestClimateTests(unittest.TestCase):
    def test_logger_keeps_csv_and_atomically_updates_latest_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            klima_logger.ensure_climate_csv(paths.climate_csv_path)
            klima_logger.append_climate_csv(
                paths.climate_csv_path,
                "2026-09-08T00:45:00",
                23.44,
                91.24,
            )
            klima_logger.write_latest_climate(
                paths.latest_climate_path,
                "2026-09-08T00:45:00",
                23.44,
                91.24,
            )

            snapshot = json.loads(
                paths.latest_climate_path.read_text(encoding="utf-8")
            )
            with paths.climate_csv_path.open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(
                snapshot,
                {
                    "timestamp": "2026-09-08T00:45:00",
                    "temperature_c": 23.4,
                    "humidity_percent": 91.2,
                },
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["temperature_c"], "23.4")
            self.assertFalse(
                paths.latest_climate_path.with_suffix(".json.tmp").exists()
            )

    def test_daemon_reads_json_without_touching_historical_climate_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            klima_logger.write_latest_climate(
                paths.latest_climate_path,
                "2026-09-08T00:45:00",
                23.4,
                91.2,
            )
            paths.climate_csv_path.parent.mkdir(parents=True, exist_ok=True)
            paths.climate_csv_path.write_text(
                "timestamp,temperature_c,humidity_percent\n"
                "2000-01-01T00:00:00,1,2\n",
                encoding="utf-8",
            )
            original_open = Path.open

            def guarded_open(path, *args, **kwargs):
                if path == paths.climate_csv_path:
                    raise AssertionError("historical CSV must not be opened")
                return original_open(path, *args, **kwargs)

            with (
                patch.object(automation_daemon, "PATHS", paths),
                patch.object(Path, "open", guarded_open),
            ):
                climate = automation_daemon.read_latest_climate()

        self.assertEqual(climate["timestamp"], datetime(2026, 9, 8, 0, 45))
        self.assertEqual(climate["temperature_c"], 23.4)
        self.assertEqual(climate["humidity_percent"], 91.2)

    def test_daemon_retains_state_value_until_first_new_snapshot(self) -> None:
        fallback = {
            "timestamp": "2026-09-08T00:44:00",
            "temperature_c": 22.0,
            "humidity_percent": 80.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            with patch.object(automation_daemon, "PATHS", paths):
                climate = automation_daemon.read_latest_climate(fallback)

        self.assertEqual(climate["timestamp"], datetime(2026, 9, 8, 0, 44))
        self.assertEqual(climate["temperature_c"], 22.0)


class CsvTailTests(unittest.TestCase):
    def test_tail_reads_only_requested_rows_from_large_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timestamp", "value", "note"])
                for index in range(50_000):
                    writer.writerow(
                        [
                            f"2026-09-08T00:{index % 60:02d}:00",
                            index,
                            "value,with,commas",
                        ]
                    )

            bytes_returned = 0
            original_open = Path.open

            class CountingHandle:
                def __init__(self, handle):
                    self.handle = handle

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.handle.close()

                def __getattr__(self, name):
                    return getattr(self.handle, name)

                def read(self, size=-1):
                    nonlocal bytes_returned
                    data = self.handle.read(size)
                    bytes_returned += len(data)
                    return data

                def readline(self, size=-1):
                    nonlocal bytes_returned
                    data = self.handle.readline(size)
                    bytes_returned += len(data)
                    return data

            def counting_open(file_path, *args, **kwargs):
                handle = original_open(file_path, *args, **kwargs)
                return (
                    CountingHandle(handle)
                    if file_path == path and args and args[0] == "rb"
                    else handle
                )

            file_size = path.stat().st_size
            with patch.object(Path, "open", counting_open):
                source = tail_csv_source(path, 3)
            rows = list(csv.DictReader(source))

        self.assertEqual([int(row["value"]) for row in rows], [49997, 49998, 49999])
        self.assertEqual(rows[-1]["note"], "value,with,commas")
        self.assertLess(bytes_returned, file_size // 10)

    def test_tail_handles_header_only_and_oversized_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "small.csv"
            path.write_text("name,value\na,1\nb,2\n", encoding="utf-8")
            rows = list(csv.DictReader(tail_csv_source(path, 100)))

        self.assertEqual(rows, [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}])


if __name__ == "__main__":
    unittest.main()
