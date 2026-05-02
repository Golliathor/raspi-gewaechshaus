import csv
import json
import os
import time
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
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
SENSOR_CSV_PATH = "/home/grow/gewaechshaus/logs/sensoren.csv"
ACTION_LOG_PATH = "/home/grow/gewaechshaus/logs/actions.csv"

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
    "water_flow_ml_per_second": 25,

    "timelapse_morning": "08:00",
    "timelapse_noon": "13:00",
    "timelapse_evening": "19:00",

    "refresh_seconds": 15,
    "chart_refresh_seconds": 60,
    "chart_points": 200,

    "adc_enabled": True,
    "adc_type": "ADS1115",
    "adc_address": 72,
    "sensor_read_interval_seconds": 30,
    "watering_check_interval_seconds": 300,
    
    "light_sensor": {
    "name": "Lichtsensor",
    "enabled": True,
    "channel": 3,
    "calibration_raw_dark": 26000,
    "calibration_raw_bright": 2000
    },
    
    "soil_sensors": [
        {
            "name": "Sensor 1",
            "enabled": True,
            "channel": 0,
            "dry_below_percent": 35,
            "calibration_raw_dry": 26000,
            "calibration_raw_wet": 12000
        },
        {
            "name": "Sensor 2",
            "enabled": False,
            "channel": 1,
            "dry_below_percent": 35,
            "calibration_raw_dry": 26000,
            "calibration_raw_wet": 12000
        },
        {
            "name": "Sensor 3",
            "enabled": False,
            "channel": 2,
            "dry_below_percent": 35,
            "calibration_raw_dry": 26000,
            "calibration_raw_wet": 12000
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

def append_csv_row(path, fieldnames, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    file_exists = os.path.exists(path)

    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)


def log_action(event, details="", source="automation"):
    append_csv_row(
        ACTION_LOG_PATH,
        ["timestamp", "event", "source", "details"],
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "source": source,
            "details": details,
        },
    )


def light_class_from_percent(percent):
    if percent is None:
        return None
    if percent >= 80:
        return "direkte_sonne"
    if percent >= 50:
        return "hell"
    if percent >= 25:
        return "bewoelkt_oder_schatten"
    return "dunkel"


def log_sensor_values(sensor_states, light_sensor=None):
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),

        "soil1_raw": None,
        "soil1_percent": None,
        "soil2_raw": None,
        "soil2_percent": None,
        "soil3_raw": None,
        "soil3_percent": None,

        "light_raw": None,
        "light_percent": None,
        "light_class": None,
    }

    for sensor in sensor_states:
        index = int(sensor.get("index", 0)) + 1

        if index not in (1, 2, 3):
            continue

        row[f"soil{index}_raw"] = sensor.get("raw_value")
        row[f"soil{index}_percent"] = sensor.get("moisture_percent")

    if light_sensor:
        light_percent = light_sensor.get("light_percent")
        row["light_raw"] = light_sensor.get("raw_value")
        row["light_percent"] = light_percent
        row["light_class"] = light_class_from_percent(light_percent)

    append_csv_row(
        SENSOR_CSV_PATH,
        [
            "timestamp",
            "soil1_raw",
            "soil1_percent",
            "soil2_raw",
            "soil2_percent",
            "soil3_raw",
            "soil3_percent",
            "light_raw",
            "light_percent",
            "light_class",
        ],
        row,
    )
    
def load_config():
    cfg = load_json(CONFIG_PATH, {})
    merged = DEFAULT_CONFIG.copy()
    merged.update(cfg)
    
    if "light_sensor" not in cfg or not isinstance(cfg["light_sensor"], dict):
        merged["light_sensor"] = DEFAULT_CONFIG["light_sensor"]
    else:
        light_sensor = DEFAULT_CONFIG["light_sensor"].copy()
        light_sensor.update(cfg["light_sensor"])
        merged["light_sensor"] = light_sensor
    
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

def raw_to_light_percent(raw_value, raw_dark, raw_bright):
    if raw_value is None:
        return None
    raw_value = float(raw_value)
    raw_dark = float(raw_dark)
    raw_bright = float(raw_bright)

    if raw_dark == raw_bright:
        return None

    percent = (raw_dark - raw_value) / (raw_dark - raw_bright) * 100.0
    return round(clamp(percent, 0.0, 100.0), 1)
    
def light_class(percent):
    if percent is None:
        return "unbekannt"
    if percent < 10:
        return "dunkel"
    if percent < 30:
        return "wenig_licht"
    if percent < 60:
        return "schatten"
    if percent < 80:
        return "hell"
    if percent < 95:
        return "sehr_hell"
    return "direkte_sonne"


