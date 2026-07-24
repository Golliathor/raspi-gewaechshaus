from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_BASE_DIR = Path("/home/grow/gewaechshaus")

DEFAULT_CONFIG: dict[str, Any] = {
    "greenhouse_name": "Raspi-Gewächshaus",
    "automation_enabled": True,
    "watering_enabled": True,
    "exhaust_temp_on_c": 28.0,
    "exhaust_temp_off_c": 25.0,
    "exhaust_humidity_on": 50.0,
    "exhaust_humidity_off": 40.0,
    "exhaust_min_temp_c": 18.0,
    "circulation_temp_on_c": 24.0,
    "circulation_temp_off_c": 22.0,
    "circulation_humidity_on": 45.0,
    "circulation_humidity_off": 38.0,
    "watering_seconds": 10,
    "watering_fallback_seconds": 40,
    "water_flow_ml_per_second": 25.0,
    "timelapse_morning": "08:00",
    "timelapse_noon": "13:00",
    "timelapse_evening": "19:00",
    "refresh_seconds": 15,
    "chart_refresh_seconds": 60,
    "chart_points": 200,
    "daily_summary_days": 14,
    "daily_summary_cache_max_age_seconds": 300,
    "daily_summary_row_safety_factor": 2,
    "adc_enabled": True,
    "adc_type": "ADS1115",
    "adc_address": 72,
    "sensor_read_interval_seconds": 30,
    "watering_check_interval_seconds": 300,
    "control_loop_interval_seconds": 5,
    "light_sensor": {
        "name": "Lichtsensor",
        "enabled": True,
        "channel": 3,
        "calibration_raw_dark": 26000,
        "calibration_raw_bright": 2000,
    },
    "soil_sensors": [
        {
            "name": "Sensor 1",
            "enabled": True,
            "channel": 0,
            "dry_below_percent": 35,
            "calibration_raw_dry": 26000,
            "calibration_raw_wet": 12000,
        },
        {
            "name": "Sensor 2",
            "enabled": False,
            "channel": 1,
            "dry_below_percent": 35,
            "calibration_raw_dry": 26000,
            "calibration_raw_wet": 12000,
        },
        {
            "name": "Sensor 3",
            "enabled": False,
            "channel": 2,
            "dry_below_percent": 35,
            "calibration_raw_dry": 26000,
            "calibration_raw_wet": 12000,
        },
    ],
    "camera_enabled": True,
    "camera_image_dir": "images",
    "camera_filename_pattern": "%Y-%m-%d_%H-%M-%S.jpg",
    "camera_width": 1920,
    "camera_height": 1080,
    "camera_quality": 93,
    "camera_rotation": 0,
    "camera_hflip": False,
    "camera_vflip": False,
    "camera_timeout_ms": 1000,
    "weather": {
        "enabled": True,
        "provider": "open_meteo",
        "latitude": 50.766778,
        "longitude": 12.979194,
        "forecast_horizon_hours": 6,
        "refresh_seconds": 900,
        "max_stale_seconds": 3600,
        "request_timeout_seconds": 10,
        "base_url": "https://api.open-meteo.com/v1/forecast",
    },
    "controller": {
        "active": "adaptive_weather",
        "history_size": 720,
    },
    "controllers": {
        "adaptive_local": {
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
            "trend_window_seconds": 900.0,
            "trend_minimum_span_seconds": 120.0,
            "trend_lookahead_minutes": 10.0,
            "max_abs_temperature_trend_per_minute": 2.0,
            "max_abs_humidity_trend_per_minute": 10.0,
            "max_abs_soil_trend_per_minute": 10.0,
            "light_adaptation_start_percent": 50.0,
            "light_temperature_reduction_c": 1.5,
            "max_temperature_reduction_c": 3.0,
            "max_humidity_reduction_percent": 10.0,
            "soil_moisture_on_percent": 35.0,
            "soil_moisture_off_percent": 45.0,
            "max_soil_threshold_increase_percent": 5.0,
            "watering_temperature_reference_c": 25.0,
            "watering_base_seconds": 10.0,
            "watering_min_seconds": 5.0,
            "watering_max_seconds": 30.0,
            "watering_cooldown_seconds": 3600.0,
        },
        "adaptive_weather": {
            "weather_max_age_seconds": 3600.0,
            "outdoor_temperature_difference_scale_c": 10.0,
            "absolute_humidity_difference_scale_g_m3": 5.0,
            "max_outdoor_temperature_adjustment_c": 1.5,
            "max_outdoor_humidity_adjustment_percent": 5.0,
            "outdoor_heat_reference_c": 28.0,
            "outdoor_heat_scale_c": 8.0,
            "rain_probability_threshold_percent": 60.0,
            "rain_amount_reference_mm": 5.0,
            "max_heat_soil_threshold_increase_percent": 2.0,
            "max_rain_soil_threshold_reduction_percent": 2.0,
            "max_heat_watering_increase": 0.3,
            "max_rain_watering_reduction": 0.3,
            "watering_multiplier_min": 0.7,
            "watering_multiplier_max": 1.3,
            "critical_soil_moisture_percent": 20.0,
        },
        "legacy": {},
    },
    "safety": {
        "max_watering_pulse_seconds": 60,
        "max_daily_watering_seconds": 180,
        "sensor_stale_after_seconds": 180,
        "safe_state_after_seconds": 600,
    },
    "targets": {
        "temperature_min_c": 18.0,
        "temperature_max_c": 28.0,
        "humidity_min_percent": 40.0,
        "humidity_max_percent": 70.0,
        "soil_moisture_min_percent": 35.0,
        "soil_moisture_max_percent": 70.0,
    },
}


