from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from greenhouse.models import (
    CycleResult,
    SensorSnapshot,
    WeatherSnapshot,
    parse_datetime,
    soil_values,
)


SNAPSHOT_FIELDS = [
    "timestamp",
    "temperature_c",
    "humidity_percent",
    "soil1_percent",
    "soil2_percent",
    "soil3_percent",
    "light_percent",
    "quality",
    "issues",
    "weather_timestamp",
    "outside_temperature_c",
    "outside_humidity_percent",
    "precipitation_mm",
    "precipitation_probability_percent",
    "weather_provider",
]

DECISION_FIELDS = [
    "timestamp",
    "run_id",
    "controller_id",
    "snapshot_timestamp",
    "temperature_c",
    "humidity_percent",
    "soil1_percent",
    "soil2_percent",
    "soil3_percent",
    "light_percent",
    "weather_available",
    "requested_exhaust",
    "requested_circulation",
    "requested_watering_seconds",
    "applied_exhaust",
    "applied_circulation",
    "applied_watering_seconds",
    "water_valve",
    "watering_started_seconds",
    "reasons",
    "diagnostics",
    "safety_overrides",
    "transitions",
]


def _serialize_float(value: float | None) -> str:
    return "" if value is None else str(value)


def snapshot_to_row(snapshot: SensorSnapshot) -> dict[str, Any]:
    soil = list(snapshot.soil_moisture_percent[:3])
    soil.extend([None] * (3 - len(soil)))
    weather = snapshot.weather
    return {
        "timestamp": snapshot.timestamp.isoformat(timespec="seconds"),
        "temperature_c": _serialize_float(snapshot.temperature_c),
        "humidity_percent": _serialize_float(snapshot.humidity_percent),
        "soil1_percent": _serialize_float(soil[0]),
        "soil2_percent": _serialize_float(soil[1]),
        "soil3_percent": _serialize_float(soil[2]),
        "light_percent": _serialize_float(snapshot.light_percent),
        "quality": snapshot.quality,
        "issues": "|".join(snapshot.issues),
        "weather_timestamp": (
            weather.timestamp.isoformat(timespec="seconds") if weather else ""
        ),
        "outside_temperature_c": _serialize_float(
            weather.outside_temperature_c if weather else None
        ),
        "outside_humidity_percent": _serialize_float(
            weather.outside_humidity_percent if weather else None
        ),
        "precipitation_mm": _serialize_float(
            weather.precipitation_mm if weather else None
        ),
        "precipitation_probability_percent": _serialize_float(
            weather.precipitation_probability_percent if weather else None
        ),
        "weather_provider": weather.provider if weather and weather.provider else "",
    }


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def snapshot_from_row(row: dict[str, Any]) -> SensorSnapshot:
    timestamp = parse_datetime(str(row["timestamp"]))
    if timestamp is None:
        raise ValueError(f"Ungültiger Snapshot-Zeitstempel: {row.get('timestamp')!r}")
    weather_timestamp = row.get("weather_timestamp")
    weather = None
    if weather_timestamp:
        parsed_weather_timestamp = parse_datetime(str(weather_timestamp))
        if parsed_weather_timestamp is None:
            raise ValueError(
                f"Ungültiger Wetter-Zeitstempel: {weather_timestamp!r}"
            )
        weather = WeatherSnapshot(
            timestamp=parsed_weather_timestamp,
            outside_temperature_c=_parse_float(row.get("outside_temperature_c")),
            outside_humidity_percent=_parse_float(
                row.get("outside_humidity_percent")
            ),
            precipitation_mm=_parse_float(row.get("precipitation_mm")),
            precipitation_probability_percent=_parse_float(
                row.get("precipitation_probability_percent")
            ),
            provider=row.get("weather_provider") or None,
        )
    return SensorSnapshot(
        timestamp=timestamp,
        temperature_c=_parse_float(row.get("temperature_c")),
        humidity_percent=_parse_float(row.get("humidity_percent")),
        soil_moisture_percent=soil_values(
            (
                row.get("soil1_percent"),
                row.get("soil2_percent"),
                row.get("soil3_percent"),
            )
        ),
        light_percent=_parse_float(row.get("light_percent")),
        quality=row.get("quality") or "ok",
        issues=tuple(filter(None, str(row.get("issues", "")).split("|"))),
        weather=weather,
    )


def append_csv(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def write_snapshots(path: Path, snapshots: Iterable[SensorSnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_FIELDS)
        writer.writeheader()
        for snapshot in snapshots:
            writer.writerow(snapshot_to_row(snapshot))


def read_snapshots(path: Path) -> Iterator[SensorSnapshot]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from (snapshot_from_row(row) for row in csv.DictReader(handle))


def decision_to_row(result: CycleResult, run_id: str) -> dict[str, Any]:
    soil = list(result.snapshot.soil_moisture_percent[:3])
    soil.extend([None] * (3 - len(soil)))
    return {
        "timestamp": result.timestamp.isoformat(timespec="seconds"),
        "run_id": run_id,
        "controller_id": result.controller_id,
        "snapshot_timestamp": result.snapshot.timestamp.isoformat(timespec="seconds"),
        "temperature_c": _serialize_float(result.snapshot.temperature_c),
        "humidity_percent": _serialize_float(result.snapshot.humidity_percent),
        "soil1_percent": _serialize_float(soil[0]),
        "soil2_percent": _serialize_float(soil[1]),
        "soil3_percent": _serialize_float(soil[2]),
        "light_percent": _serialize_float(result.snapshot.light_percent),
        "weather_available": int(result.snapshot.weather is not None),
        "requested_exhaust": int(result.requested.exhaust),
        "requested_circulation": int(result.requested.circulation),
        "requested_watering_seconds": result.requested.watering_seconds,
        "applied_exhaust": int(result.applied.exhaust),
        "applied_circulation": int(result.applied.circulation),
        "applied_watering_seconds": result.applied.watering_seconds,
        "water_valve": int(result.state.water_valve),
        "watering_started_seconds": result.watering_started_seconds,
        "reasons": "|".join(result.requested.reasons),
        "diagnostics": json.dumps(
            result.requested.diagnostics,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        "safety_overrides": "|".join(result.safety_overrides),
        "transitions": "|".join(result.transitions),
    }


def append_snapshot(path: Path, snapshot: SensorSnapshot) -> None:
    append_csv(path, SNAPSHOT_FIELDS, snapshot_to_row(snapshot))


def append_decision(path: Path, result: CycleResult, run_id: str) -> None:
    append_csv(path, DECISION_FIELDS, decision_to_row(result, run_id))
