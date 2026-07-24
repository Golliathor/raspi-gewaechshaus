from __future__ import annotations

from typing import Any, Mapping

from greenhouse.models import ControlContext, ControlDecision, SensorSnapshot


class LegacyController:
    """Abbildung der bisherigen Lüfter- und Bewässerungsentscheidung."""

    controller_id = "legacy"

    def decide(
        self,
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
    ) -> ControlDecision:
        current = context.actuator_state
        exhaust = current.exhaust
        circulation = current.circulation
        watering_seconds = 0.0
        reasons: list[str] = []
        diagnostics: dict[str, Any] = {}

        if not config.get("automation_enabled", True):
            return ControlDecision(
                exhaust=exhaust,
                circulation=circulation,
                reasons=("automation_disabled",),
            )

        temperature = snapshot.temperature_c
        humidity = snapshot.humidity_percent
        if temperature is not None and humidity is not None:
            exhaust, exhaust_reason = self._decide_exhaust(
                temperature, humidity, exhaust, config
            )
            circulation, circulation_reason = self._decide_circulation(
                temperature, humidity, circulation, config
            )
            reasons.extend((exhaust_reason, circulation_reason))
        else:
            reasons.append("air_values_missing_hold_fans")

        if context.watering_check_due and config.get("watering_enabled", True):
            watering_seconds, watering_reason = self._decide_watering(
                snapshot, context, config
            )
            reasons.append(watering_reason)

        diagnostics.update(
            {
                "temperature_c": temperature,
                "humidity_percent": humidity,
                "valid_soil_sensor_count": len(snapshot.valid_soil_values),
            }
        )
        return ControlDecision(
            exhaust=exhaust,
            circulation=circulation,
            watering_seconds=watering_seconds,
            reasons=tuple(reasons),
            diagnostics=diagnostics,
        )

    @staticmethod
    def _decide_exhaust(
        temperature: float,
        humidity: float,
        currently_on: bool,
        config: Mapping[str, Any],
    ) -> tuple[bool, str]:
        temp_on = float(config.get("exhaust_temp_on_c", 28.0))
        temp_off = float(config.get("exhaust_temp_off_c", 25.0))
        humidity_on = float(config.get("exhaust_humidity_on", 50.0))
        humidity_off = float(config.get("exhaust_humidity_off", 40.0))
        minimum_temperature = float(config.get("exhaust_min_temp_c", 18.0))

        if currently_on:
            if temperature < minimum_temperature:
                return False, "exhaust_off_below_min_temperature"
            if temperature <= temp_off and humidity <= humidity_off:
                return False, "exhaust_off_below_thresholds"
            return True, "exhaust_hold_on"

        if temperature >= temp_on:
            return True, "exhaust_on_temperature"
        if temperature >= minimum_temperature and humidity >= humidity_on:
            return True, "exhaust_on_humidity"
        return False, "exhaust_hold_off"

    @staticmethod
    def _decide_circulation(
        temperature: float,
        humidity: float,
        currently_on: bool,
        config: Mapping[str, Any],
    ) -> tuple[bool, str]:
        temp_on = float(config.get("circulation_temp_on_c", 24.0))
        temp_off = float(config.get("circulation_temp_off_c", 22.0))
        humidity_on = float(config.get("circulation_humidity_on", 45.0))
        humidity_off = float(config.get("circulation_humidity_off", 38.0))

        if currently_on:
            if temperature <= temp_off and humidity <= humidity_off:
                return False, "circulation_off_below_thresholds"
            return True, "circulation_hold_on"

        if temperature >= temp_on:
            return True, "circulation_on_temperature"
        if humidity >= humidity_on:
            return True, "circulation_on_humidity"
        return False, "circulation_hold_off"

    @staticmethod
    def _decide_watering(
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
    ) -> tuple[float, str]:
        sensors = config.get("soil_sensors", [])
        enabled_indices = [
            index for index, sensor in enumerate(sensors[:3]) if sensor.get("enabled", False)
        ]
        if not enabled_indices:
            return 0.0, "watering_no_enabled_sensors"

        current_values = [
            (index, snapshot.soil_moisture_percent[index])
            for index in enabled_indices
            if index < len(snapshot.soil_moisture_percent)
            and snapshot.soil_moisture_percent[index] is not None
        ]
        if not current_values:
            today = snapshot.timestamp.date().isoformat()
            if context.last_fallback_watering_date != today:
                return (
                    float(config.get("watering_fallback_seconds", 40)),
                    "watering_fallback_no_sensor_values",
                )
            return 0.0, "watering_fallback_already_used"

        for index, value in current_values:
            threshold = float(sensors[index].get("dry_below_percent", 35))
            if value is not None and value <= threshold:
                return (
                    float(config.get("watering_seconds", 10)),
                    f"watering_soil_{index + 1}_dry",
                )
        return 0.0, "watering_soil_ok"
