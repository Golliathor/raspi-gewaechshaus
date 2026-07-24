import csv
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from collections import deque
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from flask import Flask, jsonify, render_template, request, redirect, url_for, send_file, after_this_request
from flask_compress import Compress

from greenhouse.config import (
    DEFAULT_CONFIG,
    ProjectPaths,
    ensure_config as ensure_project_config,
    load_config as load_project_config,
    validate_config,
)

app = Flask(__name__)

app.config["COMPRESS_MIMETYPES"] = [
    "text/html",
    "text/css",
    "application/json",
    "application/javascript",
]
app.config["COMPRESS_LEVEL"] = 6
Compress(app)

PATHS = ProjectPaths.from_env()
BASE_DIR = str(PATHS.web_dir)
CONFIG_PATH = str(PATHS.config_path)
STATE_PATH = str(PATHS.state_path)
COMMAND_PATH = str(PATHS.command_path)
CSV_PATH = str(PATHS.climate_csv_path)
LOG_DIR = str(PATHS.logs_dir)
SENSOR_CSV_PATH = str(PATHS.sensor_csv_path)
TEST_IMAGE_PATH = str(PATHS.images_dir / "test_capture.jpg")
CONFIG_LOG_PATH = str(PATHS.logs_dir / "config_aenderungen.csv")
ACTION_LOG_PATH = str(PATHS.action_log_path)
DAILY_SUMMARY_JSON_PATH = str(PATHS.logs_dir / "daily_summary.json")


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


def flatten_dict(data, prefix=""):
    items = {}

    if isinstance(data, dict):
        for key, value in data.items():
            new_key = f"{prefix}.{key}" if prefix else key
            items.update(flatten_dict(value, new_key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            new_key = f"{prefix}.{index}" if prefix else str(index)
            items.update(flatten_dict(value, new_key))
    else:
        items[prefix] = data

    return items


def log_config_changes(old_config, new_config):
    old_flat = flatten_dict(old_config)
    new_flat = flatten_dict(new_config)
    changed_keys = sorted(set(old_flat.keys()) | set(new_flat.keys()))
    rows = []
    timestamp = datetime.now().isoformat(timespec="seconds")

    for key in changed_keys:
        old_value = old_flat.get(key)
        new_value = new_flat.get(key)

        if old_value != new_value:
            rows.append({
                "timestamp": timestamp,
                "key": key,
                "old_value": old_value,
                "new_value": new_value,
            })

    if not rows:
        return

    os.makedirs(os.path.dirname(CONFIG_LOG_PATH), exist_ok=True)
    file_exists = os.path.exists(CONFIG_LOG_PATH)

    with open(CONFIG_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "key", "old_value", "new_value"])

        if not file_exists:
            writer.writeheader()

        writer.writerows(rows)


def ensure_config():
    ensure_project_config(Path(CONFIG_PATH))


def load_config():
    ensure_config()
    config = load_project_config(Path(CONFIG_PATH))
    for index, sensor in enumerate(config["soil_sensors"]):
        sensor["index"] = index
    return config


def save_config(config, old_config=None):
    if old_config is None:
        old_config = load_json(CONFIG_PATH, {})

    errors = validate_config(config)
    if errors:
        raise ValueError("; ".join(errors))
    log_config_changes(old_config, config)
    save_json(CONFIG_PATH, config)


def load_state():
    return load_json(STATE_PATH, {
        "relays": {
            "exhaust": False,
            "circulation": False,
            "water_valve": False,
        },
        "light_sensor": {},
        "soil_sensors": [],
        "automation_active": True,
        "last_sensor_update": None,
        "last_watering_at": None,
        "last_watering_reason": None,
        "active_controller": "legacy",
        "last_decision_reasons": [],
        "last_safety_overrides": [],
        "run_id": None,
    })


def write_command(command):
    save_json(COMMAND_PATH, command)


def parse_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def get_latest_image_info():
    path = PATHS.images_dir / "latest.jpg"
    if not path.exists():
        return {"path": None, "time": None}

    ts = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    return {"path": str(path), "time": ts}


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


def capture_test_image(config):
    PATHS.images_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rpicam-still",
        "-o", TEST_IMAGE_PATH,
        "--width", str(int(config.get("camera_width", 1920))),
        "--height", str(int(config.get("camera_height", 1080))),
        "--quality", str(int(config.get("camera_quality", 93))),
        "--timeout", str(int(config.get("camera_timeout_ms", 1000))),
        "--nopreview",
    ]

    rotation = int(config.get("camera_rotation", 0))
    if rotation in (0, 180):
        cmd.extend(["--rotation", str(rotation)])

    if bool(config.get("camera_hflip", False)):
        cmd.append("--hflip")

    if bool(config.get("camera_vflip", False)):
        cmd.append("--vflip")

    subprocess.run(cmd, check=True)
    return TEST_IMAGE_PATH