@dataclass(frozen=True)
class ProjectPaths:
    base_dir: Path

    @classmethod
    def from_env(cls, base_dir: str | os.PathLike[str] | None = None) -> "ProjectPaths":
        configured = base_dir or os.environ.get("GREENHOUSE_BASE_DIR")
        return cls(Path(configured).expanduser() if configured else DEFAULT_BASE_DIR)

    @property
    def web_dir(self) -> Path:
        return self.base_dir / "web"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def images_dir(self) -> Path:
        return self.base_dir / "images"

    @property
    def config_path(self) -> Path:
        return self.web_dir / "config.json"

    @property
    def state_path(self) -> Path:
        return self.web_dir / "state.json"

    @property
    def command_path(self) -> Path:
        return self.web_dir / "command.json"

    @property
    def climate_csv_path(self) -> Path:
        return self.logs_dir / "klima.csv"

    @property
    def sensor_csv_path(self) -> Path:
        return self.logs_dir / "sensoren.csv"

    @property
    def action_log_path(self) -> Path:
        return self.logs_dir / "actions.csv"

    @property
    def snapshot_log_path(self) -> Path:
        return self.logs_dir / "control_snapshots.csv"

    @property
    def decision_log_path(self) -> Path:
        return self.logs_dir / "control_decisions.csv"

    def resolve_configured_path(self, value: str | os.PathLike[str]) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.base_dir / path


def _deep_merge(defaults: Any, overrides: Any) -> Any:
    if isinstance(defaults, dict) and isinstance(overrides, Mapping):
        result = copy.deepcopy(defaults)
        for key, value in overrides.items():
            if key in result:
                result[key] = _deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result
    if isinstance(defaults, list) and isinstance(overrides, list):
        result = copy.deepcopy(defaults)
        for index, value in enumerate(overrides[: len(result)]):
            result[index] = _deep_merge(result[index], value)
        return result
    return copy.deepcopy(overrides)


