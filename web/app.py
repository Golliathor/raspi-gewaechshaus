import csv
import json
import os
import subprocess
import tempfile
import zipfile
from datetime import datetime
from collections import deque

from flask import Flask, jsonify, render_template, request, redirect, url_for, send_file, after_this_request

app = Flask(__name__)

BASE_DIR = "/home/grow/gewaechshaus/web"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
COMMAND_PATH = os.path.join(BASE_DIR, "command.json")
CSV_PATH = "/home/grow/gewaechshaus/logs/klima.csv"
LOG_DIR = "/home/grow/gewaechshaus/logs"
SENSOR_CSV_PATH = "/home/grow/gewaechshaus/logs/sensoren.csv"
TEST_IMAGE_PATH = "/home/grow/gewaechshaus/images/test_capture.jpg"
CONFIG_LOG_PATH = "/home/grow/gewaechshaus/logs/config_aenderungen.csv"
ACTION_LOG_PATH = "/home/grow/gewaechshaus/logs/actions.csv"

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

    "camera_enabled": True,
    "camera_image_dir": "/home/grow/gewaechshaus/images",
    "camera_filename_pattern": "%Y-%m-%d_%H-%M-%S.jpg",
    "camera_width": 1920,
    "camera_height": 1080,
    "camera_quality": 93,
    "camera_rotation": 0,
    "camera_hflip": False,
    "camera_vflip": False,
    "camera_timeout_ms": 1000,
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
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "key", "old_value", "new_value"]
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(rows)
        
def ensure_config():
    os.makedirs(BASE_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        save_json(CONFIG_PATH, DEFAULT_CONFIG)


def load_config():
    ensure_config()
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
        defaults = DEFAULT_CONFIG["soil_sensors"]
        sensors = []
        for i in range(len(defaults)):
            base = defaults[i].copy()
            if i < len(cfg["soil_sensors"]) and isinstance(cfg["soil_sensors"][i], dict):
                base.update(cfg["soil_sensors"][i])
            base["index"] = i
            sensors.append(base)
        merged["soil_sensors"] = sensors

    return merged


def save_config(config, old_config=None):
    if old_config is None:
        old_config = load_json(CONFIG_PATH, {})

    log_config_changes(old_config, config)
    save_json(CONFIG_PATH, config)


def load_state():
    return load_json(STATE_PATH, {
        "relays": {
            "exhaust": False,
            "circulation": False,
            "water_valve": False
        },
        "light_sensor": {},
        "soil_sensors": [],
        "automation_active": True,
        "last_sensor_update": None,
        "last_watering_at": None,
        "last_watering_reason": None,
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
    path = "/home/grow/gewaechshaus/images/latest.jpg"
    if not os.path.exists(path):
        return {
            "path": None,
            "time": None,
        }

    ts = datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")
    return {
        "path": path,
        "time": ts,
    }

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
    os.makedirs("/home/grow/gewaechshaus/images", exist_ok=True)

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
                reader = csv.DictReader(f)
                source_rows = reader
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
def read_action_rows():
    if not os.path.exists(ACTION_LOG_PATH):
        return []

    rows = []
    try:
        with open(ACTION_LOG_PATH, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
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


let climateLightChart = null;
let waterSunChart = null;

async function refreshDailySummaryCharts() {
  const response = await fetch("/api/daily_summary?days=14");
  const data = await response.json();

  const labels = data.map(r => r.date);

  // --- Klima & Licht ---
  const ctx1 = document.getElementById("climateLightChart");
  if (ctx1) {
    const datasets1 = [
      { label: "Ø Temp Tag (°C)", data: data.map(r => r.avg_temp_day_c) },
      { label: "Ø Temp Nacht (°C)", data: data.map(r => r.avg_temp_night_c) },
      { label: "Ø Licht am Tag (%)", data: data.map(r => r.avg_light_day) },
      { label: "Lichtindex (%)", data: data.map(r => r.light_index) }
    ];

    if (!climateLightChart) {
      climateLightChart = new Chart(ctx1, {
        type: "line",
        data: { labels, datasets: datasets1 },
        options: {
          responsive: true,
          interaction: { mode: "index", intersect: false }
        }
      });
    } else {
      climateLightChart.data.labels = labels;
      climateLightChart.data.datasets = datasets1;
      climateLightChart.update();
    }
  }

  // --- Wasser & Sonne ---
  const ctx2 = document.getElementById("waterSunChart");
  if (ctx2) {
    const datasets2 = [
      { label: "Wasser (ml)", data: data.map(r => r.water_ml) },
      { label: "Direkte Sonne (min)", data: data.map(r => r.direct_sun_minutes) }
    ];

    if (!waterSunChart) {
      waterSunChart = new Chart(ctx2, {
        type: "bar",
        data: { labels, datasets: datasets2 },
        options: {
          responsive: true,
          interaction: { mode: "index", intersect: false }
        }
      });
    } else {
      waterSunChart.data.labels = labels;
      waterSunChart.data.datasets = datasets2;
      waterSunChart.update();
    }
  }
}

refreshDailySummaryCharts();
setInterval(refreshDailySummaryCharts, {{ config.chart_refresh_seconds * 1000 }});
    
@app.route("/")
def index():
    config = load_config()
    state = load_state()
    latest_image = get_latest_image_info()

    if not state.get("last_image_time"):
        state["last_image_time"] = latest_image["time"]

    if not state.get("last_image_path"):
        state["last_image_path"] = latest_image["path"]

    latest = latest = state.get("climate", {})

    return render_template(
        "index.html",
        latest=latest,
        config=config,
        relay_states=state.get("relays", {}),
        state=state,
    )

@app.route("/api/daily_summary")
def api_daily_summary():
    days = request.args.get("days", default=14, type=int)
    return jsonify(build_daily_summary(days))

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
            "exhaust_humidity_on",
            "exhaust_humidity_off",
            "circulation_temp_on_c",
            "circulation_temp_off_c",
            "circulation_humidity_on",
            "circulation_humidity_off",
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

        config["camera_image_dir"] = form.get("camera_image_dir", config.get("camera_image_dir", "/home/grow/gewaechshaus/images"))
        config["camera_filename_pattern"] = form.get("camera_filename_pattern", config.get("camera_filename_pattern", "%Y-%m-%d_%H-%M-%S.jpg"))
        adc_address = parse_float(form.get("adc_address"))
        if adc_address is not None:
            config["adc_address"] = int(adc_address)
        
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
        config["light_sensor"]["name"] = form.get("light_sensor_name",config["light_sensor"]["name"])
        config["light_sensor"]["enabled"] = form.get("light_sensor_enabled") == "on"

        for key in ["channel", "calibration_raw_dark", "calibration_raw_bright"]:
            value = parse_float(form.get(f"light_sensor_{key}"))
            if value is not None:
                config["light_sensor"][key] = int(value)
        save_config(config, old_config)

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
    path = "/home/grow/gewaechshaus/images/latest.jpg"
    if os.path.exists(path):
        return send_file(path, mimetype="image/jpeg")
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
        delete=False
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
    image_dir = config.get("camera_image_dir", "/home/grow/gewaechshaus/images")

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