def normalize_row(row):
    if not row:
        return None

    if "timestamp" in row:
        ts = parse_timestamp(row.get("timestamp"))
        temp = parse_float(row.get("temperature_c"))
        hum = parse_float(row.get("humidity_percent"))
        if ts and temp is not None and hum is not None:
            return {
                "timestamp": ts,
                "timestamp_iso": ts.isoformat(),
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
                "timestamp": ts,
                "timestamp_iso": ts.isoformat(),
                "temperature_c": round(temp, 1),
                "humidity_percent": round(hum, 1),
                "label": ts.strftime("%d.%m. %H:%M"),
            }

    return None


def read_csv_rows(limit=None):
    if not os.path.exists(CSV_PATH):
        return []

    rows = []
    try:
        with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
            if limit is None:
                source_rows = csv.DictReader(f)
            else:
                header = f.readline()
                last_lines = deque(f, maxlen=limit)
                source_rows = csv.DictReader([header] + list(last_lines))

            for row in source_rows:
                normalized = normalize_row(row)
                if normalized:
                    rows.append(normalized)
    except Exception:
        return []

    return rows


def read_latest_values():
    rows = read_csv_rows(limit=1)
    if not rows:
        return {
            "timestamp": None,
            "temperature_c": None,
            "humidity_percent": None,
            "label": None,
        }

    row = rows[-1]
    return {
        "timestamp": row["timestamp_iso"],
        "temperature_c": row["temperature_c"],
        "humidity_percent": row["humidity_percent"],
        "label": row["label"],
    }


def build_chart_data(points):
    rows = read_csv_rows(limit=points)
    return {
        "labels": [r["label"] for r in rows],
        "temperature": [r["temperature_c"] for r in rows],
        "humidity": [r["humidity_percent"] for r in rows],
    }


def read_sensor_csv_rows(limit=None):
    if not os.path.exists(SENSOR_CSV_PATH):
        return []

    rows = []
    try:
        with open(SENSOR_CSV_PATH, "r", encoding="utf-8", newline="") as f:
            if limit is None:
                source_rows = csv.DictReader(f)
            else:
                header = f.readline()
                last_lines = deque(f, maxlen=limit)
                source_rows = csv.DictReader([header] + list(last_lines))

            for row in source_rows:
                ts = parse_timestamp(row.get("timestamp"))
                if not ts:
                    continue

                rows.append({
                    "timestamp": ts,
                    "label": ts.strftime("%d.%m. %H:%M"),
                    "soil1": parse_float(row.get("soil1_percent")),
                    "soil2": parse_float(row.get("soil2_percent")),
                    "soil3": parse_float(row.get("soil3_percent")),
                    "light": parse_float(row.get("light_percent")),
                    "light_raw": parse_float(row.get("light_raw")),
                    "light_class": row.get("light_class"),
                })
    except Exception:
        return []

    return rows


def build_sensor_chart_data(points):
    rows = read_sensor_csv_rows(limit=points)
    return {
        "labels": [r["label"] for r in rows],
        "soil1": [r["soil1"] for r in rows],
        "soil2": [r["soil2"] for r in rows],
        "soil3": [r["soil3"] for r in rows],
        "light": [r["light"] for r in rows],
        "light_raw": [r["light_raw"] for r in rows],
        "light_class": [r["light_class"] for r in rows],
    }


