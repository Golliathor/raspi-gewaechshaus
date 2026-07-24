from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from greenhouse.models import (
    ControlContext,
    ControlDecision,
    SafetyResult,
    SensorSnapshot,
)


class SafetyLayer:
    def apply(
        self,
        decision: ControlDecision,
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
        now: datetime,
    ) -> SafetyResult:
        safety_config = config.get("safety", {})
        overrides: list[str] = []
        exhaust = decision.exhaust
        circulation = decision.circulation
        watering_seconds = max(0.0, float(decision.watering_seconds))

        maximum_pulse = max(
            0.0, float(safety_config.get("max_watering_pulse_seconds", 60))
        )
        if watering_seconds > maximum_pulse:
            watering_seconds = maximum_pulse
            overrides.append("watering_pulse_limited")

        daily_limit = max(
            0.0, float(safety_config.get("max_daily_watering_seconds", 180))
        )
        daily_remaining = max(0.0, daily_limit - context.daily_watering_seconds)
        if watering_seconds > daily_remaining:
            watering_seconds = daily_remaining
            overrides.append("daily_watering_limit")

        enabled_soil_indices = [
            index
            for index, sensor in enumerate(config.get("soil_sensors", [])[:3])
            if sensor.get("enabled", False)
        ]
        has_valid_enabled_soil = any(
            index < len(snapshot.soil_moisture_percent)
            and snapshot.soil_moisture_percent[index] is not None
            for index in enabled_soil_indices
        )
        if watering_seconds > 0 and not has_valid_enabled_soil:
            watering_seconds = 0.0
            overrides.append("watering_blocked_no_valid_soil")

        age_seconds = max(0.0, (now - snapshot.timestamp).total_seconds())
        stale_after = float(safety_config.get("sensor_stale_after_seconds", 180))
        safe_after = float(safety_config.get("safe_state_after_seconds", 600))
        if age_seconds > stale_after and watering_seconds > 0:
            watering_seconds = 0.0
            overrides.append("watering_blocked_stale_snapshot")

        if not snapshot.has_valid_air:
            if exhaust or circulation:
                overrides.append("fans_off_invalid_air")
            exhaust = False
            circulation = False
        elif age_seconds > safe_after:
            if exhaust or circulation:
                overrides.append("fans_off_stale_snapshot")
            exhaust = False
            circulation = False

        applied = decision.with_changes(
            exhaust=exhaust,
            circulation=circulation,
            watering_seconds=watering_seconds,
        )
        return SafetyResult(applied, tuple(overrides))
