from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from greenhouse.models import SensorSnapshot, parse_datetime
from greenhouse.records import write_snapshots


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("*C", "").replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _timestamp(row: dict[str, Any]) -> datetime | None:
    raw = row.get("timestamp")
    if raw:
        parsed = parse_datetime(str(raw))
        if parsed is not None:
            return parsed
    if row.get("Date") and row.get("Time"):
        raw = f"{row['Date']} {row['Time']}"
        for pattern in ("%m/%d/%y %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(raw, pattern)
            except ValueError:
                continue
    return None


def _read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def import_legacy_logs(
    climate_path: Path,
    sensor_path: Path,
    *,
    tolerance_seconds: float = 120.0,
) -> list[SensorSnapshot]:
    sensor_rows = [
        (timestamp, row)
        for row in _read_rows(sensor_path)
        if (timestamp := _timestamp(row)) is not None
    ]
    snapshots: list[SensorSnapshot] = []

    for climate in _read_rows(climate_path):
        timestamp = _timestamp(climate)
        if timestamp is None:
            continue
        temperature = _float(
            climate.get("temperature_c", climate.get("Temperature"))
        )
        humidity = _float(
            climate.get("humidity_percent", climate.get("Humidity"))
        )
        nearest = None
        if sensor_rows:
            candidate_timestamp, candidate = min(
                sensor_rows,
                key=lambda item: abs((item[0] - timestamp).total_seconds()),
            )
            if abs((candidate_timestamp - timestamp).total_seconds()) <= tolerance_seconds:
                nearest = candidate

        soil = tuple(
            _float(nearest.get(f"soil{index}_percent")) if nearest else None
            for index in (1, 2, 3)
        )
        light = _float(nearest.get("light_percent")) if nearest else None
        issues: tuple[str, ...] = () if nearest else ("sensor_log_not_matched",)
        snapshots.append(
            SensorSnapshot(
                timestamp=timestamp,
                temperature_c=temperature,
                humidity_percent=humidity,
                soil_moisture_percent=soil,
                light_percent=light,
                quality="ok" if temperature is not None and humidity is not None else "invalid",
                issues=issues,
            ).validated()
        )
    return snapshots


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bestehende Klima- und Sensorlogs in Snapshot-CSV überführen"
    )
    parser.add_argument("--climate", type=Path, required=True)
    parser.add_argument("--sensors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tolerance-seconds", type=float, default=120.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    snapshots = import_legacy_logs(
        args.climate,
        args.sensors,
        tolerance_seconds=args.tolerance_seconds,
    )
    write_snapshots(args.output, snapshots)


if __name__ == "__main__":
    main()