def read_action_rows(limit=None):
    if not os.path.exists(ACTION_LOG_PATH):
        return []

    rows = []

    try:
        with open(ACTION_LOG_PATH, "r", encoding="utf-8", newline="") as f:
            if limit is None:
                source_rows = csv.DictReader(f)
            else:
                header = f.readline()
                last_lines = deque(f, maxlen=limit)
                source_rows = csv.DictReader([header] + list(last_lines))

            for row in source_rows:
                ts = parse_timestamp(row.get("timestamp"))
                if not ts:
                    continue

                rows.append({
                    "timestamp": ts,
                    "event": row.get("event", ""),
                    "source": row.get("source", ""),
                    "details": row.get("details", ""),
                })

    except Exception:
        return []

    return rows

def estimate_log_rows_for_days(days, interval_seconds, safety_factor=2, minimum=500):
    try:
        days = int(days)
        interval_seconds = int(interval_seconds)
        safety_factor = int(safety_factor)
    except Exception:
        return minimum

    if days < 1:
        days = 1

    if interval_seconds < 1:
        interval_seconds = 30

    if safety_factor < 1:
        safety_factor = 1

    rows = int((days * 24 * 60 * 60 / interval_seconds) * safety_factor)
    return max(minimum, rows)
    
def build_daily_summary(days=14):
    config = load_config()

    interval_seconds = int(config.get("sensor_read_interval_seconds", 30))
    safety_factor = int(config.get("daily_summary_row_safety_factor", 2))

    row_limit = estimate_log_rows_for_days(
        days=days,
        interval_seconds=interval_seconds,
        safety_factor=safety_factor,
    )

    climate_rows = read_csv_rows(limit=row_limit)
    sensor_rows = read_sensor_csv_rows(limit=row_limit)
    action_rows = read_action_rows(limit=2000)

    flow = float(config.get("water_flow_ml_per_second", 25))
    watering_seconds = float(config.get("watering_seconds", 10))
    interval_min = config.get("sensor_read_interval_seconds", 30) / 60

    summaries = {}

    def empty_summary(date_key):
        return {
            "date": date_key,
            "light_sum_percent_minutes": 0,
            "direct_sun_minutes": 0,
            "temp_day_values": [],
            "temp_night_values": [],
            "humidity_values": [],
            "watering_events": 0,
            "water_ml": 0,
        }

    def day_key(ts):
        return ts.strftime("%Y-%m-%d")

    for row in sensor_rows:
        ts = row.get("timestamp")
        if not ts:
            continue
    
        d = day_key(ts)
        s = summaries.setdefault(d, empty_summary(d))

        light = row.get("light")
        light_class = row.get("light_class", "")

        if light is not None:
            s["light_sum_percent_minutes"] += light * interval_min

        if light_class == "direkte_sonne" or (light is not None and light >= 95):
            s["direct_sun_minutes"] += interval_min

    for r in climate_rows:
        ts = r["timestamp"]
        d = day_key(ts)
        s = summaries.setdefault(d, empty_summary(d))

        hour = ts.hour
        if 7 <= hour < 21:
            s["temp_day_values"].append(r["temperature_c"])
        else:
            s["temp_night_values"].append(r["temperature_c"])
            
        if r.get("humidity_percent") is not None:
            s["humidity_values"].append(r["humidity_percent"])

    for r in action_rows:
        event = r["event"].lower()
        details = r["details"].lower()
        if "water" in event or "watering" in event or "bewässer" in event or "water" in details:
            d = day_key(r["timestamp"])
            s = summaries.setdefault(d, empty_summary(d))
            s["watering_events"] += 1
            s["water_ml"] += watering_seconds * flow

    result = []
    for d in sorted(summaries.keys())[-days:]:
        s = summaries[d]
        day_vals = s.pop("temp_day_values")
        night_vals = s.pop("temp_night_values")
        humidity_vals = s.pop("humidity_values", [])
        
        s["direct_sun_minutes"] = round(s["direct_sun_minutes"], 1)
        s["avg_temp_day_c"] = round(sum(day_vals) / len(day_vals), 1) if day_vals else None
        s["avg_temp_night_c"] = round(sum(night_vals) / len(night_vals), 1) if night_vals else None
        s["avg_humidity_percent"] = round(sum(humidity_vals) / len(humidity_vals), 1) if humidity_vals else None
        s["water_ml"] = round(s["water_ml"], 1)
        
        max_light = 100 * 24 * 60
        s["light_index"] = round(s["light_sum_percent_minutes"] / max_light * 100, 1)
        s.pop("light_sum_percent_minutes", None)
        s.pop("active_minutes", None)


        result.append(s)

    return result
    
