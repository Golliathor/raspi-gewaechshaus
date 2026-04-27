import csv
import json
import os
import time
from datetime import datetime, date

try:
    from smbus2 import SMBus
    SMBUS_AVAILABLE = True
except ModuleNotFoundError:
    SMBus = None
    SMBUS_AVAILABLE = False

from relay_control import init_relays, set_relay, get_state as get_relay_state

BASE_DIR = "/home/grow/gewaechshaus/web"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
COMMAND_PATH = os.path.join(BASE_DIR, "command.json")
CSV_PATH = "/home/grow/gewaechshaus/logs/klima.csv"

LOOP_INTERVAL = 5

DEFAULT_CONFIG = {
    "greenhouse_name": "Raspi-Gewächshaus",

    "exhaust_temp_on_c": 28.0,
    "exhaust_temp_off_c": 25.0,
    "exhaust_humidity_on": 50.0,
    "exhaust_humidity_off": 40.0,

    "circulation_temp_on_c": 24.0,
    "circulation_temp_off_c": 22.0,
    "circulation_humidity_on": 45.0,
    "circulation_humidity_off": 38.0,

    "automation_enabled": True,
    "watering_enabled": True,
    "watering_seconds": 10,
    "watering_fallback_seconds": 40,

    "timelapse_morning": "08:00",
    "timelapse_noon": "13:00",
    "timelapse_evening": "19:00",

    "refresh_seconds": 15,
    "chart_refresh_seconds": 60,
    "chart_points": 200,

    "pcf8591_enabled": True,
    "pcf8591_address": 72,
    "sensor_read_interval_seconds": 30,
    "watering_check_interval_seconds": 300,
    
    "light_sensor": {
    "name": "Lichtsensor",
    "enabled": True,
    "channel": 3,
    "calibration_raw_dark": 255,
    "calibration_raw_bright": 0,
    },
    
    "soil_sensors": [
        {
            "name": "Sensor 1",
            "enabled": True,
            "channel": 0,
            "dry_below_percent": 35,
            "calibration_raw_dry": 210,
            "calibration_raw_wet": 110,
        },
        {
            "name": "Sensor 2",
            "enabled": False,
            "channel": 1,
            "dry_below_percent": 35,
            "calibration_raw_dry": 210,
            "calibration_raw_wet": 110,
        },
        {
            "name": "Sensor 3",
            "enabled": False,
            "channel": 2,
            "dry_below_percent": 35,
            "calibration_raw_dry": 210,
            "calibration_raw_wet": 110,
        },
    ],
}


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def load_config():
    cfg = load_json(CONFIG_PATH, {})
    merged = DEFAULT_CONFIG.copy()
    merged.update(cfg)

    if "soil_sensors" not in cfg or not isinstance(cfg["soil_sensors"], list):
        merged["soil_sensors"] = DEFAULT_CONFIG["soil_sensors"]
    else:
        sensors = []
        defaults = DEFAULT_CONFIG["soil_sensors"]
        for i in range(len(DEFAULT_CONFIG["soil_sensors"])):
            base = defaults[i].copy()
            if i < len(cfg["soil_sensors"]) and isinstance(cfg["soil_sensors"][i], dict):
                base.update(cfg["soil_sensors"][i])
            sensors.append(base)
        merged["soil_sensors"] = sensors

    return merged


def parse_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def parse_timestamp(value):
    if not value:
        return None

    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%y %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def normalize_row(row):
    if not row:
        return None

    if "timestamp" in row:
        ts = parse_timestamp(row.get("timestamp"))
        temp = parse_float(row.get("temperature_c"))
        hum = parse_float(row.get("humidity_percent"))
        if ts and temp is not None and hum is not None:
            return {
                "timestamp": ts.isoformat(),
                "temperature_c": round(temp, 1),
                "humidity_percent": round(hum, 1),
                "label": ts.strftime("%d.%m. %H:%M"),
            }

    if "Date" in row and "Time" in row:
        raw_ts = f"{row.get('Date', '').strip()} {row.get('Time', '').strip()}"
        ts = parse_timestamp(raw_ts)
        raw_temp = str(row.get("Temperature", "")).replace("*C", "").strip()
        raw_hum = str(row.get("Humidity", "")).replace("%", "").strip()
        temp = parse_float(raw_temp)
        hum = parse_float(raw_hum)
        if ts and temp is not None and hum is not None:
            return {
                "timestamp": ts.isoformat(),
                "temperature_c": round(temp, 1),
                "humidity_percent": round(hum, 1),
                "label": ts.strftime("%d.%m. %H:%M"),
            }

    return None


