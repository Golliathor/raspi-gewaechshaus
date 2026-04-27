import csv
import json
import os
from datetime import datetime

from relay_control import init_relays, set_relay, get_state as get_relay_state

BASE_DIR = "/home/grow/gewaechshaus/web"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CSV_PATH = "/home/grow/gewaechshaus/logs/klima.csv"

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
    "watering_enabled": False,
    "watering_seconds": 10,

    "timelapse_morning": "08:00",
    "timelapse_noon": "13:00",
    "timelapse_evening": "19:00",

    "refresh_seconds": 15,
    "chart_refresh_seconds": 60,
    "chart_points": 200,
}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    merged = DEFAULT_CONFIG.copy()
    merged.update(data)
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
            }

    return None


def read_latest_values():
    if not os.path.exists(CSV_PATH):
        return {
            "timestamp": None,
            "temperature_c": None,
            "humidity_percent": None,
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
        return {
            "timestamp": None,
            "temperature_c": None,
            "humidity_percent": None,
        }

    return latest or {
        "timestamp": None,
        "temperature_c": None,
        "humidity_percent": None,
    }


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


def apply_automation_once():
    init_relays()
    config = load_config()
    latest = read_latest_values()
    relays = apply_fan_control(latest, config)

    return {
        "ok": True,
        "latest": latest,
        "relays": relays,
        "automation_enabled": config.get("automation_enabled", True),
    }


if __name__ == "__main__":
    result = apply_automation_once()
    print(result)