def file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def daily_summary_source_signature():
    return {
        "klima_mtime": file_mtime(CSV_PATH),
        "sensoren_mtime": file_mtime(SENSOR_CSV_PATH),
        "actions_mtime": file_mtime(ACTION_LOG_PATH),
        "config_mtime": file_mtime(CONFIG_PATH),
    }


def load_daily_summary_cache(days):
    config = load_config()
    max_age_seconds = int(config.get("daily_summary_cache_max_age_seconds", 300))

    cache = load_json(DAILY_SUMMARY_JSON_PATH, {})

    try:
        created_at = datetime.fromisoformat(cache.get("created_at", ""))
        cache_age_seconds = (datetime.now() - created_at).total_seconds()
    except Exception:
        cache_age_seconds = None

    if (
        cache.get("days") == days
        and cache.get("max_age_seconds") == max_age_seconds
        and isinstance(cache.get("data"), list)
        and cache_age_seconds is not None
        and cache_age_seconds < max_age_seconds
    ):
        return cache["data"]

    data = build_daily_summary(days)

    os.makedirs(os.path.dirname(DAILY_SUMMARY_JSON_PATH), exist_ok=True)
    save_json(
        DAILY_SUMMARY_JSON_PATH,
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "days": days,
            "max_age_seconds": max_age_seconds,
            "data": data,
        },
    )

    return data

@app.route("/")
def index():
    config = load_config()
    state = load_state()
    latest_image = get_latest_image_info()

    if not state.get("last_image_time"):
        state["last_image_time"] = latest_image["time"]

    if not state.get("last_image_path"):
        state["last_image_path"] = latest_image["path"]

    latest = state.get("climate", {})

    return render_template(
        "index.html",
        latest=latest,
        config=config,
        relay_states=state.get("relays", {}),
        state=state,
    )