def read_latest_climate():
    if not os.path.exists(CSV_PATH):
        return {
            "timestamp": None,
            "temperature_c": None,
            "humidity_percent": None,
            "label": None,
        }

    latest = None
    try:
        with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                normalized = normalize_row(row)
                if normalized:
                    latest = normalized
    except Exception:
        pass

    return latest or {
        "timestamp": None,
        "temperature_c": None,
        "humidity_percent": None,
        "label": None,
    }


def clamp(value, low, high):
    return max(low, min(high, value))


def raw_to_percent(raw_value, raw_dry, raw_wet):
    if raw_value is None:
        return None

    try:
        raw_value = float(raw_value)
        raw_dry = float(raw_dry)
        raw_wet = float(raw_wet)
    except Exception:
        return None

    if raw_dry == raw_wet:
        return None

    # Kapazitive Sensoren liefern oft: trocken = höherer Wert, nass = niedrigerer Wert
    percent = (raw_dry - raw_value) / (raw_dry - raw_wet) * 100.0
    return round(clamp(percent, 0.0, 100.0), 1)


def read_pcf8591_channel(address, channel):
    if not SMBUS_AVAILABLE:
        return None

    if channel not in (0, 1, 2, 3):
        return None

    try:
        with SMBus(1) as bus:
            control_byte = 0x40 | channel
            bus.write_byte(address, control_byte)
            bus.read_byte(address)  # dummy read
            value = bus.read_byte(address)
            return int(value)
    except Exception:
        return None


def read_soil_sensors(config):
    results = []
    pcf_enabled = bool(config.get("pcf8591_enabled", True))
    address = int(config.get("pcf8591_address", 72))
    sensors = config.get("soil_sensors", [])

    for idx, sensor in enumerate(sensors):
        enabled = bool(sensor.get("enabled", False))
        channel = int(sensor.get("channel", idx))
        raw = None
        percent = None

        if enabled and pcf_enabled:
            raw = read_pcf8591_channel(address, channel)
            percent = raw_to_percent(
                raw,
                sensor.get("calibration_raw_dry", 210),
                sensor.get("calibration_raw_wet", 110),
            )

        results.append({
            "index": idx,
            "name": sensor.get("name", f"Sensor {idx + 1}"),
            "enabled": enabled,
            "channel": channel,
            "raw_value": raw,
            "moisture_percent": percent,
            "dry_below_percent": sensor.get("dry_below_percent", 35),
            "calibration_raw_dry": sensor.get("calibration_raw_dry", 210),
            "calibration_raw_wet": sensor.get("calibration_raw_wet", 110),
        })

    return results


def apply_fan_control(latest_values, config):
    if not config.get("automation_enabled", True):
        return get_relay_state()

    temp = latest_values.get("temperature_c")
    hum = latest_values.get("humidity_percent")

    if temp is None or hum is None:
        return get_relay_state()

    state = get_relay_state()

    exhaust_temp_on = float(config.get("exhaust_temp_on_c", 28.0))
    exhaust_temp_off = float(config.get("exhaust_temp_off_c", 25.0))
    exhaust_hum_on = float(config.get("exhaust_humidity_on", 50.0))
    exhaust_hum_off = float(config.get("exhaust_humidity_off", 40.0))

    circulation_temp_on = float(config.get("circulation_temp_on_c", 24.0))
    circulation_temp_off = float(config.get("circulation_temp_off_c", 22.0))
    circulation_hum_on = float(config.get("circulation_humidity_on", 45.0))
    circulation_hum_off = float(config.get("circulation_humidity_off", 38.0))

    exhaust_on = state["exhaust"]
    if exhaust_on:
        if temp <= exhaust_temp_off and hum <= exhaust_hum_off:
            exhaust_on = False
    else:
        if temp >= exhaust_temp_on or hum >= exhaust_hum_on:
            exhaust_on = True

    circulation_on = state["circulation"]
    if circulation_on:
        if temp <= circulation_temp_off and hum <= circulation_hum_off:
            circulation_on = False
    else:
        if temp >= circulation_temp_on or hum >= circulation_hum_on:
            circulation_on = True

    set_relay("exhaust", exhaust_on)
    set_relay("circulation", circulation_on)

    return get_relay_state()


def water_for_seconds(seconds):
    seconds = max(1, int(seconds))
    set_relay("water_valve", True)
    time.sleep(seconds)
    set_relay("water_valve", False)