def append_sensor_log(timestamp, soil_sensors, light_sensor):
    os.makedirs(os.path.dirname(SENSOR_CSV_PATH), exist_ok=True)

    row = {
        "timestamp": timestamp,
        "soil1_raw": None,
        "soil1_percent": None,
        "soil2_raw": None,
        "soil2_percent": None,
        "soil3_raw": None,
        "soil3_percent": None,
        "light_raw": light_sensor.get("raw_value"),
        "light_percent": light_sensor.get("light_percent"),
        "light_class": light_class(light_sensor.get("light_percent")),
    }

    for i, sensor in enumerate(soil_sensors[:3], start=1):
        row[f"soil{i}_raw"] = sensor.get("raw_value")
        row[f"soil{i}_percent"] = sensor.get("moisture_percent")

    file_exists = os.path.exists(SENSOR_CSV_PATH)

    with open(SENSOR_CSV_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

def read_light_sensor(config):
    sensor = config.get("light_sensor", {})
    enabled = bool(sensor.get("enabled", False))
    channel = int(sensor.get("channel", 3))

    raw = None
    percent = None

    if enabled and bool(config.get("adc_enabled", True)):
        raw = read_adc_channel(int(config.get("adc_address", 72)), channel)
        percent = raw_to_light_percent(
            raw,
            sensor.get("calibration_raw_dark", 255),
            sensor.get("calibration_raw_bright", 0),
        )

    return {
        "name": sensor.get("name", "Lichtsensor"),
        "enabled": enabled,
        "channel": channel,
        "raw_value": raw,
        "light_percent": percent,
        "calibration_raw_dark": sensor.get("calibration_raw_dark", 255),
        "calibration_raw_bright": sensor.get("calibration_raw_bright", 0),
    }

_ads1115 = None

def get_ads1115(address=0x48):
    global _ads1115
    if _ads1115 is None:
        i2c = busio.I2C(board.SCL, board.SDA)
        _ads1115 = ADS.ADS1115(i2c, address=address)
        _ads1115.gain = 1
    return _ads1115


def read_adc_channel(address, channel):
    try:
        channel = int(channel)
        if channel not in (0, 1, 2, 3):
            return None

        ads = get_ads1115(address)
        chan = AnalogIn(ads, channel)
        return chan.value
    except Exception as e:
        log_action("adc_read_error", f"channel={channel}; error={e}", source="automation")
        return None

def read_soil_sensors(config):
    results = []
    adc_enabled = bool(config.get("adc_enabled", True))
    address = int(config.get("adc_address", 72))
    sensors = config.get("soil_sensors", [])

    for idx, sensor in enumerate(sensors):
        enabled = bool(sensor.get("enabled", False))
        channel = int(sensor.get("channel", idx))
        raw = None
        percent = None

        if enabled and adc_enabled:
            raw = read_adc_channel(int(config.get("adc_address", 72)), channel)
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


def water_for_seconds(seconds, reason="manual_or_automation"):
    seconds = max(1, int(seconds))

    log_action("watering_started", f"seconds={seconds}; reason={reason}")

    set_relay("water_valve", True)
    time.sleep(seconds)
    set_relay("water_valve", False)

    log_action("watering_finished", f"seconds={seconds}; reason={reason}")


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
            log_action("relay_changed", f"name={name}; on={on}", source="web_command")
            state["last_command_result"] = {
                "ok": True,
                "type": cmd_type,
                "name": name,
                "on": on,
                "at": datetime.now().isoformat(timespec="seconds"),
            }

    elif cmd_type == "water_pulse":
        seconds = int(command.get("seconds", config.get("watering_seconds", 10)))
        water_for_seconds(seconds, reason="manual_web_pulse")
        state["last_command_result"] = {
            "ok": True,
            "type": cmd_type,
            "seconds": seconds,
            "at": datetime.now().isoformat(timespec="seconds"),
        }

    elif cmd_type == "set_automation":
        config["automation_enabled"] = bool(command.get("enabled", True))
        save_json(CONFIG_PATH, config)
        log_action(
            "automation_changed",
            f"enabled={config['automation_enabled']}",
            source="web_command",
            )
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
            log_action(
                "sensor_calibrated",
                f"sensor_index={sensor_index}; type={calibration_type}; raw_value={raw_value}",
                source="web_command",
            )
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

        now = time.time()
        now_iso = datetime.now().isoformat(timespec="seconds")

        if now - last_sensor_read_ts >= int(config.get("sensor_read_interval_seconds", 30)):
            state["soil_sensors"] = read_soil_sensors(config)
            state["light_sensor"] = read_light_sensor(config)
            state["light_sensor"]["light_class"] = light_class(
                state["light_sensor"].get("light_percent")
            )

            append_sensor_log(now_iso, state["soil_sensors"], state["light_sensor"])

            state["last_sensor_update"] = now_iso
            last_sensor_read_ts = now

        if now - last_watering_check_ts >= int(config.get("watering_check_interval_seconds", 300)):
            if config.get("watering_enabled", True):
                water_needed, reason = should_water_by_sensor(config, state["soil_sensors"])

                if water_needed and state["automation_active"]:
                    seconds = int(config.get("watering_seconds", 10))
                    water_for_seconds(seconds, reason=reason)
                    state["last_watering_at"] = datetime.now().isoformat(timespec="seconds")
                    state["last_watering_reason"] = reason

                elif reason == "no_current_sensor_values" and state["automation_active"]:
                    today = date.today().isoformat()
                    if state.get("last_fallback_watering_day") != today:
                        seconds = int(config.get("watering_fallback_seconds", 40))
                        water_for_seconds(seconds, reason="daily_fallback_no_sensor_values")
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