@app.route("/api/daily_summary")
def api_daily_summary():
    config = load_config()

    default_days = int(config.get("daily_summary_days", 14))
    days = request.args.get("days", default=default_days, type=int)

    if days < 1:
        days = 1
    if days > 365:
        days = 365

    response = jsonify(load_daily_summary_cache(days))
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@app.route("/config", methods=["GET", "POST"])
def config_page():
    config = load_config()
    old_config = json.loads(json.dumps(config))

    if request.method == "POST":
        form = request.form
        config["greenhouse_name"] = form.get("greenhouse_name", config["greenhouse_name"])

        numeric_keys = [
            "exhaust_temp_on_c",
            "exhaust_temp_off_c",
            "exhaust_min_temp_c",
            "exhaust_humidity_on",
            "exhaust_humidity_off",
            "circulation_temp_on_c",
            "circulation_temp_off_c",
            "circulation_humidity_on",
            "circulation_humidity_off",
            "watering_seconds",
            "watering_fallback_seconds",
            "water_flow_ml_per_second",
            "refresh_seconds",
            "chart_refresh_seconds",
            "chart_points",
            "adc_address",
            "sensor_read_interval_seconds",
            "watering_check_interval_seconds",
            "camera_width",
            "camera_height",
            "camera_quality",
            "camera_rotation",
            "camera_timeout_ms",
            "daily_summary_days",
            "daily_summary_cache_max_age_seconds",
            "daily_summary_row_safety_factor",
        ]

        int_keys = {
            "watering_seconds",
            "watering_fallback_seconds",
            "refresh_seconds",
            "chart_refresh_seconds",
            "chart_points",
            "adc_address",
            "sensor_read_interval_seconds",
            "watering_check_interval_seconds",
            "camera_width",
            "camera_height",
            "camera_quality",
            "camera_rotation",
            "camera_timeout_ms",
            "daily_summary_days",
            "daily_summary_cache_max_age_seconds",
            "daily_summary_row_safety_factor",
        }

        for key in numeric_keys:
            value = parse_float(form.get(key))
            if value is not None:
                config[key] = int(value) if key in int_keys else value

        config["automation_enabled"] = form.get("automation_enabled") == "on"
        config["watering_enabled"] = form.get("watering_enabled") == "on"
        config["adc_enabled"] = form.get("adc_enabled") == "on"
        config["adc_type"] = form.get("adc_type", "ADS1115")
        config["camera_enabled"] = form.get("camera_enabled") == "on"
        config["camera_hflip"] = form.get("camera_hflip") == "on"
        config["camera_vflip"] = form.get("camera_vflip") == "on"

        config["timelapse_morning"] = form.get("timelapse_morning", config["timelapse_morning"])
        config["timelapse_noon"] = form.get("timelapse_noon", config["timelapse_noon"])
        config["timelapse_evening"] = form.get("timelapse_evening", config["timelapse_evening"])

        config["camera_image_dir"] = form.get(
            "camera_image_dir",
            config.get("camera_image_dir", "images"),
        )
        config["camera_filename_pattern"] = form.get(
            "camera_filename_pattern",
            config.get("camera_filename_pattern", "%Y-%m-%d_%H-%M-%S.jpg"),
        )

        sensors = []
        for i in range(len(DEFAULT_CONFIG["soil_sensors"])):
            base = config["soil_sensors"][i].copy()
            base["name"] = form.get(f"sensor_{i}_name", base["name"])
            base["enabled"] = form.get(f"sensor_{i}_enabled") == "on"

            for sensor_key in ["channel", "dry_below_percent", "calibration_raw_dry", "calibration_raw_wet"]:
                sensor_value = parse_float(form.get(f"sensor_{i}_{sensor_key}"))
                if sensor_value is not None:
                    base[sensor_key] = int(sensor_value)

            sensors.append(base)

        config["soil_sensors"] = sensors

        config["light_sensor"]["name"] = form.get(
            "light_sensor_name",
            config["light_sensor"]["name"],
        )
        config["light_sensor"]["enabled"] = form.get("light_sensor_enabled") == "on"

        for key in ["channel", "calibration_raw_dark", "calibration_raw_bright"]:
            value = parse_float(form.get(f"light_sensor_{key}"))
            if value is not None:
                config["light_sensor"][key] = int(value)

        for section, fields in {
            "safety": (
                "max_watering_pulse_seconds",
                "max_daily_watering_seconds",
                "sensor_stale_after_seconds",
                "safe_state_after_seconds",
            ),
            "targets": (
                "temperature_min_c",
                "temperature_max_c",
                "humidity_min_percent",
                "humidity_max_percent",
                "soil_moisture_min_percent",
                "soil_moisture_max_percent",
            ),
        }.items():
            for key in fields:
                value = parse_float(form.get(f"{section}_{key}"))
                if value is not None:
                    config[section][key] = value

        try:
            save_config(config, old_config)
        except ValueError as error:
            state = load_state()
            latest = read_latest_values()
            return render_template(
                "config.html",
                config=config,
                state=state,
                latest=latest,
                config_error=str(error),
            ), 400
        return redirect(url_for("config_page"))

    state = load_state()
    latest = read_latest_values()

    return render_template(
        "config.html",
        config=config,
        state=state,
        latest=latest,
    )


@app.route("/calibration")
def calibration_page():
    config = load_config()
    state = load_state()
    return render_template("calibration.html", config=config, state=state)


@app.route("/api/status")
def api_status():
    config = load_config()
    state = load_state()
    latest = read_latest_values()

    return jsonify({
        "latest": latest,
        "config": config,
        "relays": state.get("relays", {}),
        "automation_active": state.get("automation_active", config.get("automation_enabled", True)),
        "light_sensor": state.get("light_sensor", {}),
        "soil_sensors": state.get("soil_sensors", []),
        "last_sensor_update": state.get("last_sensor_update"),
        "last_watering_at": state.get("last_watering_at"),
        "last_watering_reason": state.get("last_watering_reason"),
        "active_controller": state.get(
            "active_controller", config.get("controller", {}).get("active", "legacy")
        ),
        "last_decision_reasons": state.get("last_decision_reasons", []),
        "last_safety_overrides": state.get("last_safety_overrides", []),
        "run_id": state.get("run_id"),
    })


@app.route("/api/chart")
def api_chart():
    config = load_config()
    points = request.args.get("points", default=config.get("chart_points", 200), type=int)
    return jsonify(build_chart_data(points))


@app.route("/api/sensor_chart")
def api_sensor_chart():
    config = load_config()
    points = request.args.get("points", default=config.get("chart_points", 200), type=int)
    return jsonify(build_sensor_chart_data(points))