def should_water_by_sensor(config, sensor_states):
    enabled_sensors = [s for s in sensor_states if s["enabled"]]
    current_sensors = [s for s in enabled_sensors if s["moisture_percent"] is not None]

    if not enabled_sensors:
        return False, "no_enabled_sensors"

    if not current_sensors:
        return False, "no_current_sensor_values"

    for sensor in current_sensors:
        if sensor["moisture_percent"] <= float(sensor["dry_below_percent"]):
            return True, f"dry:{sensor['name']}"

    return False, "all_ok"


def handle_command(config, state):
    if not os.path.exists(COMMAND_PATH):
        return state

    command = load_json(COMMAND_PATH, {})
    try:
        os.remove(COMMAND_PATH)
    except Exception:
        pass

    cmd_type = command.get("type")

    if cmd_type == "set_relay":
        name = command.get("name")
        on = bool(command.get("on", False))
        if name in {"exhaust", "circulation", "water_valve"}:
            set_relay(name, on)
            state["last_command_result"] = {
                "ok": True,
                "type": cmd_type,
                "name": name,
                "on": on,
                "at": datetime.now().isoformat(timespec="seconds"),
            }

    elif cmd_type == "water_pulse":
        seconds = int(command.get("seconds", config.get("watering_seconds", 10)))
        water_for_seconds(seconds)
        state["last_command_result"] = {
            "ok": True,
            "type": cmd_type,
            "seconds": seconds,
            "at": datetime.now().isoformat(timespec="seconds"),
        }

    elif cmd_type == "set_automation":
        config["automation_enabled"] = bool(command.get("enabled", True))
        save_json(CONFIG_PATH, config)
        state["last_command_result"] = {
            "ok": True,
            "type": cmd_type,
            "enabled": config["automation_enabled"],
            "at": datetime.now().isoformat(timespec="seconds"),
        }

    elif cmd_type == "calibrate_sensor":
        sensor_index = int(command.get("sensor_index", -1))
        calibration_type = command.get("calibration_type")
        raw_value = command.get("raw_value")

        if (
            0 <= sensor_index < len(config["soil_sensors"])
            and calibration_type in {"dry", "wet"}
            and raw_value is not None
        ):
            key = "calibration_raw_dry" if calibration_type == "dry" else "calibration_raw_wet"
            config["soil_sensors"][sensor_index][key] = int(raw_value)
            save_json(CONFIG_PATH, config)
            state["last_command_result"] = {
                "ok": True,
                "type": cmd_type,
                "sensor_index": sensor_index,
                "calibration_type": calibration_type,
                "raw_value": int(raw_value),
                "at": datetime.now().isoformat(timespec="seconds"),
            }

    return state


def main():
    init_relays()

    state = load_json(STATE_PATH, {})
    state.setdefault("relays", get_relay_state())
    state.setdefault("soil_sensors", [])
    state.setdefault("last_sensor_update", None)
    state.setdefault("last_watering_at", None)
    state.setdefault("last_watering_reason", None)
    state.setdefault("last_fallback_watering_day", None)
    state.setdefault("automation_active", True)

    last_sensor_read_ts = 0
    last_watering_check_ts = 0

    while True:
        now = time.time()
        now_iso = datetime.now().isoformat(timespec="seconds")

        config = load_config()
        latest_climate = read_latest_climate()

        state["automation_active"] = bool(config.get("automation_enabled", True))
        state["climate"] = latest_climate

        state = handle_command(config, state)

        if state["automation_active"]:
            apply_fan_control(latest_climate, config)

        if now - last_sensor_read_ts >= int(config.get("sensor_read_interval_seconds", 30)):
            state["soil_sensors"] = read_soil_sensors(config)
            state["last_sensor_update"] = now_iso
            last_sensor_read_ts = now

        if now - last_watering_check_ts >= int(config.get("watering_check_interval_seconds", 300)):
            if config.get("watering_enabled", True):
                water_needed, reason = should_water_by_sensor(config, state["soil_sensors"])

                if water_needed and state["automation_active"]:
                    seconds = int(config.get("watering_seconds", 10))
                    water_for_seconds(seconds)
                    state["last_watering_at"] = datetime.now().isoformat(timespec="seconds")
                    state["last_watering_reason"] = reason

                elif reason == "no_current_sensor_values" and state["automation_active"]:
                    today = date.today().isoformat()
                    if state.get("last_fallback_watering_day") != today:
                        seconds = int(config.get("watering_fallback_seconds", 40))
                        water_for_seconds(seconds)
                        state["last_watering_at"] = datetime.now().isoformat(timespec="seconds")
                        state["last_watering_reason"] = "daily_fallback_no_sensor_values"
                        state["last_fallback_watering_day"] = today

            last_watering_check_ts = now

        state["relays"] = get_relay_state()
        state["last_loop_at"] = now_iso
        save_json(STATE_PATH, state)

        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
