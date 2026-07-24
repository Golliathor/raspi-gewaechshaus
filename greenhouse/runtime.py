from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any, Mapping

from greenhouse.controllers.base import Controller
from greenhouse.models import (
    ActuatorState,
    ControlContext,
    CycleResult,
    SensorSnapshot,
)
from greenhouse.safety import SafetyLayer


class ControlEngine:
    """Deterministische Laufzeit für Livebetrieb und Daten-Replay."""

    def __init__(
        self,
        controller: Controller,
        config: Mapping[str, Any],
        *,
        safety_layer: SafetyLayer | None = None,
        initial_state: ActuatorState | None = None,
    ) -> None:
        self.controller = controller
        self.config = config
        self.safety_layer = safety_layer or SafetyLayer()
        self.state = initial_state or ActuatorState()
        history_size = int(config.get("controller", {}).get("history_size", 120))
        self.history: deque[SensorSnapshot] = deque(maxlen=max(1, history_size))
        self.last_transition_at: dict[str, datetime] = {}
        self.last_watering_at: datetime | None = None
        self.last_fallback_watering_date: str | None = None
        self.watering_until: datetime | None = None
        self._watering_day: str | None = None
        self.daily_watering_seconds = 0.0

    def restore(self, data: Mapping[str, Any]) -> None:
        runtime = data.get("control_runtime", {})

        def read_time(key: str) -> datetime | None:
            value = runtime.get(key)
            try:
                return datetime.fromisoformat(value) if value else None
            except (TypeError, ValueError):
                return None

        self.last_watering_at = read_time("last_watering_at")
        self.watering_until = read_time("watering_until")
        self.last_fallback_watering_date = runtime.get("last_fallback_watering_date")
        self._watering_day = runtime.get("watering_day")
        self.daily_watering_seconds = float(runtime.get("daily_watering_seconds", 0.0))
        transitions = runtime.get("last_transition_at", {})
        self.last_transition_at = {}
        for name, value in transitions.items():
            try:
                self.last_transition_at[name] = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                continue

    def export_state(self) -> dict[str, Any]:
        return {
            "last_watering_at": (
                self.last_watering_at.isoformat(timespec="seconds")
                if self.last_watering_at
                else None
            ),
            "watering_until": (
                self.watering_until.isoformat(timespec="seconds")
                if self.watering_until
                else None
            ),
            "last_fallback_watering_date": self.last_fallback_watering_date,
            "watering_day": self._watering_day,
            "daily_watering_seconds": self.daily_watering_seconds,
            "last_transition_at": {
                name: value.isoformat(timespec="seconds")
                for name, value in self.last_transition_at.items()
            },
        }

    def step(
        self,
        snapshot: SensorSnapshot,
        *,
        now: datetime | None = None,
        watering_check_due: bool = False,
    ) -> CycleResult:
        now = now or snapshot.timestamp
        self._roll_watering_day(now)
        expired_transition = self._expire_watering(now)
        validated_snapshot = snapshot.validated()

        context = ControlContext(
            actuator_state=self.state,
            history=tuple(self.history),
            last_transition_at=dict(self.last_transition_at),
            last_watering_at=self.last_watering_at,
            last_fallback_watering_date=self.last_fallback_watering_date,
            daily_watering_seconds=self.daily_watering_seconds,
            watering_check_due=watering_check_due,
        )
        requested = self.controller.decide(validated_snapshot, context, self.config)
        safety = self.safety_layer.apply(
            requested, validated_snapshot, context, self.config, now
        )
        applied = safety.decision

        next_state = ActuatorState(
            exhaust=applied.exhaust,
            circulation=applied.circulation,
            water_valve=self.state.water_valve,
        )
        watering_started = 0.0
        if applied.watering_seconds > 0 and not next_state.water_valve:
            watering_started = applied.watering_seconds
            self.watering_until = now + timedelta(seconds=watering_started)
            self.last_watering_at = now
            self.daily_watering_seconds += watering_started
            next_state = ActuatorState(
                exhaust=next_state.exhaust,
                circulation=next_state.circulation,
                water_valve=True,
            )
            if "watering_fallback_no_sensor_values" in requested.reasons:
                self.last_fallback_watering_date = now.date().isoformat()

        transitions = list(expired_transition)
        for name in ("exhaust", "circulation", "water_valve"):
            if getattr(next_state, name) != getattr(self.state, name):
                transitions.append(f"{name}:{'on' if getattr(next_state, name) else 'off'}")
                self.last_transition_at[name] = now

        self.state = next_state
        self.history.append(validated_snapshot)
        return CycleResult(
            timestamp=now,
            controller_id=self.controller.controller_id,
            snapshot=validated_snapshot,
            requested=requested,
            applied=applied,
            state=self.state,
            safety_overrides=safety.overrides,
            transitions=tuple(transitions),
            watering_started_seconds=watering_started,
        )

    def request_manual_watering(self, seconds: float, now: datetime) -> float:
        self._roll_watering_day(now)
        self._expire_watering(now)
        if self.state.water_valve:
            return 0.0
        safety = self.config.get("safety", {})
        maximum = float(safety.get("max_watering_pulse_seconds", 60))
        daily_limit = float(safety.get("max_daily_watering_seconds", 180))
        duration = min(max(0.0, float(seconds)), maximum)
        duration = min(duration, max(0.0, daily_limit - self.daily_watering_seconds))
        if duration <= 0:
            return 0.0

        self.watering_until = now + timedelta(seconds=duration)
        self.last_watering_at = now
        self.daily_watering_seconds += duration
        if not self.state.water_valve:
            self.last_transition_at["water_valve"] = now
        self.state = ActuatorState(
            exhaust=self.state.exhaust,
            circulation=self.state.circulation,
            water_valve=True,
        )
        return duration

    def set_manual_relay(self, name: str, enabled: bool, now: datetime) -> None:
        if name not in {"exhaust", "circulation", "water_valve"}:
            raise ValueError(f"Unbekanntes Relais: {name}")
        if name == "water_valve" and enabled:
            self.request_manual_watering(
                float(self.config.get("watering_seconds", 10)), now
            )
            return
        values = self.state.as_dict()
        values[name] = bool(enabled)
        if name == "water_valve" and not enabled:
            self.watering_until = None
        self.state = ActuatorState(**values)
        self.last_transition_at[name] = now

    def _roll_watering_day(self, now: datetime) -> None:
        day = now.date().isoformat()
        if self._watering_day != day:
            self._watering_day = day
            self.daily_watering_seconds = 0.0

    def _expire_watering(self, now: datetime) -> tuple[str, ...]:
        if (
            self.state.water_valve
            and self.watering_until is not None
            and now >= self.watering_until
        ):
            self.state = ActuatorState(
                exhaust=self.state.exhaust,
                circulation=self.state.circulation,
                water_valve=False,
            )
            self.watering_until = None
            self.last_transition_at["water_valve"] = now
            return ("water_valve:off",)
        return ()