@app.route("/api/relays/<name>", methods=["POST"])
def api_set_relay(name):
    if name not in {"exhaust", "circulation", "water_valve"}:
        return jsonify({"error": "unbekanntes relais"}), 400

    data = request.get_json(silent=True) or {}
    write_command({
        "type": "set_relay",
        "name": name,
        "on": bool(data.get("on", False)),
    })
    return jsonify({"ok": True})


@app.route("/api/automation/toggle", methods=["POST"])
def api_toggle_automation():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    write_command({
        "type": "set_automation",
        "enabled": enabled,
    })
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/water_pulse", methods=["POST"])
def api_water_pulse():
    data = request.get_json(silent=True) or {}
    seconds = int(data.get("seconds", 10))
    write_command({
        "type": "water_pulse",
        "seconds": seconds,
    })
    return jsonify({"ok": True, "seconds": seconds})


@app.route("/api/calibrate/<int:sensor_index>", methods=["POST"])
def api_calibrate(sensor_index):
    state = load_state()
    sensors = state.get("soil_sensors", [])
    if sensor_index < 0 or sensor_index >= len(sensors):
        return jsonify({"error": "ungueltiger sensor"}), 400

    data = request.get_json(silent=True) or {}
    calibration_type = data.get("calibration_type")
    if calibration_type not in {"dry", "wet"}:
        return jsonify({"error": "ungueltiger typ"}), 400

    raw_value = sensors[sensor_index].get("raw_value")
    if raw_value is None:
        return jsonify({"error": "kein aktueller rohwert"}), 400

    write_command({
        "type": "calibrate_sensor",
        "sensor_index": sensor_index,
        "calibration_type": calibration_type,
        "raw_value": raw_value,
    })
    return jsonify({"ok": True, "sensor_index": sensor_index, "raw_value": raw_value})


@app.route("/latest.jpg")
def latest_image():
    path = PATHS.images_dir / "latest.jpg"
    if path.exists():
        return send_file(str(path), mimetype="image/jpeg")
    return ("Kein Bild vorhanden", 404)


@app.route("/api/camera/test_capture", methods=["POST"])
def api_camera_test_capture():
    config = load_config()
    try:
        path = capture_test_image(config)
        return jsonify({
            "ok": True,
            "path": path,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/test_capture.jpg")
def test_capture_image():
    if os.path.exists(TEST_IMAGE_PATH):
        return send_file(TEST_IMAGE_PATH, mimetype="image/jpeg")
    return ("Kein Testbild vorhanden", 404)


def create_zip_from_folder(folder_path, zip_prefix):
    if not os.path.isdir(folder_path):
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tmp = tempfile.NamedTemporaryFile(
        suffix=f"_{zip_prefix}_{timestamp}.zip",
        delete=False,
    )
    tmp.close()

    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(folder_path):
            for filename in files:
                file_path = os.path.join(root, filename)

                if not os.path.isfile(file_path):
                    continue

                arcname = os.path.relpath(file_path, folder_path)
                zipf.write(file_path, arcname)

    return tmp.name


@app.route("/download/images")
def download_images():
    config = load_config()
    image_dir = str(
        PATHS.resolve_configured_path(config.get("camera_image_dir", "images"))
    )

    zip_path = create_zip_from_folder(image_dir, "bilder")
    if zip_path is None:
        return ("Bildordner nicht gefunden", 404)

    @after_this_request
    def cleanup(response):
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return response

    return send_file(
        zip_path,
        as_attachment=True,
        download_name="gewaechshaus_bilder.zip",
        mimetype="application/zip",
    )


@app.route("/download/logs")
def download_logs():
    zip_path = create_zip_from_folder(LOG_DIR, "logs")
    if zip_path is None:
        return ("Logordner nicht gefunden", 404)

    @after_this_request
    def cleanup(response):
        try:
            os.remove(zip_path)
        except Exception:
            pass
        return response

    return send_file(
        zip_path,
        as_attachment=True,
        download_name="gewaechshaus_logs.zip",
        mimetype="application/zip",
    )


if __name__ == "__main__":
    ensure_config()
    app.run(host="0.0.0.0", port=8080, debug=False)
