from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Sequence


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except ValueError:
        return None


@dataclass(frozen=True)
class WeatherSnapshot:
    timestamp: datetime
    outside_temperature_c: float | None = None
    outside_humidity_percent: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability_percent: float | None = None
    provider: str | None = None


@dataclass(frozen=True)
class SensorSnapshot:
    timestamp: datetime
    temperature_c: float | None
    humidity_percent: float | None
    soil_moisture_percent: tuple[float | None, ...] = ()
    light_percent: float | None = None
    quality: str = "ok"
    issues: tuple[str, ...] = ()
    weather: WeatherSnapshot | None = None

    @property
    def has_valid_air(self) -> bool:
        return self.temperature_c is not None and self.humidity_percent is not None

    @property
    def valid_soil_values(self) -> tuple[float, ...]:
        return tuple(value for value in self.soil_moisture_percent if value is not None)

    def validated(self) -> "SensorSnapshot":
        issues = list(self.issues)

        def check_range(name: str, value: float | None, low: float, high: float) -> float | None:
            if value is None:
                return None
            if low <= value <= high:
                return float(value)
            issues.append(f"{name}_out_of_range")
            return None

        temperature = check_range("temperature", self.temperature_c, -40.0, 85.0)
        humidity = check_range("humidity", self.humidity_percent, 0.0, 100.0)
        light = check_range("light", self.light_percent, 0.0, 100.0)
        soil = tuple(
            check_range(f"soil_{index + 1}", value, 0.0, 100.0)
            for index, value in enumerate(self.soil_moisture_percent[:3])
        )
        quality = "ok" if not issues and temperature is not None and humidity is not None else "invalid"
        return replace(
            self,
            temperature_c=temperature,
            humidity_percent=humidity,
            soil_moisture_percent=soil,
            light_percent=light,
            quality=quality,
            issues=tuple(dict.fromkeys(issues)),
        )


@dataclass(frozen=True)
class ActuatorState:
    exhaust: bool = False
    circulation: bool = False
    water_valve: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "exhaust": self.exhaust,
            "circulation": self.circulation,
            "water_valve": self.water_valve,
        }


@dataclass(frozen=True)
class ControlContext:
    actuator_state: ActuatorState
    history: tuple[SensorSnapshot, ...] = ()
    last_transition_at: Mapping[str, datetime] = field(default_factory=dict)
    last_watering_at: datetime | None = None
    last_fallback_watering_date: str | None = None
    daily_watering_seconds: float = 0.0
    watering_check_due: bool = False


@dataclass(frozen=True)
class ControlDecision:
    exhaust: bool
    circulation: bool
    watering_seconds: float = 0.0
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def with_changes(self, **changes: Any) -> "ControlDecision":
        return replace(self, **changes)


@dataclass(frozen=True)
class SafetyResult:
    decision: ControlDecision
    overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class CycleResult:
    timestamp: datetime
    controller_id: str
    snapshot: SensorSnapshot
    requested: ControlDecision
    applied: ControlDecision
    state: ActuatorState
    safety_overrides: tuple[str, ...]
    transitions: tuple[str, ...]
    watering_started_seconds: float = 0.0


def actuator_state_from_mapping(data: Mapping[str, Any] | None) -> ActuatorState:
    data = data or {}
    return ActuatorState(
        exhaust=bool(data.get("exhaust", False)),
        circulation=bool(data.get("circulation", False)),
        water_valve=bool(data.get("water_valve", False)),
    )


def soil_values(values: Sequence[Any]) -> tuple[float | None, ...]:
    converted: list[float | None] = []
    for value in values[:3]:
        try:
            converted.append(None if value in (None, "") else float(value))
        except (TypeError, ValueError):
            converted.append(None)
    return tuple(converted)