def validate_config(config: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    positive_numbers = (
        "watering_seconds",
        "water_flow_ml_per_second",
        "sensor_read_interval_seconds",
        "watering_check_interval_seconds",
        "control_loop_interval_seconds",
    )
    for key in positive_numbers:
        try:
            if float(config[key]) <= 0:
                errors.append(f"{key} muss größer als 0 sein")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{key} muss eine Zahl sein")

    controller_id = config.get("controller", {}).get("active")
    if not isinstance(controller_id, str) or not controller_id:
        errors.append("controller.active muss gesetzt sein")
    else:
        from greenhouse.controllers.registry import registered_controller_ids

        if controller_id not in registered_controller_ids():
            errors.append(f"controller.active ist unbekannt: {controller_id}")

    adaptive = config.get("controllers", {}).get("adaptive_local", {})
    adaptive_keys = tuple(DEFAULT_CONFIG["controllers"]["adaptive_local"])
    values: dict[str, float] = {}
    for key in adaptive_keys:
        try:
            values[key] = float(adaptive[key])
        except (KeyError, TypeError, ValueError):
            errors.append(f"controllers.adaptive_local.{key} muss eine Zahl sein")

    for key in (
        "exhaust_humidity_on_percent",
        "exhaust_humidity_off_percent",
        "circulation_humidity_on_percent",
        "circulation_humidity_off_percent",
        "light_adaptation_start_percent",
        "max_humidity_reduction_percent",
        "soil_moisture_on_percent",
        "soil_moisture_off_percent",
        "max_soil_threshold_increase_percent",
    ):
        if key in values and not 0 <= values[key] <= 100:
            errors.append(f"controllers.adaptive_local.{key} muss 0–100 sein")

    for key in (
        "exhaust_min_on_seconds",
        "exhaust_min_off_seconds",
        "circulation_min_on_seconds",
        "circulation_min_off_seconds",
        "max_abs_temperature_trend_per_minute",
        "max_abs_humidity_trend_per_minute",
        "max_abs_soil_trend_per_minute",
        "light_temperature_reduction_c",
        "max_temperature_reduction_c",
        "watering_cooldown_seconds",
    ):
        if key in values and values[key] < 0:
            errors.append(
                f"controllers.adaptive_local.{key} darf nicht negativ sein"
            )

    for off_key, on_key in (
        ("exhaust_temperature_off_c", "exhaust_temperature_on_c"),
        ("exhaust_humidity_off_percent", "exhaust_humidity_on_percent"),
        ("circulation_temperature_off_c", "circulation_temperature_on_c"),
        ("circulation_humidity_off_percent", "circulation_humidity_on_percent"),
        ("soil_moisture_on_percent", "soil_moisture_off_percent"),
    ):
        if (
            off_key in values
            and on_key in values
            and values[off_key] >= values[on_key]
        ):
            errors.append(
                f"controllers.adaptive_local: {off_key} "
                f"muss kleiner als {on_key} sein"
            )

    if (
        "trend_minimum_span_seconds" in values
        and "trend_window_seconds" in values
        and (
            values["trend_minimum_span_seconds"] <= 0
            or values["trend_minimum_span_seconds"]
            > values["trend_window_seconds"]
        )
    ):
        errors.append(
            "controllers.adaptive_local.trend_minimum_span_seconds "
            "muss größer als 0 und höchstens so groß wie das Trendfenster sein"
        )
    if values.get("trend_lookahead_minutes", 1) <= 0:
        errors.append(
            "controllers.adaptive_local.trend_lookahead_minutes "
            "muss größer als 0 sein"
        )
    if all(
        key in values
        for key in (
            "watering_min_seconds",
            "watering_base_seconds",
            "watering_max_seconds",
        )
    ) and not (
        0
        < values["watering_min_seconds"]
        <= values["watering_base_seconds"]
        <= values["watering_max_seconds"]
    ):
        errors.append(
            "controllers.adaptive_local: watering_min_seconds <= "
            "watering_base_seconds <= watering_max_seconds muss gelten"
        )
    if all(
        key in values
        for key in (
            "soil_moisture_on_percent",
            "max_soil_threshold_increase_percent",
            "soil_moisture_off_percent",
        )
    ) and (
        values["soil_moisture_on_percent"]
        + values["max_soil_threshold_increase_percent"]
        >= values["soil_moisture_off_percent"]
    ):
        errors.append(
            "controllers.adaptive_local: adaptive Bodenfeuchte-EIN-Grenze "
            "muss unter der AUS-Grenze bleiben"
        )
    if all(
        key in values
        for key in (
            "max_temperature_reduction_c",
            "exhaust_temperature_on_c",
            "exhaust_temperature_off_c",
            "circulation_temperature_on_c",
            "circulation_temperature_off_c",
        )
    ):
        smallest_gap = min(
            values["exhaust_temperature_on_c"]
            - values["exhaust_temperature_off_c"],
            values["circulation_temperature_on_c"]
            - values["circulation_temperature_off_c"],
        )
        if values["max_temperature_reduction_c"] >= 2 * smallest_gap:
            errors.append(
                "controllers.adaptive_local.max_temperature_reduction_c "
                "würde die Temperaturhysterese aufheben"
            )
    if all(
        key in values
        for key in (
            "max_humidity_reduction_percent",
            "exhaust_humidity_on_percent",
            "exhaust_humidity_off_percent",
            "circulation_humidity_on_percent",
            "circulation_humidity_off_percent",
        )
    ):
        smallest_gap = min(
            values["exhaust_humidity_on_percent"]
            - values["exhaust_humidity_off_percent"],
            values["circulation_humidity_on_percent"]
            - values["circulation_humidity_off_percent"],
        )
        if values["max_humidity_reduction_percent"] >= 2 * smallest_gap:
            errors.append(
                "controllers.adaptive_local.max_humidity_reduction_percent "
                "würde die Feuchtehysterese aufheben"
            )

    adaptive_weather = config.get("controllers", {}).get(
        "adaptive_weather", {}
    )
    weather_values: dict[str, float] = {}
    for key in DEFAULT_CONFIG["controllers"]["adaptive_weather"]:
        try:
            weather_values[key] = float(adaptive_weather[key])
        except (KeyError, TypeError, ValueError):
            errors.append(
                f"controllers.adaptive_weather.{key} muss eine Zahl sein"
            )

    for key in (
        "rain_probability_threshold_percent",
        "critical_soil_moisture_percent",
    ):
        if key in weather_values and not 0 <= weather_values[key] <= 100:
            errors.append(
                f"controllers.adaptive_weather.{key} muss 0–100 sein"
            )
    for key, value in weather_values.items():
        if key not in {"outdoor_heat_reference_c"} and value < 0:
            errors.append(
                f"controllers.adaptive_weather.{key} darf nicht negativ sein"
            )
    if (
        "watering_multiplier_min" in weather_values
        and "watering_multiplier_max" in weather_values
        and not (
            0
            < weather_values["watering_multiplier_min"]
            <= 1.0
            <= weather_values["watering_multiplier_max"]
        )
    ):
        errors.append(
            "controllers.adaptive_weather: watering_multiplier_min <= 1 "
            "<= watering_multiplier_max muss gelten"
        )

    weather = config.get("weather", {})
    if weather.get("provider", "open_meteo") != "open_meteo":
        errors.append("weather.provider muss open_meteo sein")
    for key in (
        "forecast_horizon_hours",
        "refresh_seconds",
        "max_stale_seconds",
        "request_timeout_seconds",
    ):
        try:
            if float(weather[key]) <= 0:
                errors.append(f"weather.{key} muss größer als 0 sein")
        except (KeyError, TypeError, ValueError):
            errors.append(f"weather.{key} muss eine Zahl sein")
    try:
        horizon = float(weather["forecast_horizon_hours"])
        if horizon > 48:
            errors.append(
                "weather.forecast_horizon_hours darf höchstens 48 sein"
            )
    except (KeyError, TypeError, ValueError):
        pass
    try:
        if float(weather["max_stale_seconds"]) < float(
            weather["refresh_seconds"]
        ):
            errors.append(
                "weather.max_stale_seconds muss mindestens "
                "weather.refresh_seconds entsprechen"
            )
    except (KeyError, TypeError, ValueError):
        pass
    if not str(weather.get("base_url", "")).startswith("https://"):
        errors.append("weather.base_url muss eine HTTPS-URL sein")
    if weather.get("enabled", False):
        try:
            latitude = float(weather["latitude"])
            if not -90 <= latitude <= 90:
                errors.append("weather.latitude muss zwischen -90 und 90 liegen")
        except (KeyError, TypeError, ValueError):
            errors.append("weather.latitude muss für Wetterabrufe gesetzt sein")
        try:
            longitude = float(weather["longitude"])
            if not -180 <= longitude <= 180:
                errors.append(
                    "weather.longitude muss zwischen -180 und 180 liegen"
                )
        except (KeyError, TypeError, ValueError):
            errors.append("weather.longitude muss für Wetterabrufe gesetzt sein")

    safety = config.get("safety", {})
    try:
        stale = float(safety["sensor_stale_after_seconds"])
        safe = float(safety["safe_state_after_seconds"])
        maximum_pulse = float(safety["max_watering_pulse_seconds"])
        daily_limit = float(safety["max_daily_watering_seconds"])
        if stale <= 0 or safe < stale:
            errors.append(
                "safety.safe_state_after_seconds muss mindestens "
                "sensor_stale_after_seconds entsprechen"
            )
        if maximum_pulse <= 0 or daily_limit < maximum_pulse:
            errors.append(
                "Das tägliche Bewässerungslimit muss mindestens einem "
                "maximalen Wasserimpuls entsprechen"
            )
    except (KeyError, TypeError, ValueError):
        errors.append("Safety-Zeitgrenzen müssen Zahlen sein")

    targets = config.get("targets", {})
    for minimum_key, maximum_key in (
        ("temperature_min_c", "temperature_max_c"),
        ("humidity_min_percent", "humidity_max_percent"),
        ("soil_moisture_min_percent", "soil_moisture_max_percent"),
    ):
        try:
            if float(targets[minimum_key]) >= float(targets[maximum_key]):
                errors.append(f"targets.{minimum_key} muss unter {maximum_key} liegen")
        except (KeyError, TypeError, ValueError):
            errors.append(f"targets.{minimum_key}/{maximum_key} müssen Zahlen sein")

    for index, sensor in enumerate(config.get("soil_sensors", [])):
        channel = sensor.get("channel")
        if channel not in (0, 1, 2, 3):
            errors.append(f"soil_sensors[{index}].channel muss zwischen 0 und 3 liegen")

    return errors


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
    temporary_path.replace(path)


def load_config(path: Path | None = None, *, validate: bool = False) -> dict[str, Any]:
    config_path = path or ProjectPaths.from_env().config_path
    overrides = load_json(config_path, {})
    config = _deep_merge(DEFAULT_CONFIG, overrides if isinstance(overrides, dict) else {})
    if validate:
        errors = validate_config(config)
        if errors:
            raise ValueError("; ".join(errors))
    return config


def ensure_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or ProjectPaths.from_env().config_path
    if not config_path.exists():
        save_json(config_path, DEFAULT_CONFIG)
    return load_config(config_path)
