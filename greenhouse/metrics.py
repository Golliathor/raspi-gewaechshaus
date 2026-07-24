from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from greenhouse.models import CycleResult


@dataclass
class _Accumulator:
    observed_seconds: float = 0.0
    temperature_in_target_seconds: float = 0.0
    humidity_in_target_seconds: float = 0.0
    soil_in_target_seconds: float = 0.0
    temperature_deviation_degree_minutes: float = 0.0
    humidity_deviation_percent_minutes: float = 0.0
    soil_deficit_percent_minutes: float = 0.0
    exhaust_runtime_seconds: float = 0.0
    circulation_runtime_seconds: float = 0.0
    exhaust_switches: int = 0
    circulation_switches: int = 0
    watering_seconds: float = 0.0
    invalid_snapshots: int = 0
    safety_overrides: int = 0


def _outside_deviation(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum - value
    if value > maximum:
        return value - maximum
    return 0.0


def calculate_metrics(
    results: Iterable[CycleResult],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    rows = list(results)
    accumulator = _Accumulator()
    targets = config.get("targets", {})
    temp_min = float(targets.get("temperature_min_c", 18.0))
    temp_max = float(targets.get("temperature_max_c", 28.0))
    humidity_min = float(targets.get("humidity_min_percent", 40.0))
    humidity_max = float(targets.get("humidity_max_percent", 70.0))
    soil_min = float(targets.get("soil_moisture_min_percent", 35.0))
    soil_max = float(targets.get("soil_moisture_max_percent", 70.0))

    for index, result in enumerate(rows):
        if result.snapshot.quality != "ok":
            accumulator.invalid_snapshots += 1
        accumulator.safety_overrides += len(result.safety_overrides)
        accumulator.watering_seconds += result.watering_started_seconds
        accumulator.exhaust_switches += sum(
            transition.startswith("exhaust:") for transition in result.transitions
        )
        accumulator.circulation_switches += sum(
            transition.startswith("circulation:") for transition in result.transitions
        )
        if index + 1 >= len(rows):
            continue

        seconds = max(
            0.0,
            (rows[index + 1].timestamp - result.timestamp).total_seconds(),
        )
        accumulator.observed_seconds += seconds
        if result.state.exhaust:
            accumulator.exhaust_runtime_seconds += seconds
        if result.state.circulation:
            accumulator.circulation_runtime_seconds += seconds

        snapshot = result.snapshot
        if snapshot.temperature_c is not None:
            deviation = _outside_deviation(snapshot.temperature_c, temp_min, temp_max)
            accumulator.temperature_deviation_degree_minutes += deviation * seconds / 60
            if deviation == 0:
                accumulator.temperature_in_target_seconds += seconds
        if snapshot.humidity_percent is not None:
            deviation = _outside_deviation(
                snapshot.humidity_percent, humidity_min, humidity_max
            )
            accumulator.humidity_deviation_percent_minutes += deviation * seconds / 60
            if deviation == 0:
                accumulator.humidity_in_target_seconds += seconds
        valid_soil = snapshot.valid_soil_values
        if valid_soil:
            mean_soil = sum(valid_soil) / len(valid_soil)
            deficit = max(0.0, soil_min - mean_soil)
            accumulator.soil_deficit_percent_minutes += deficit * seconds / 60
            if soil_min <= mean_soil <= soil_max:
                accumulator.soil_in_target_seconds += seconds

    flow = float(config.get("water_flow_ml_per_second", 25.0))

    def share(seconds: float) -> float | None:
        if accumulator.observed_seconds <= 0:
            return None
        return round(seconds / accumulator.observed_seconds * 100.0, 3)

    return {
        "cycles": len(rows),
        "observed_seconds": round(accumulator.observed_seconds, 3),
        "target_ranges": {
            "temperature_c": [temp_min, temp_max],
            "humidity_percent": [humidity_min, humidity_max],
            "soil_moisture_percent": [soil_min, soil_max],
        },
        "climate": {
            "temperature_in_target_percent": share(
                accumulator.temperature_in_target_seconds
            ),
            "humidity_in_target_percent": share(
                accumulator.humidity_in_target_seconds
            ),
            "soil_in_target_percent": share(accumulator.soil_in_target_seconds),
            "temperature_deviation_degree_minutes": round(
                accumulator.temperature_deviation_degree_minutes, 3
            ),
            "humidity_deviation_percent_minutes": round(
                accumulator.humidity_deviation_percent_minutes, 3
            ),
            "soil_deficit_percent_minutes": round(
                accumulator.soil_deficit_percent_minutes, 3
            ),
        },
        "resources": {
            "watering_seconds": round(accumulator.watering_seconds, 3),
            "estimated_water_ml": round(accumulator.watering_seconds * flow, 3),
            "exhaust_runtime_seconds": round(
                accumulator.exhaust_runtime_seconds, 3
            ),
            "circulation_runtime_seconds": round(
                accumulator.circulation_runtime_seconds, 3
            ),
            "exhaust_switches": accumulator.exhaust_switches,
            "circulation_switches": accumulator.circulation_switches,
        },
        "quality": {
            "invalid_snapshots": accumulator.invalid_snapshots,
            "safety_overrides": accumulator.safety_overrides,
        },
    }
