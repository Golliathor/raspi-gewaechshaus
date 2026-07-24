from __future__ import annotations

from typing import Any, Mapping

from greenhouse.models import ControlContext, ControlDecision, SensorSnapshot


class BaselineFixedController:
    """Zustandsloser Referenzregler mit festen Schwellwerten."""

    controller_id = "baseline_fixed"

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
            )

        controller_config = config.get("controllers", {}).get(
            self.controller_id, {}
        )
        temperature = snapshot.temperature_c
        humidity = snapshot.humidity_percent
        exhaust = False
        circulation = False
        reasons: list[str] = []

        if temperature is None or humidity is None:
            reasons.extend(
                ("exhaust_off_invalid_air", "circulation_off_invalid_air")
            )
        else:
            exhaust, exhaust_reason = self._decide_exhaust(
                temperature, humidity, controller_config
            )
            circulation, circulation_reason = self._decide_circulation(
                temperature, humidity, controller_config
            )
            reasons.extend((exhaust_reason, circulation_reason))

        watering_seconds = 0.0
        dry_sensor_indices: tuple[int, ...] = ()
        if context.watering_check_due and config.get("watering_enabled", True):
            (
                watering_seconds,
                watering_reason,
                dry_sensor_indices,
            ) = self._decide_watering(snapshot, config, controller_config)
            reasons.append(watering_reason)

        return ControlDecision(
            exhaust=exhaust,
            circulation=circulation,
            watering_seconds=watering_seconds,
            reasons=tuple(reasons),
            diagnostics={
                "temperature_c": temperature,
                "humidity_percent": humidity,
                "dry_soil_sensor_indices": dry_sensor_indices,
                "thresholds": {
                    "exhaust_temperature_c": self._number(
                        controller_config, "exhaust_temperature_threshold_c", 28.0
                    ),
                    "exhaust_humidity_percent": self._number(
                        controller_config,
                        "exhaust_humidity_threshold_percent",
                        50.0,
                    ),
                    "exhaust_min_temperature_c": self._number(
                        controller_config,
                        "exhaust_min_temperature_c",
                        18.0,
                    ),
                    "circulation_temperature_c": self._number(
                        controller_config,
                        "circulation_temperature_threshold_c",
                        24.0,
                    ),
                    "circulation_humidity_percent": self._number(
                        controller_config,
                        "circulation_humidity_threshold_percent",
                        45.0,
                    ),
                    "soil_moisture_percent": self._number(
                        controller_config,
                        "soil_moisture_threshold_percent",
                        35.0,
                    ),
                    "watering_seconds": self._number(
                        controller_config, "watering_seconds", 10.0
                    ),
                },
            },
        )

    @classmethod
    def _decide_exhaust(
        cls,
        temperature: float,
        humidity: float,
        config: Mapping[str, Any],
    ) -> tuple[bool, str]:
        temperature_threshold = cls._number(
            config, "exhaust_temperature_threshold_c", 28.0
        )
        humidity_threshold = cls._number(
            config, "exhaust_humidity_threshold_percent", 50.0
        )
        minimum_temperature = cls._number(
            config, "exhaust_min_temperature_c", 18.0
        )
        if temperature >= temperature_threshold:
            return True, "exhaust_on_temperature_threshold"
        if temperature >= minimum_temperature and humidity >= humidity_threshold:
            return True, "exhaust_on_humidity_threshold"
        return False, "exhaust_off_below_fixed_thresholds"

    @classmethod
    def _decide_circulation(
        cls,
        temperature: float,
        humidity: float,
        config: Mapping[str, Any],
    ) -> tuple[bool, str]:
        temperature_threshold = cls._number(
            config, "circulation_temperature_threshold_c", 24.0
        )
        humidity_threshold = cls._number(
            config, "circulation_humidity_threshold_percent", 45.0
        )
        if temperature >= temperature_threshold:
            return True, "circulation_on_temperature_threshold"
        if humidity >= humidity_threshold:
            return True, "circulation_on_humidity_threshold"
        return False, "circulation_off_below_fixed_thresholds"

    @classmethod
    def _decide_watering(
        cls,
        snapshot: SensorSnapshot,
        config: Mapping[str, Any],
        controller_config: Mapping[str, Any],
    ) -> tuple[float, str, tuple[int, ...]]:
        threshold = cls._number(
            controller_config, "soil_moisture_threshold_percent", 35.0
        )
        enabled_indices = tuple(
            index
            for index, sensor in enumerate(config.get("soil_sensors", [])[:3])
            if sensor.get("enabled", False)
        )
        if not enabled_indices:
            return 0.0, "watering_no_enabled_sensors", ()

        valid_values = tuple(
            (index, snapshot.soil_moisture_percent[index])
            for index in enabled_indices
            if index < len(snapshot.soil_moisture_percent)
            and snapshot.soil_moisture_percent[index] is not None
        )
        if not valid_values:
            return 0.0, "watering_off_no_valid_soil", ()

        dry_indices = tuple(
            index
            for index, value in valid_values
            if value is not None and value <= threshold
        )
        if dry_indices:
            duration = cls._number(
                controller_config, "watering_seconds", 10.0
            )
            return duration, "watering_on_fixed_soil_threshold", dry_indices
        return 0.0, "watering_off_above_fixed_threshold", ()

    @staticmethod
    def _number(
        config: Mapping[str, Any],
        key: str,
        default: float,
    ) -> float:
        return float(config.get(key, default))
