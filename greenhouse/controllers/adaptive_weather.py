from __future__ import annotations

from datetime import datetime
from math import exp
from typing import Any, Mapping

from greenhouse.controllers.adaptive_local import AdaptiveLocalController
from greenhouse.models import ControlContext, ControlDecision, SensorSnapshot


class AdaptiveWeatherController(AdaptiveLocalController):
    """Lokale adaptive Regelung mit erklärbaren Wetterkorrekturen."""

    controller_id = "adaptive_weather"
    watering_activation_reason = "watering_on_adaptive_weather_demand"

    def decide(
        self,
        snapshot: SensorSnapshot,
        context: ControlContext,
        config: Mapping[str, Any],
    ) -> ControlDecision:
        # Ansatz B verwendet bewusst exakt die lokalen Parameter von Ansatz A.
        # Sein eigener Bereich enthält ausschließlich Wetterkorrekturen.
        combined_config = dict(config)
        controllers = dict(config.get("controllers", {}))
        controller_config = dict(controllers.get("adaptive_local", {}))
        controller_config.update(controllers.get(self.controller_id, {}))
        controllers[self.controller_id] = controller_config
        combined_config["controllers"] = controllers
        return super().decide(snapshot, context, combined_config)

    @classmethod
    def _apply_external_adjustments(
        cls,
        snapshot: SensorSnapshot,
        now: datetime,
        config: Mapping[str, Any],
        adaptive: Mapping[str, float],
    ) -> tuple[dict[str, float], dict[str, Any]]:
        result = dict(adaptive)
        weather = snapshot.weather
        if weather is None:
            return result, {
                "weather_used": False,
                "weather_status": "missing",
            }

        weather_age = max(0.0, (now - weather.timestamp).total_seconds())
        max_age = cls._number(config, "weather_max_age_seconds", 3600.0)
        if weather_age > max_age:
            return result, {
                "weather_used": False,
                "weather_status": "stale",
                "weather_age_seconds": round(weather_age, 3),
                "weather_provider": weather.provider,
            }

        temperature_score = 0.0
        humidity_score = 0.0
        indoor_absolute_humidity = None
        outdoor_absolute_humidity = None
        outdoor_heat_score = 0.0
        rain_score = 0.0
        useful_values = 0

        if (
            snapshot.temperature_c is not None
            and weather.outside_temperature_c is not None
        ):
            temperature_score = cls._clamp(
                (
                    snapshot.temperature_c
                    - weather.outside_temperature_c
                )
                / max(
                    0.1,
                    cls._number(
                        config,
                        "outdoor_temperature_difference_scale_c",
                        10.0,
                    ),
                ),
                -1.0,
                1.0,
            )
            useful_values += 1
            outdoor_heat_score = cls._clamp(
                (
                    weather.outside_temperature_c
                    - cls._number(config, "outdoor_heat_reference_c", 28.0)
                )
                / max(
                    0.1,
                    cls._number(config, "outdoor_heat_scale_c", 8.0),
                ),
                0.0,
                1.0,
            )

        if (
            snapshot.temperature_c is not None
            and snapshot.humidity_percent is not None
            and weather.outside_temperature_c is not None
            and weather.outside_humidity_percent is not None
            and -90.0 <= weather.outside_temperature_c <= 70.0
            and 0.0 <= weather.outside_humidity_percent <= 100.0
        ):
            indoor_absolute_humidity = cls._absolute_humidity_g_m3(
                snapshot.temperature_c, snapshot.humidity_percent
            )
            outdoor_absolute_humidity = cls._absolute_humidity_g_m3(
                weather.outside_temperature_c,
                weather.outside_humidity_percent,
            )
            humidity_score = cls._clamp(
                (
                    indoor_absolute_humidity
                    - outdoor_absolute_humidity
                )
                / max(
                    0.1,
                    cls._number(
                        config,
                        "absolute_humidity_difference_scale_g_m3",
                        5.0,
                    ),
                ),
                -1.0,
                1.0,
            )
            useful_values += 1

        rain_probability = weather.precipitation_probability_percent
        rain_amount = weather.precipitation_mm
        probability_threshold = cls._number(
            config, "rain_probability_threshold_percent", 60.0
        )
        if (
            rain_probability is not None
            and rain_amount is not None
            and rain_probability >= probability_threshold
        ):
            rain_score = cls._clamp(
                rain_amount
                / max(
                    0.1,
                    cls._number(config, "rain_amount_reference_mm", 5.0),
                ),
                0.0,
                1.0,
            ) * cls._clamp(rain_probability / 100.0, 0.0, 1.0)
            useful_values += 2
        elif rain_probability is not None or rain_amount is not None:
            useful_values += 1

        if useful_values == 0:
            return result, {
                "weather_used": False,
                "weather_status": "no_usable_values",
                "weather_age_seconds": round(weather_age, 3),
                "weather_provider": weather.provider,
            }

        outdoor_temperature_adjustment = (
            temperature_score
            * cls._number(
                config, "max_outdoor_temperature_adjustment_c", 1.5
            )
        )
        exhaust_temperature_adjustment = cls._cap_hysteresis_adjustment(
            outdoor_temperature_adjustment,
            result["effective_exhaust_temperature_on_c"],
            result["effective_exhaust_temperature_off_c"],
        )
        humidity_adjustment = cls._cap_hysteresis_adjustment(
            humidity_score
            * cls._number(
                config, "max_outdoor_humidity_adjustment_percent", 5.0
            ),
            result["effective_exhaust_humidity_on_percent"],
            result["effective_exhaust_humidity_off_percent"],
        )

        cls._shift_threshold_pair(
            result,
            "effective_exhaust_temperature_on_c",
            "effective_exhaust_temperature_off_c",
            exhaust_temperature_adjustment,
        )
        cls._shift_threshold_pair(
            result,
            "effective_exhaust_humidity_on_percent",
            "effective_exhaust_humidity_off_percent",
            humidity_adjustment,
        )

        minimum_soil = (
            min(snapshot.valid_soil_values)
            if snapshot.valid_soil_values
            else None
        )
        critical_soil = cls._number(
            config, "critical_soil_moisture_percent", 20.0
        )
        effective_rain_score = (
            0.0
            if minimum_soil is not None and minimum_soil <= critical_soil
            else rain_score
        )
        soil_adjustment = (
            outdoor_heat_score
            * cls._number(
                config, "max_heat_soil_threshold_increase_percent", 2.0
            )
            - effective_rain_score
            * cls._number(
                config, "max_rain_soil_threshold_reduction_percent", 2.0
            )
        )
        result["effective_soil_moisture_on_percent"] = cls._clamp(
            result["effective_soil_moisture_on_percent"] + soil_adjustment,
            0.0,
            cls._number(config, "soil_moisture_off_percent", 45.0) - 0.1,
        )
        duration_multiplier = (
            1.0
            + outdoor_heat_score
            * cls._number(config, "max_heat_watering_increase", 0.3)
            - effective_rain_score
            * cls._number(config, "max_rain_watering_reduction", 0.3)
        )
        result["watering_duration_multiplier"] = cls._clamp(
            duration_multiplier,
            cls._number(config, "watering_multiplier_min", 0.7),
            cls._number(config, "watering_multiplier_max", 1.3),
        )
        result["weather_exhaust_temperature_adjustment_c"] = (
            exhaust_temperature_adjustment
        )
        result["weather_exhaust_humidity_adjustment_percent"] = (
            humidity_adjustment
        )
        result["weather_soil_threshold_adjustment_percent"] = soil_adjustment

        return result, {
            "weather_used": True,
            "weather_status": "used",
            "weather_age_seconds": round(weather_age, 3),
            "weather_provider": weather.provider,
            "weather_inputs": {
                "outside_temperature_c": weather.outside_temperature_c,
                "outside_humidity_percent": weather.outside_humidity_percent,
                "precipitation_mm": rain_amount,
                "precipitation_probability_percent": rain_probability,
                "inside_absolute_humidity_g_m3": (
                    round(indoor_absolute_humidity, 4)
                    if indoor_absolute_humidity is not None
                    else None
                ),
                "outside_absolute_humidity_g_m3": (
                    round(outdoor_absolute_humidity, 4)
                    if outdoor_absolute_humidity is not None
                    else None
                ),
            },
            "weather_scores": {
                "cooling_opportunity": round(temperature_score, 4),
                "dehumidifying_opportunity": round(humidity_score, 4),
                "outdoor_heat": round(outdoor_heat_score, 4),
                "rain": round(rain_score, 4),
                "rain_effective": round(effective_rain_score, 4),
            },
        }

    @staticmethod
    def _cap_hysteresis_adjustment(
        adjustment: float, on_threshold: float, off_threshold: float
    ) -> float:
        maximum_positive = max(0.0, 2.0 * (on_threshold - off_threshold) - 0.2)
        return min(adjustment, maximum_positive)

    @staticmethod
    def _shift_threshold_pair(
        values: dict[str, float],
        on_key: str,
        off_key: str,
        adjustment: float,
    ) -> None:
        values[on_key] -= adjustment
        values[off_key] -= adjustment * 0.5

    @staticmethod
    def _absolute_humidity_g_m3(
        temperature_c: float, relative_humidity_percent: float
    ) -> float:
        saturation_vapor_pressure_hpa = 6.112 * exp(
            (17.62 * temperature_c) / (243.12 + temperature_c)
        )
        vapor_pressure_hpa = (
            relative_humidity_percent / 100.0
        ) * saturation_vapor_pressure_hpa
        return 216.7 * vapor_pressure_hpa / (273.15 + temperature_c)
