from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from greenhouse.calibration import light_class, raw_to_light_percent, raw_to_percent
from greenhouse.config import ProjectPaths, load_config, load_json, save_json
from greenhouse.controllers import create_controller
from greenhouse.hardware import ADS1115Reader, RaspberryPiRelayOutput
from greenhouse.models import ActuatorState, SensorSnapshot, WeatherSnapshot
from greenhouse.records import append_decision, append_snapshot
from greenhouse.runtime import ControlEngine
from greenhouse.weather import (
    CachedWeatherProvider,
    OpenMeteoWeatherClient,
    weather_from_mapping,
    weather_to_mapping,
)


PATHS = ProjectPaths.from_env()
RUN_ID = os.environ.get("GREENHOUSE_RUN_ID", f"live-{datetime.now():%Y-%m-%d}")


def append_csv_row(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def log_action(event: str, details: str = "", source: str = "automation") -> None:
    append_csv_row(
        PATHS.action_log_path,
        ["timestamp", "event", "source", "details"],
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "source": source,
            "details": details,
        },
    )


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("*C", "").replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    for pattern in (
        None,
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%y %H:%M",
    ):
        try:
            if pattern is None:
                return datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                ).replace(tzinfo=None)
            return datetime.strptime(str(value), pattern)
        except ValueError:
            continue
    return None


def normalize_climate_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("timestamp"):
        timestamp = parse_timestamp(row.get("timestamp"))
        temperature = parse_float(row.get("temperature_c"))
        humidity = parse_float(row.get("humidity_percent"))
    else:
        timestamp = parse_timestamp(f"{row.get('Date', '')} {row.get('Time', '')}")
        temperature = parse_float(row.get("Temperature"))
        humidity = parse_float(row.get("Humidity"))
    if timestamp is None or temperature is None or humidity is None:
        return None
    return {
        "timestamp": timestamp,
        "temperature_c": round(temperature, 1),
        "humidity_percent": round(humidity, 1),
    }


def read_latest_climate() -> dict[str, Any]:
    latest = None
    try:
        with PATHS.climate_csv_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                normalized = normalize_climate_row(row)
                if normalized is not None:
                    latest = normalized
    except OSError:
        pass
    return latest or {
        "timestamp": None,
        "temperature_c": None,
        "humidity_percent": None,
    }


