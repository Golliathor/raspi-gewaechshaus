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
from greenhouse.hardware import (
    ADCBatchReading,
    ADS1115Reader,
    RaspberryPiRelayOutput,
)
from greenhouse.influx import InfluxTelemetry
from greenhouse.models import (
    ActuatorState,
    CycleResult,
    SensorSnapshot,
    WeatherSnapshot,
)
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


def record_cycle(
    result: CycleResult,
    telemetry: InfluxTelemetry,
    config: dict[str, Any],
) -> None:
    """Persist the authoritative CSV records before optional telemetry."""

    append_snapshot(PATHS.snapshot_log_path, result.snapshot)
    append_decision(PATHS.decision_log_path, result, RUN_ID)
    try:
        telemetry.submit_cycle(
            result,
            RUN_ID,
            float(config.get("water_flow_ml_per_second", 0.0)),
        )
    except Exception as error:
        # This outer guard deliberately protects the control process even if a
        # future telemetry implementation violates its no-raise contract.
        try:
            log_action(
                "influx_submit_failed",
                type(error).__name__,
                source="influxdb",
            )
        except Exception:
            pass


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


def read_latest_climate(
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = load_json(PATHS.latest_climate_path, None)
    if isinstance(current, dict):
        normalized = normalize_climate_row(current)
        if normalized is not None:
            return normalized
    if isinstance(fallback, dict):
        normalized = normalize_climate_row(fallback)
        if normalized is not None:
            return normalized
    return {
        "timestamp": None,
        "temperature_c": None,
        "humidity_percent": None,
    }


class LocalSensorReader:
    def __init__(
        self,
        initial_soil_states: list[dict[str, Any]] | None = None,
        *,
        initial_adc_address: int | None = None,
    ) -> None:
        self._adc: ADS1115Reader | None = None
        self._address = initial_adc_address
        self._soil_filtered_percent: dict[int, float] = {}
        self._soil_filter_signatures: dict[int, tuple[int, int, int, int]] = {}
        for fallback_index, sensor in enumerate(initial_soil_states or []):
            try:
                index = int(sensor.get("index", fallback_index))
                value = sensor.get("moisture_percent")
                if value is None or not sensor.get("enabled", False):
                    continue
                self._soil_filtered_percent[index] = float(value)
                self._soil_filter_signatures[index] = self._sensor_signature(
                    sensor,
                    index,
                    initial_adc_address,
                )
            except (TypeError, ValueError):
                continue

    @staticmethod
    def _sensor_signature(
        sensor: dict[str, Any],
        fallback_channel: int,
        fallback_address: int | None = None,
    ) -> tuple[int, int, int, int]:
        return (
            int(sensor.get("adc_address", fallback_address or 72)),
            int(sensor.get("channel", fallback_channel)),
            int(sensor.get("calibration_raw_dry", 26000)),
            int(sensor.get("calibration_raw_wet", 12000)),
        )

    def _reset_filters(self) -> None:
        self._soil_filtered_percent.clear()
        self._soil_filter_signatures.clear()

    def _get_adc(self, config: dict[str, Any]) -> ADS1115Reader | None:
        if not config.get("adc_enabled", True):
            self._adc = None
            self._address = None
            self._reset_filters()
            return None
        address = int(config.get("adc_address", 72))
        address_changed = self._address is not None and self._address != address
        if self._adc is None or address_changed:
            self._adc = ADS1115Reader(address)
            self._address = address
            if address_changed:
                self._reset_filters()
        return self._adc

    @staticmethod
    def _read_batch(
        adc: ADS1115Reader | None,
        *,
        enabled: bool,
        channel: int,
        sample_count: int,
        sample_interval_seconds: float,
    ) -> ADCBatchReading:
        if not enabled or adc is None:
            return ADCBatchReading(None, None, None, 0, sample_count)
        return adc.read_channel_batch(
            channel,
            sample_count=sample_count,
            sample_interval_seconds=sample_interval_seconds,
        )

    def _filter_soil_percent(
        self,
        *,
        index: int,
        sensor: dict[str, Any],
        enabled: bool,
        unfiltered_percent: float | None,
        alpha: float,
    ) -> float | None:
        signature = self._sensor_signature(sensor, index, self._address)
        if not enabled:
            self._soil_filtered_percent.pop(index, None)
            self._soil_filter_signatures.pop(index, None)
            return None
        if self._soil_filter_signatures.get(index) != signature:
            self._soil_filtered_percent.pop(index, None)
            self._soil_filter_signatures[index] = signature
        if unfiltered_percent is None:
            return None

        previous = self._soil_filtered_percent.get(index)
        filtered = (
            unfiltered_percent
            if previous is None
            else alpha * unfiltered_percent + (1.0 - alpha) * previous
        )
        filtered = round(filtered, 1)
        self._soil_filtered_percent[index] = filtered
        return filtered

    def read(self, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        adc = self._get_adc(config)
        sample_count = int(config.get("adc_sample_count", 9))
        sample_interval_seconds = (
            float(config.get("adc_sample_interval_ms", 40)) / 1000.0
        )
        alpha = max(0.0001, min(1.0, float(config.get("soil_filter_alpha", 0.2))))
        soil_states: list[dict[str, Any]] = []
        for index, sensor in enumerate(config.get("soil_sensors", [])[:3]):
            enabled = bool(sensor.get("enabled", False))
            channel = int(sensor.get("channel", index))
            batch = self._read_batch(
                adc,
                enabled=enabled,
                channel=channel,
                sample_count=sample_count,
                sample_interval_seconds=sample_interval_seconds,
            )
            unfiltered_percent = raw_to_percent(
                batch.value,
                sensor.get("calibration_raw_dry", 26000),
                sensor.get("calibration_raw_wet", 12000),
            )
            percent = self._filter_soil_percent(
                index=index,
                sensor=sensor,
                enabled=enabled,
                unfiltered_percent=unfiltered_percent,
                alpha=alpha,
            )
            soil_states.append(
                {
                    "index": index,
                    "name": sensor.get("name", f"Sensor {index + 1}"),
                    "enabled": enabled,
                    "adc_address": self._address,
                    "channel": channel,
                    "raw_value": batch.value,
                    "raw_min": batch.minimum,
                    "raw_max": batch.maximum,
                    "raw_span": batch.span,
                    "valid_samples": batch.valid_samples,
                    "requested_samples": batch.requested_samples,
                    "unfiltered_moisture_percent": unfiltered_percent,
                    "moisture_percent": percent,
                    "filter_alpha": alpha,
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
        light_batch = self._read_batch(
            adc,
            enabled=light_enabled,
            channel=light_channel,
            sample_count=sample_count,
            sample_interval_seconds=sample_interval_seconds,
        )
        light_percent = raw_to_light_percent(
            light_batch.value,
            light_config.get("calibration_raw_dark", 26000),
            light_config.get("calibration_raw_bright", 2000),
        )
        light_state = {
            "name": light_config.get("name", "Lichtsensor"),
            "enabled": light_enabled,
            "channel": light_channel,
            "raw_value": light_batch.value,
            "raw_min": light_batch.minimum,
            "raw_max": light_batch.maximum,
            "raw_span": light_batch.span,
            "valid_samples": light_batch.valid_samples,
            "requested_samples": light_batch.requested_samples,
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
        valve_was_active = engine.state.water_valve
        maximum_pulse = max(
            0.0,
            float(
                config.get("safety", {}).get(
                    "max_watering_pulse_seconds", 60
                )
            ),
        )
        applied = engine.request_manual_watering(requested, now)
        limit_reasons: list[str] = []
        if valve_was_active:
            limit_reasons.append("water_valve_already_active")
        if requested > maximum_pulse:
            limit_reasons.append("watering_pulse_limited")
        if not valve_was_active and applied < min(requested, maximum_pulse):
            limit_reasons.append("daily_watering_limit")
        result.update(
            {
                "ok": applied > 0,
                "requested_seconds": requested,
                "applied_seconds": applied,
                "seconds": applied,
                "limited": applied < requested,
                "limit_reasons": limit_reasons,
            }
        )
        log_action(
            "manual_watering_requested",
            (
                f"requested_seconds={requested}; applied_seconds={applied}; "
                f"limit_reasons={'|'.join(limit_reasons)}"
            ),
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


def restorable_soil_states(
    state: dict[str, Any],
    config: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    last_update = parse_timestamp(state.get("last_sensor_update"))
    if last_update is None:
        return []
    maximum_age = max(
        120.0,
        3.0 * float(config.get("sensor_read_interval_seconds", 30)),
    )
    age = (now - last_update).total_seconds()
    soil_states = state.get("soil_sensors", [])
    return (
        soil_states
        if 0 <= age <= maximum_age and isinstance(soil_states, list)
        else []
    )


def main() -> None:
    relay_output = RaspberryPiRelayOutput()
    state = load_json(PATHS.state_path, {}) or {}
    config = load_config(PATHS.config_path, validate=True)
    startup_time = datetime.now()
    sensor_reader = LocalSensorReader(
        restorable_soil_states(state, config, startup_time),
        initial_adc_address=int(config.get("adc_address", 72)),
    )
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
    influx = InfluxTelemetry.from_config(
        {"influxdb": {"enabled": False}},
        start_worker=False,
    )
    influx_signature: str | None = None

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
            current_influx_signature = json.dumps(
                config.get("influxdb", {}),
                sort_keys=True,
                separators=(",", ":"),
            )
            if current_influx_signature != influx_signature:
                previous_influx = influx
                try:
                    influx = InfluxTelemetry.from_config(
                        config,
                        status_callback=lambda event, details: log_action(
                            event,
                            details,
                            source="influxdb",
                        ),
                    )
                except Exception as error:
                    influx = InfluxTelemetry.from_config(
                        {"influxdb": {"enabled": False}},
                        start_worker=False,
                    )
                    try:
                        log_action(
                            "influx_disabled",
                            f"initialization_failed ({type(error).__name__})",
                            source="influxdb",
                        )
                    except Exception:
                        pass
                influx_signature = current_influx_signature
                previous_influx.close(timeout_seconds=0.0)
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

            climate = read_latest_climate(state.get("climate"))
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
            record_cycle(result, influx, config)

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
                    "last_decision_diagnostics": dict(
                        result.requested.diagnostics
                    ),
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
        influx.close(timeout_seconds=1.0)


if __name__ == "__main__":
    main()
