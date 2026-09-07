from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from greenhouse.models import ControlContext, ControlDecision, SensorSnapshot


class BaselineHysteresisController:
    """Schwellwertregler mit Hysterese, Mindestlaufzeiten und Sperrzeiten."""

    controller_id = "baseline_hysteresis"

    def decide(
        self,
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
    ) -> ControlDecision:
        if not config.get("automation_enabled", True):
            return ControlDecision(
                exhaust=context.actuator_state.exhaust,
                circulation=context.actuator_state.circulation,
                reasons=("automation_disabled",),
                controller_state=dict(context.controller_state),
            )

        controller_config = config.get("controllers", {}).get(
            self.controller_id, {}
        )
        now = context.now or snapshot.timestamp
        temperature = snapshot.temperature_c
        humidity = snapshot.humidity_percent

        if temperature is None or humidity is None:
            exhaust = False
            circulation = False
            exhaust_reason = "exhaust_off_invalid_air"
            circulation_reason = "circulation_off_invalid_air"
            exhaust_lock_remaining = 0.0
            circulation_lock_remaining = 0.0
        else:
            (
                exhaust,
                exhaust_reason,
                exhaust_lock_remaining,
            ) = self._decide_exhaust(
                temperature, humidity, context, controller_config, now
            )
            (
                circulation,
                circulation_reason,
                circulation_lock_remaining,
            ) = self._decide_circulation(
                temperature, humidity, context, controller_config, now
            )

        (
            watering_seconds,
            watering_reason,
            next_controller_state,
            watering_diagnostics,
        ) = self._decide_watering(
            snapshot, context, config, controller_config, now
        )

        reasons = [exhaust_reason, circulation_reason]
        if watering_reason:
            reasons.append(watering_reason)
        return ControlDecision(
            exhaust=exhaust,
            circulation=circulation,
            watering_seconds=watering_seconds,
            reasons=tuple(reasons),
            diagnostics={
                "temperature_c": temperature,
                "humidity_percent": humidity,
                "lock_remaining_seconds": {
                    "exhaust": round(exhaust_lock_remaining, 3),
                    "circulation": round(circulation_lock_remaining, 3),
                    "watering": round(
                        watering_diagnostics["cooldown_remaining_seconds"], 3
                    ),
                },
                "watering_armed": watering_diagnostics["watering_armed"],
                "watering_rearm_mode": watering_diagnostics[
                    "watering_rearm_mode"
                ],
                "dry_soil_sensor_indices": watering_diagnostics[
                    "dry_soil_sensor_indices"
                ],
                "soil_moisture_target_percent": watering_diagnostics[
                    "soil_moisture_target_percent"
                ],
                "soil_moisture_target_reached": watering_diagnostics[
                    "soil_moisture_target_reached"
                ],
                "thresholds": self._threshold_diagnostics(controller_config),
            },
            controller_state=next_controller_state,
        )

    @classmethod
    def _decide_exhaust(
        cls,
        temperature: float,
        humidity: float,
        context: ControlContext,
        config: Mapping[str, Any],
        now: datetime,
    ) -> tuple[bool, str, float]:
        current = context.actuator_state.exhaust
        temp_on = cls._number(config, "exhaust_temperature_on_c", 28.0)
        temp_off = cls._number(config, "exhaust_temperature_off_c", 25.0)
        humidity_on = cls._number(
            config, "exhaust_humidity_on_percent", 50.0
        )
        humidity_off = cls._number(
            config, "exhaust_humidity_off_percent", 40.0
        )
        minimum_temperature = cls._number(
            config, "exhaust_min_temperature_c", 18.0
        )

        if current:
            if temperature < minimum_temperature:
                return False, "exhaust_off_below_min_temperature", 0.0
            should_turn_off = temperature <= temp_off and humidity <= humidity_off
            if not should_turn_off:
                return True, "exhaust_hold_on_hysteresis", 0.0
            remaining = cls._lock_remaining(
                context,
                "exhaust",
                cls._number(config, "exhaust_min_on_seconds", 120.0),
                now,
            )
            if remaining > 0:
                return True, "exhaust_hold_on_minimum_time", remaining
            return False, "exhaust_off_below_off_thresholds", 0.0

        should_turn_on = temperature >= temp_on or (
            temperature >= minimum_temperature and humidity >= humidity_on
        )
        if not should_turn_on:
            return False, "exhaust_hold_off_hysteresis", 0.0
        remaining = cls._lock_remaining(
            context,
            "exhaust",
            cls._number(config, "exhaust_min_off_seconds", 120.0),
            now,
        )
        if remaining > 0:
            return False, "exhaust_hold_off_minimum_time", remaining
        if temperature >= temp_on:
            return True, "exhaust_on_temperature_threshold", 0.0
        return True, "exhaust_on_humidity_threshold", 0.0

    @classmethod
    def _decide_circulation(
        cls,
        temperature: float,
        humidity: float,
        context: ControlContext,
        config: Mapping[str, Any],
        now: datetime,
    ) -> tuple[bool, str, float]:
        current = context.actuator_state.circulation
        temp_on = cls._number(config, "circulation_temperature_on_c", 24.0)
        temp_off = cls._number(config, "circulation_temperature_off_c", 22.0)
        humidity_on = cls._number(
            config, "circulation_humidity_on_percent", 45.0
        )
        humidity_off = cls._number(
            config, "circulation_humidity_off_percent", 38.0
        )

        if current:
            should_turn_off = temperature <= temp_off and humidity <= humidity_off
            if not should_turn_off:
                return True, "circulation_hold_on_hysteresis", 0.0
            remaining = cls._lock_remaining(
                context,
                "circulation",
                cls._number(config, "circulation_min_on_seconds", 120.0),
                now,
            )
            if remaining > 0:
                return True, "circulation_hold_on_minimum_time", remaining
            return False, "circulation_off_below_off_thresholds", 0.0

        should_turn_on = temperature >= temp_on or humidity >= humidity_on
        if not should_turn_on:
            return False, "circulation_hold_off_hysteresis", 0.0
        remaining = cls._lock_remaining(
            context,
            "circulation",
            cls._number(config, "circulation_min_off_seconds", 120.0),
            now,
        )
        if remaining > 0:
            return False, "circulation_hold_off_minimum_time", remaining
        if temperature >= temp_on:
            return True, "circulation_on_temperature_threshold", 0.0
        return True, "circulation_on_humidity_threshold", 0.0

    @classmethod
    def _decide_watering(
        cls,
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
        controller_config: Mapping[str, Any],
        now: datetime,
    ) -> tuple[float, str | None, dict[str, Any], dict[str, Any]]:
        state = dict(context.controller_state)
        enabled_indices = tuple(
            index
            for index, sensor in enumerate(config.get("soil_sensors", [])[:3])
            if sensor.get("enabled", False)
        )
        valid_values = {
            index: snapshot.soil_moisture_percent[index]
            for index in enabled_indices
            if index < len(snapshot.soil_moisture_percent)
            and snapshot.soil_moisture_percent[index] is not None
        }
        target_threshold = cls._number(
            controller_config, "soil_moisture_off_percent", 45.0
        )
        target_reached = bool(enabled_indices) and all(
            index in valid_values
            and valid_values[index] >= target_threshold
            for index in enabled_indices
        )

        dry_on = cls._number(
            controller_config, "soil_moisture_on_percent", 35.0
        )
        dry_indices = tuple(
            index
            for index, value in valid_values.items()
            if value is not None and value <= dry_on
        )
        cooldown_remaining = cls._watering_cooldown_remaining(
            context,
            cls._number(controller_config, "watering_cooldown_seconds", 3600.0),
            now,
        )
        armed = (
            not context.actuator_state.water_valve
            and cooldown_remaining <= 0
        )
        state.pop("last_observed_watering_at", None)
        watering_seconds = 0.0
        watering_reason: str | None = None

        if context.watering_check_due and config.get("watering_enabled", True):
            if not enabled_indices:
                watering_reason = "watering_no_enabled_sensors"
            elif len(valid_values) != len(enabled_indices):
                watering_reason = "watering_blocked_missing_soil"
            elif context.actuator_state.water_valve:
                watering_reason = "watering_blocked_valve_active"
            elif not dry_indices:
                watering_reason = "watering_hold_above_on_threshold"
            elif cooldown_remaining > 0:
                watering_reason = "watering_blocked_cooldown"
            else:
                watering_seconds = cls._number(
                    controller_config, "watering_seconds", 10.0
                )
                watering_reason = "watering_on_dry_threshold"
                armed = False

        state["watering_armed"] = armed
        return (
            watering_seconds,
            watering_reason,
            state,
            {
                "watering_armed": armed,
                "watering_rearm_mode": "cooldown",
                "cooldown_remaining_seconds": cooldown_remaining,
                "dry_soil_sensor_indices": dry_indices,
                "soil_moisture_target_percent": target_threshold,
                "soil_moisture_target_reached": target_reached,
            },
        )

    @staticmethod
    def _lock_remaining(
        context: ControlContext,
        actuator_name: str,
        duration_seconds: float,
        now: datetime,
    ) -> float:
        last_transition = context.last_transition_at.get(actuator_name)
        if last_transition is None:
            return 0.0
        elapsed = max(0.0, (now - last_transition).total_seconds())
        return max(0.0, duration_seconds - elapsed)

    @staticmethod
    def _watering_cooldown_remaining(
        context: ControlContext,
        cooldown_seconds: float,
        now: datetime,
    ) -> float:
        if context.last_watering_at is None:
            return 0.0
        elapsed = max(0.0, (now - context.last_watering_at).total_seconds())
        return max(0.0, cooldown_seconds - elapsed)

    @classmethod
    def _threshold_diagnostics(
        cls, config: Mapping[str, Any]
    ) -> dict[str, float]:
        defaults = {
            "exhaust_temperature_on_c": 28.0,
            "exhaust_temperature_off_c": 25.0,
            "exhaust_humidity_on_percent": 50.0,
            "exhaust_humidity_off_percent": 40.0,
            "exhaust_min_temperature_c": 18.0,
            "exhaust_min_on_seconds": 120.0,
            "exhaust_min_off_seconds": 120.0,
            "circulation_temperature_on_c": 24.0,
            "circulation_temperature_off_c": 22.0,
            "circulation_humidity_on_percent": 45.0,
            "circulation_humidity_off_percent": 38.0,
            "circulation_min_on_seconds": 120.0,
            "circulation_min_off_seconds": 120.0,
            "soil_moisture_on_percent": 35.0,
            "soil_moisture_off_percent": 45.0,
            "watering_seconds": 10.0,
            "watering_cooldown_seconds": 3600.0,
        }
        return {
            key: cls._number(config, key, default)
            for key, default in defaults.items()
        }

    @staticmethod
    def _number(
        config: Mapping[str, Any],
        key: str,
        default: float,
    ) -> float:
        return float(config.get(key, default))