class LocalSensorReader:
    def __init__(self) -> None:
        self._adc: ADS1115Reader | None = None
        self._address: int | None = None

    def _get_adc(self, config: dict[str, Any]) -> ADS1115Reader | None:
        if not config.get("adc_enabled", True):
            return None
        address = int(config.get("adc_address", 72))
        if self._adc is None or self._address != address:
            self._adc = ADS1115Reader(address)
            self._address = address
        return self._adc

    def read(self, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        adc = self._get_adc(config)
        soil_states: list[dict[str, Any]] = []
        for index, sensor in enumerate(config.get("soil_sensors", [])[:3]):
            enabled = bool(sensor.get("enabled", False))
            channel = int(sensor.get("channel", index))
            raw = adc.read_channel(channel) if enabled and adc else None
            percent = raw_to_percent(
                raw,
                sensor.get("calibration_raw_dry", 26000),
                sensor.get("calibration_raw_wet", 12000),
            )
            soil_states.append(
                {
                    "index": index,
                    "name": sensor.get("name", f"Sensor {index + 1}"),
                    "enabled": enabled,
                    "channel": channel,
                    "raw_value": raw,
                    "moisture_percent": percent,
                    "dry_below_percent": sensor.get("dry_below_percent", 35),
                    "calibration_raw_dry": sensor.get(
                        "calibration_raw_dry", 26000
                    ),
                    "calibration_raw_wet": sensor.get(
                        "calibration_raw_wet", 12000
                    ),
                }
            )

        light_config = config.get("light_sensor", {})
        light_enabled = bool(light_config.get("enabled", False))
        light_channel = int(light_config.get("channel", 3))
        light_raw = adc.read_channel(light_channel) if light_enabled and adc else None
        light_percent = raw_to_light_percent(
            light_raw,
            light_config.get("calibration_raw_dark", 26000),
            light_config.get("calibration_raw_bright", 2000),
        )
        light_state = {
            "name": light_config.get("name", "Lichtsensor"),
            "enabled": light_enabled,
            "channel": light_channel,
            "raw_value": light_raw,
            "light_percent": light_percent,
            "light_class": light_class(light_percent),
            "calibration_raw_dark": light_config.get(
                "calibration_raw_dark", 26000
            ),
            "calibration_raw_bright": light_config.get(
                "calibration_raw_bright", 2000
            ),
        }
        return soil_states, light_state


def append_sensor_log(
    timestamp: datetime,
    soil_sensors: list[dict[str, Any]],
    light_sensor: dict[str, Any],
) -> None:
    row: dict[str, Any] = {
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "soil1_raw": None,
        "soil1_percent": None,
        "soil2_raw": None,
        "soil2_percent": None,
        "soil3_raw": None,
        "soil3_percent": None,
        "light_raw": light_sensor.get("raw_value"),
        "light_percent": light_sensor.get("light_percent"),
        "light_class": light_sensor.get("light_class"),
    }
    for index, sensor in enumerate(soil_sensors[:3], start=1):
        row[f"soil{index}_raw"] = sensor.get("raw_value")
        row[f"soil{index}_percent"] = sensor.get("moisture_percent")
    append_csv_row(PATHS.sensor_csv_path, list(row), row)


def build_snapshot(
    now: datetime,
    climate: dict[str, Any],
    soil_sensors: list[dict[str, Any]],
    light_sensor: dict[str, Any],
    sensor_timestamp: datetime | None,
    weather: WeatherSnapshot | None = None,
) -> SensorSnapshot:
    observed_times = [
        value
        for value in (climate.get("timestamp"), sensor_timestamp)
        if isinstance(value, datetime)
    ]
    observed_at = min(observed_times) if observed_times else now
    issues: list[str] = []
    if climate.get("timestamp") is None:
        issues.append("climate_missing")
    if sensor_timestamp is None:
        issues.append("local_sensors_missing")
    return SensorSnapshot(
        timestamp=observed_at,
        temperature_c=climate.get("temperature_c"),
        humidity_percent=climate.get("humidity_percent"),
        soil_moisture_percent=tuple(
            sensor.get("moisture_percent") for sensor in soil_sensors[:3]
        ),
        light_percent=light_sensor.get("light_percent"),
        quality="ok" if not issues else "invalid",
        issues=tuple(issues),
        weather=weather,
    )


def build_weather_service(
    config: dict[str, Any],
    cached_snapshot: WeatherSnapshot | None = None,
) -> CachedWeatherProvider | None:
    weather_config = config.get("weather", {})
    if not weather_config.get("enabled", False):
        return None
    if weather_config.get("provider", "open_meteo") != "open_meteo":
        return None
    client = OpenMeteoWeatherClient(
        latitude=float(weather_config["latitude"]),
        longitude=float(weather_config["longitude"]),
        forecast_horizon_hours=int(
            weather_config.get("forecast_horizon_hours", 6)
        ),
        timeout_seconds=float(
            weather_config.get("request_timeout_seconds", 10)
        ),
        base_url=str(
            weather_config.get(
                "base_url", "https://api.open-meteo.com/v1/forecast"
            )
        ),
    )
    return CachedWeatherProvider(
        client,
        refresh_seconds=float(weather_config.get("refresh_seconds", 900)),
        max_stale_seconds=float(
            weather_config.get("max_stale_seconds", 3600)
        ),
        cached_snapshot=cached_snapshot,
    )


def handle_command(
    config: dict[str, Any],
    state: dict[str, Any],
    engine: ControlEngine,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not PATHS.command_path.exists():
        return config, state
    command = load_json(PATHS.command_path, {})
    try:
        PATHS.command_path.unlink()
    except OSError:
        pass

    command_type = command.get("type")
    result: dict[str, Any] = {
        "ok": False,
        "type": command_type,
        "at": now.isoformat(timespec="seconds"),
    }
    if command_type == "set_relay":
        name = command.get("name")
        if name in {"exhaust", "circulation", "water_valve"}:
            enabled = bool(command.get("on", False))
            if name == "water_valve" and enabled:
                duration = engine.request_manual_watering(
                    float(config.get("watering_seconds", 10)), now
                )
                result.update(
                    {
                        "ok": duration > 0,
                        "name": name,
                        "on": duration > 0,
                        "seconds": duration,
                    }
                )
            else:
                engine.set_manual_relay(name, enabled, now)
                result.update({"ok": True, "name": name, "on": enabled})
            log_action(
                "relay_changed",
                f"name={name}; on={result.get('on')}",
                source="web_command",
            )
    elif command_type == "water_pulse":
        requested = float(command.get("seconds", config.get("watering_seconds", 10)))
        applied = engine.request_manual_watering(requested, now)
        result.update({"ok": applied > 0, "seconds": applied})
        log_action(
            "manual_watering_requested",
            f"requested_seconds={requested}; applied_seconds={applied}",
            source="web_command",
        )
    elif command_type == "set_automation":
        enabled = bool(command.get("enabled", True))
        config["automation_enabled"] = enabled
        save_json(PATHS.config_path, config)
        result.update({"ok": True, "enabled": enabled})
        log_action(
            "automation_changed", f"enabled={enabled}", source="web_command"
        )
    elif command_type == "calibrate_sensor":
        index = int(command.get("sensor_index", -1))
        calibration_type = command.get("calibration_type")
        raw_value = command.get("raw_value")
        if (
            0 <= index < len(config.get("soil_sensors", []))
            and calibration_type in {"dry", "wet"}
            and raw_value is not None
        ):
            key = (
                "calibration_raw_dry"
                if calibration_type == "dry"
                else "calibration_raw_wet"
            )
            config["soil_sensors"][index][key] = int(raw_value)
            save_json(PATHS.config_path, config)
            result.update(
                {
                    "ok": True,
                    "sensor_index": index,
                    "calibration_type": calibration_type,
                    "raw_value": int(raw_value),
                }
            )
            log_action(
                "sensor_calibrated",
                f"sensor_index={index}; type={calibration_type}; raw_value={raw_value}",
                source="web_command",
            )
    state["last_command_result"] = result
    return config, state


def serialize_climate(climate: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": (
            climate["timestamp"].isoformat(timespec="seconds")
            if isinstance(climate.get("timestamp"), datetime)
            else None
        ),
        "temperature_c": climate.get("temperature_c"),
        "humidity_percent": climate.get("humidity_percent"),
    }


def main() -> None:
    relay_output = RaspberryPiRelayOutput()
    sensor_reader = LocalSensorReader()
    state = load_json(PATHS.state_path, {}) or {}
    config = load_config(PATHS.config_path, validate=True)
    controller_id = config["controller"]["active"]
    engine = ControlEngine(
        create_controller(controller_id),
        config,
        initial_state=relay_output.get_state(),
    )
    engine.restore(state)
    last_sensor_read_at: datetime | None = None
    last_watering_check_at: datetime | None = None
    sensor_timestamp: datetime | None = None
    soil_sensors = state.get("soil_sensors", [])
    light_sensor = state.get("light_sensor", {})
    latest_weather = weather_from_mapping(state.get("weather"))
    weather_service: CachedWeatherProvider | None = None
    weather_signature: str | None = None
    last_weather_error: str | None = state.get("last_weather_error")

    try:
        while True:
            now = datetime.now()
            config = load_config(PATHS.config_path, validate=True)
            configured_controller = config["controller"]["active"]
            if configured_controller != controller_id:
                previous_runtime = engine.export_state()
                engine = ControlEngine(
                    create_controller(configured_controller),
                    config,
                    initial_state=engine.state,
                )
                engine.restore({"control_runtime": previous_runtime})
                controller_id = configured_controller
                log_action("controller_changed", f"controller_id={controller_id}")
            else:
                engine.config = config

            config, state = handle_command(config, state, engine, now)
            current_weather_signature = json.dumps(
                config.get("weather", {}),
                sort_keys=True,
                separators=(",", ":"),
            )
            if current_weather_signature != weather_signature:
                weather_service = build_weather_service(config, latest_weather)
                weather_signature = current_weather_signature
            weather_error = None
            if weather_service is not None:
                latest_weather, weather_error = weather_service.get(now)
            else:
                latest_weather = None
            if weather_error and weather_error != last_weather_error:
                log_action("weather_fetch_failed", weather_error)
            elif last_weather_error and not weather_error:
                log_action("weather_fetch_recovered")
            last_weather_error = weather_error

            sensor_interval = float(config.get("sensor_read_interval_seconds", 30))
            if (
                last_sensor_read_at is None
                or (now - last_sensor_read_at).total_seconds() >= sensor_interval
            ):
                soil_sensors, light_sensor = sensor_reader.read(config)
                sensor_timestamp = now
                last_sensor_read_at = now
                append_sensor_log(now, soil_sensors, light_sensor)

            climate = read_latest_climate()
            snapshot = build_snapshot(
                now,
                climate,
                soil_sensors,
                light_sensor,
                sensor_timestamp,
                latest_weather,
            )
            watering_interval = float(
                config.get("watering_check_interval_seconds", 300)
            )
            watering_due = (
                last_watering_check_at is None
                or (now - last_watering_check_at).total_seconds() >= watering_interval
            )
            if watering_due:
                last_watering_check_at = now

            result = engine.step(
                snapshot,
                now=now,
                watering_check_due=watering_due,
            )
            relay_output.set_state(result.state)
            append_snapshot(PATHS.snapshot_log_path, result.snapshot)
            append_decision(PATHS.decision_log_path, result, RUN_ID)

            for transition in result.transitions:
                log_action("actuator_transition", transition)
            for override in result.safety_overrides:
                log_action("safety_override", override)

            state.update(
                {
                    "relays": result.state.as_dict(),
                    "soil_sensors": soil_sensors,
                    "light_sensor": light_sensor,
                    "weather": weather_to_mapping(latest_weather),
                    "weather_available": latest_weather is not None,
                    "last_weather_error": weather_error,
                    "climate": serialize_climate(climate),
                    "automation_active": bool(
                        config.get("automation_enabled", True)
                    ),
                    "active_controller": controller_id,
                    "last_decision_reasons": list(result.requested.reasons),
                    "last_safety_overrides": list(result.safety_overrides),
                    "last_sensor_update": (
                        sensor_timestamp.isoformat(timespec="seconds")
                        if sensor_timestamp
                        else None
                    ),
                    "last_watering_at": (
                        engine.last_watering_at.isoformat(timespec="seconds")
                        if engine.last_watering_at
                        else None
                    ),
                    "last_watering_reason": next(
                        (
                            reason
                            for reason in result.requested.reasons
                            if reason.startswith("watering_")
                        ),
                        state.get("last_watering_reason"),
                    ),
                    "last_loop_at": now.isoformat(timespec="seconds"),
                    "run_id": RUN_ID,
                    "control_runtime": engine.export_state(),
                }
            )
            save_json(PATHS.state_path, state)
            time.sleep(float(config.get("control_loop_interval_seconds", 5)))
    finally:
        relay_output.all_off()


if __name__ == "__main__":
    main()
