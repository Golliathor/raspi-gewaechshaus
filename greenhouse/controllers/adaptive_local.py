from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from greenhouse.models import ControlContext, ControlDecision, SensorSnapshot
from greenhouse.trends import linear_trend_per_minute


class AdaptiveLocalController:
    """Nachvollziehbare adaptive Regelung ausschließlich mit lokaler Sensorik."""

    controller_id = "adaptive_local"
    watering_activation_reason = "watering_on_adaptive_local_demand"

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
        history = (*context.history, snapshot)
        trends = self._calculate_trends(
            history, snapshot, config, controller_config, now
        )
        adaptive = self._adaptive_values(
            snapshot, trends, controller_config
        )
        adaptive, external_diagnostics = self._apply_external_adjustments(
            snapshot,
            now,
            controller_config,
            adaptive,
        )

        if snapshot.temperature_c is None or snapshot.humidity_percent is None:
            exhaust = False
            circulation = False
            exhaust_reason = "exhaust_off_invalid_air"
            circulation_reason = "circulation_off_invalid_air"
            exhaust_lock = 0.0
            circulation_lock = 0.0
        else:
            exhaust, exhaust_reason, exhaust_lock = self._decide_exhaust(
                snapshot.temperature_c,
                snapshot.humidity_percent,
                context,
                controller_config,
                adaptive,
                now,
            )
            (
                circulation,
                circulation_reason,
                circulation_lock,
            ) = self._decide_circulation(
                snapshot.temperature_c,
                snapshot.humidity_percent,
                context,
                controller_config,
                adaptive,
                now,
            )

        (
            watering_seconds,
            watering_reason,
            next_controller_state,
            watering_diagnostics,
        ) = self._decide_watering(
            snapshot,
            context,
            config,
            controller_config,
            adaptive,
            now,
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
                "temperature_c": snapshot.temperature_c,
                "humidity_percent": snapshot.humidity_percent,
                "light_percent": snapshot.light_percent,
                **external_diagnostics,
                "trends_per_minute": {
                    key: round(value, 4) for key, value in trends.items()
                },
                "adaptive_adjustments": {
                    key: round(value, 4) for key, value in adaptive.items()
                },
                "lock_remaining_seconds": {
                    "exhaust": round(exhaust_lock, 3),
                    "circulation": round(circulation_lock, 3),
                    "watering": round(
                        watering_diagnostics["cooldown_remaining_seconds"], 3
                    ),
                },
                "watering_armed": watering_diagnostics["watering_armed"],
                "dry_soil_sensor_indices": watering_diagnostics[
                    "dry_soil_sensor_indices"
                ],
            },
            controller_state=next_controller_state,
        )

    @classmethod
    def _apply_external_adjustments(
        cls,
        snapshot: SensorSnapshot,
        now: datetime,
        config: Mapping[str, Any],
        adaptive: Mapping[str, float],
    ) -> tuple[dict[str, float], dict[str, Any]]:
        return dict(adaptive), {
            "weather_used": False,
            "weather_status": "ignored",
        }

    @classmethod
    def _calculate_trends(
        cls,
        history: Sequence[SensorSnapshot],
        snapshot: SensorSnapshot,
        config: Mapping[str, Any],
        controller_config: Mapping[str, Any],
        now: datetime,
    ) -> dict[str, float]:
        window = cls._number(controller_config, "trend_window_seconds", 900.0)
        minimum_span = cls._number(
            controller_config, "trend_minimum_span_seconds", 120.0
        )
        enabled_indices = tuple(
            index
            for index, sensor in enumerate(config.get("soil_sensors", [])[:3])
            if sensor.get("enabled", False)
        )

        def soil_mean(item: SensorSnapshot) -> float | None:
            values = [
                item.soil_moisture_percent[index]
                for index in enabled_indices
                if index < len(item.soil_moisture_percent)
                and item.soil_moisture_percent[index] is not None
            ]
            if not values:
                return None
            return sum(values) / len(values)

        temperature = linear_trend_per_minute(
            ((item.timestamp, item.temperature_c) for item in history),
            now=now,
            window_seconds=window,
            minimum_span_seconds=minimum_span,
        )
        humidity = linear_trend_per_minute(
            ((item.timestamp, item.humidity_percent) for item in history),
            now=now,
            window_seconds=window,
            minimum_span_seconds=minimum_span,
        )
        soil = linear_trend_per_minute(
            ((item.timestamp, soil_mean(item)) for item in history),
            now=now,
            window_seconds=window,
            minimum_span_seconds=minimum_span,
        )
        return {
            "temperature_c": cls._clamp(
                temperature,
                -cls._number(
                    controller_config,
                    "max_abs_temperature_trend_per_minute",
                    2.0,
                ),
                cls._number(
                    controller_config,
                    "max_abs_temperature_trend_per_minute",
                    2.0,
                ),
            ),
            "humidity_percent": cls._clamp(
                humidity,
                -cls._number(
                    controller_config,
                    "max_abs_humidity_trend_per_minute",
                    10.0,
                ),
                cls._number(
                    controller_config,
                    "max_abs_humidity_trend_per_minute",
                    10.0,
                ),
            ),
            "soil_moisture_percent": cls._clamp(
                soil,
                -cls._number(
                    controller_config,
                    "max_abs_soil_trend_per_minute",
                    10.0,
                ),
                cls._number(
                    controller_config,
                    "max_abs_soil_trend_per_minute",
                    10.0,
                ),
            ),
        }

    @classmethod
    def _adaptive_values(
        cls,
        snapshot: SensorSnapshot,
        trends: Mapping[str, float],
        config: Mapping[str, Any],
    ) -> dict[str, float]:
        lookahead = cls._number(config, "trend_lookahead_minutes", 10.0)
        light_activation = cls._number(
            config, "light_adaptation_start_percent", 50.0
        )
        light_denominator = max(1.0, 100.0 - light_activation)
        light_factor = cls._clamp(
            ((snapshot.light_percent or 0.0) - light_activation)
            / light_denominator,
            0.0,
            1.0,
        )
        projected_heating = max(0.0, trends["temperature_c"]) * lookahead
        projected_humidity_rise = (
            max(0.0, trends["humidity_percent"]) * lookahead
        )
        projected_soil_drop = (
            max(0.0, -trends["soil_moisture_percent"]) * lookahead
        )

        temperature_reduction = cls._clamp(
            projected_heating
            + light_factor
            * cls._number(config, "light_temperature_reduction_c", 1.5),
            0.0,
            cls._number(config, "max_temperature_reduction_c", 3.0),
        )
        humidity_reduction = cls._clamp(
            projected_humidity_rise,
            0.0,
            cls._number(config, "max_humidity_reduction_percent", 10.0),
        )
        temperature_stress = max(
            0.0,
            (snapshot.temperature_c or 0.0)
            - cls._number(config, "watering_temperature_reference_c", 25.0),
        )
        soil_threshold_increase = cls._clamp(
            projected_soil_drop * 0.5
            + temperature_stress * 0.3
            + light_factor,
            0.0,
            cls._number(config, "max_soil_threshold_increase_percent", 5.0),
        )

        return {
            "light_factor": light_factor,
            "projected_heating_c": projected_heating,
            "projected_humidity_rise_percent": projected_humidity_rise,
            "projected_soil_drop_percent": projected_soil_drop,
            "temperature_threshold_reduction_c": temperature_reduction,
            "humidity_threshold_reduction_percent": humidity_reduction,
            "effective_exhaust_temperature_on_c": cls._number(
                config, "exhaust_temperature_on_c", 28.0
            )
            - temperature_reduction,
            "effective_exhaust_temperature_off_c": cls._number(
                config, "exhaust_temperature_off_c", 25.0
            )
            - temperature_reduction * 0.5,
            "effective_exhaust_humidity_on_percent": cls._number(
                config, "exhaust_humidity_on_percent", 50.0
            )
            - humidity_reduction,
            "effective_exhaust_humidity_off_percent": cls._number(
                config, "exhaust_humidity_off_percent", 40.0
            )
            - humidity_reduction * 0.5,
            "effective_circulation_temperature_on_c": cls._number(
                config, "circulation_temperature_on_c", 24.0
            )
            - temperature_reduction,
            "effective_circulation_temperature_off_c": cls._number(
                config, "circulation_temperature_off_c", 22.0
            )
            - temperature_reduction * 0.5,
            "effective_circulation_humidity_on_percent": cls._number(
                config, "circulation_humidity_on_percent", 45.0
            )
            - humidity_reduction,
            "effective_circulation_humidity_off_percent": cls._number(
                config, "circulation_humidity_off_percent", 38.0
            )
            - humidity_reduction * 0.5,
            "effective_soil_moisture_on_percent": cls._number(
                config, "soil_moisture_on_percent", 35.0
            )
            + soil_threshold_increase,
            "soil_threshold_increase_percent": soil_threshold_increase,
            "temperature_stress_c": temperature_stress,
        }

    @classmethod
    def _decide_exhaust(
        cls,
        temperature: float,
        humidity: float,
        context: ControlContext,
        config: Mapping[str, Any],
        adaptive: Mapping[str, float],
        now: datetime,
    ) -> tuple[bool, str, float]:
        current = context.actuator_state.exhaust
        minimum_temperature = cls._number(
            config, "exhaust_min_temperature_c", 18.0
        )
        if current:
            if temperature < minimum_temperature:
                return False, "exhaust_off_below_min_temperature", 0.0
            should_turn_off = (
                temperature
                <= adaptive["effective_exhaust_temperature_off_c"]
                and humidity
                <= adaptive["effective_exhaust_humidity_off_percent"]
            )
            if not should_turn_off:
                return True, "exhaust_hold_on_adaptive_hysteresis", 0.0
            remaining = cls._lock_remaining(
                context,
                "exhaust",
                cls._number(config, "exhaust_min_on_seconds", 120.0),
                now,
            )
            if remaining > 0:
                return True, "exhaust_hold_on_minimum_time", remaining
            return False, "exhaust_off_adaptive_thresholds", 0.0

        should_turn_on = (
            temperature >= adaptive["effective_exhaust_temperature_on_c"]
            or (
                temperature >= minimum_temperature
                and humidity
                >= adaptive["effective_exhaust_humidity_on_percent"]
            )
        )
        if not should_turn_on:
            return False, "exhaust_hold_off_adaptive_hysteresis", 0.0
        remaining = cls._lock_remaining(
            context,
            "exhaust",
            cls._number(config, "exhaust_min_off_seconds", 120.0),
            now,
        )
        if remaining > 0:
            return False, "exhaust_hold_off_minimum_time", remaining
        return True, "exhaust_on_adaptive_threshold", 0.0

    @classmethod
    def _decide_circulation(
        cls,
        temperature: float,
        humidity: float,
        context: ControlContext,
        config: Mapping[str, Any],
        adaptive: Mapping[str, float],
        now: datetime,
    ) -> tuple[bool, str, float]:
        current = context.actuator_state.circulation
        if current:
            should_turn_off = (
                temperature
                <= adaptive["effective_circulation_temperature_off_c"]
                and humidity
                <= adaptive["effective_circulation_humidity_off_percent"]
            )
            if not should_turn_off:
                return True, "circulation_hold_on_adaptive_hysteresis", 0.0
            remaining = cls._lock_remaining(
                context,
                "circulation",
                cls._number(config, "circulation_min_on_seconds", 120.0),
                now,
            )
            if remaining > 0:
                return True, "circulation_hold_on_minimum_time", remaining
            return False, "circulation_off_adaptive_thresholds", 0.0

        should_turn_on = (
            temperature >= adaptive["effective_circulation_temperature_on_c"]
            or humidity
            >= adaptive["effective_circulation_humidity_on_percent"]
        )
        if not should_turn_on:
            return False, "circulation_hold_off_adaptive_hysteresis", 0.0
        remaining = cls._lock_remaining(
            context,
            "circulation",
            cls._number(config, "circulation_min_off_seconds", 120.0),
            now,
        )
        if remaining > 0:
            return False, "circulation_hold_off_minimum_time", remaining
        return True, "circulation_on_adaptive_threshold", 0.0

    @classmethod
    def _decide_watering(
        cls,
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
        controller_config: Mapping[str, Any],
        adaptive: Mapping[str, float],
        now: datetime,
    ) -> tuple[float, str | None, dict[str, Any], dict[str, Any]]:
        state = dict(context.controller_state)
        armed = bool(state.get("watering_armed", True))
        last_watering_iso = (
            context.last_watering_at.isoformat(timespec="seconds")
            if context.last_watering_at
            else None
        )
        if state.get("last_observed_watering_at") != last_watering_iso:
            if last_watering_iso is not None:
                armed = False
            state["last_observed_watering_at"] = last_watering_iso

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
        rearm_threshold = cls._number(
            controller_config, "soil_moisture_off_percent", 45.0
        )
        if enabled_indices and all(
            index in valid_values
            and valid_values[index] >= rearm_threshold
            for index in enabled_indices
        ):
            armed = True

        dry_threshold = adaptive["effective_soil_moisture_on_percent"]
        dry_indices = tuple(
            index
            for index, value in valid_values.items()
            if value is not None and value <= dry_threshold
        )
        cooldown_remaining = cls._watering_cooldown_remaining(
            context,
            cls._number(
                controller_config, "watering_cooldown_seconds", 3600.0
            ),
            now,
        )
        duration = 0.0
        reason: str | None = None
        if context.watering_check_due and config.get("watering_enabled", True):
            if not enabled_indices:
                reason = "watering_no_enabled_sensors"
            elif len(valid_values) != len(enabled_indices):
                reason = "watering_blocked_missing_soil"
            elif context.actuator_state.water_valve:
                reason = "watering_blocked_valve_active"
            elif not dry_indices:
                reason = "watering_hold_above_adaptive_threshold"
            elif not armed:
                reason = "watering_blocked_hysteresis"
            elif cooldown_remaining > 0:
                reason = "watering_blocked_cooldown"
            else:
                minimum_soil = min(
                    value for value in valid_values.values() if value is not None
                )
                deficit = max(0.0, dry_threshold - minimum_soil)
                raw_duration = (
                    cls._number(controller_config, "watering_base_seconds", 10.0)
                    + deficit * 0.5
                    + adaptive["projected_soil_drop_percent"] * 0.5
                    + adaptive["temperature_stress_c"] * 0.5
                    + adaptive["light_factor"] * 2.0
                ) * adaptive.get("watering_duration_multiplier", 1.0)
                duration = cls._clamp(
                    raw_duration,
                    cls._number(controller_config, "watering_min_seconds", 5.0),
                    cls._number(controller_config, "watering_max_seconds", 30.0),
                )
                reason = cls.watering_activation_reason
                armed = False

        state["watering_armed"] = armed
        return (
            round(duration, 3),
            reason,
            state,
            {
                "watering_armed": armed,
                "cooldown_remaining_seconds": cooldown_remaining,
                "dry_soil_sensor_indices": dry_indices,
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

    @staticmethod
    def _clamp(value: float, minimum: float, maximum: float) -> float:
        return max(minimum, min(maximum, value))

    @staticmethod
    def _number(
        config: Mapping[str, Any],
        key: str,
        default: float,
    ) -> float:
        return float(config.get(key, default))
