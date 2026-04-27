import json
import os
import subprocess
import time
from datetime import datetime

BASE_DIR = "/home/grow/gewaechshaus/web"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")

IMAGE_DIR = "/home/grow/gewaechshaus/images"
LATEST_IMAGE_PATH = os.path.join(IMAGE_DIR, "latest.jpg")

LOOP_INTERVAL = 20  # Sekunden

DEFAULT_CONFIG = {
    "camera_enabled": True,
    "camera_image_dir": IMAGE_DIR,
    "camera_filename_pattern": "%Y-%m-%d_%H-%M-%S.jpg",

    "timelapse_morning": "08:00",
    "timelapse_noon": "13:00",
    "timelapse_evening": "19:00",

    "camera_width": 1920,
    "camera_height": 1080,
    "camera_quality": 93,
    "camera_rotation": 0,
    "camera_hflip": False,
    "camera_vflip": False,
    "camera_timeout_ms": 1000
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
    return merged


def load_state():
    return load_json(STATE_PATH, {})


def save_state(state):
    save_json(STATE_PATH, state)


def ensure_dirs(config):
    image_dir = config.get("camera_image_dir", IMAGE_DIR)
    os.makedirs(image_dir, exist_ok=True)


def build_capture_command(output_path, config):
    cmd = [
        "rpicam-still",
        "-o", output_path,
        "--width", str(int(config.get("camera_width", 1920))),
        "--height", str(int(config.get("camera_height", 1080))),
        "--quality", str(int(config.get("camera_quality", 93))),
        "--timeout", str(int(config.get("camera_timeout_ms", 1000))),
    ]

    rotation = int(config.get("camera_rotation", 0))
    if rotation in (0, 180):
        cmd.extend(["--rotation", str(rotation)])

    if bool(config.get("camera_hflip", False)):
        cmd.append("--hflip")

    if bool(config.get("camera_vflip", False)):
        cmd.append("--vflip")

    return cmd


def capture_image(config):
    image_dir = config.get("camera_image_dir", IMAGE_DIR)
    filename_pattern = config.get("camera_filename_pattern", "%Y-%m-%d_%H-%M-%S.jpg")
    now = datetime.now()

    filename = now.strftime(filename_pattern)
    output_path = os.path.join(image_dir, filename)

    cmd = build_capture_command(output_path, config)
    subprocess.run(cmd, check=True)

    try:
        if os.path.exists(LATEST_IMAGE_PATH):
            os.remove(LATEST_IMAGE_PATH)
    except Exception:
        pass

    try:
        os.link(output_path, LATEST_IMAGE_PATH)
    except Exception:
        try:
            import shutil
            shutil.copy2(output_path, LATEST_IMAGE_PATH)
        except Exception:
            pass

    return output_path


def today_key(prefix, time_str):
    return f"{prefix}_{datetime.now().date().isoformat()}_{time_str}"


def should_capture(state, slot_name, configured_time):
    if not configured_time:
        return False

    now_str = datetime.now().strftime("%H:%M")
    if now_str != configured_time:
        return False

    key = today_key(slot_name, configured_time)
    done = state.get("camera_captures_done", {})
    return not done.get(key, False)


def mark_capture_done(state, slot_name, configured_time, path):
    key = today_key(slot_name, configured_time)
    state.setdefault("camera_captures_done", {})
    state["camera_captures_done"][key] = True
    state["last_image_path"] = path
    state["last_image_time"] = datetime.now().isoformat(timespec="seconds")
    return state


def cleanup_old_capture_marks(state):
    done = state.get("camera_captures_done", {})
    today = datetime.now().date().isoformat()
    filtered = {k: v for k, v in done.items() if today in k}
    state["camera_captures_done"] = filtered
    return state


def main():
    while True:
        config = load_config()
        state = load_state()

        ensure_dirs(config)
        state = cleanup_old_capture_marks(state)

        if config.get("camera_enabled", True):
            slots = [
                ("morning", config.get("timelapse_morning")),
                ("noon", config.get("timelapse_noon")),
                ("evening", config.get("timelapse_evening")),
            ]

            for slot_name, configured_time in slots:
                if should_capture(state, slot_name, configured_time):
                    try:
                        path = capture_image(config)
                        state = mark_capture_done(state, slot_name, configured_time, path)
                        save_state(state)
                    except Exception as e:
                        state["last_camera_error"] = str(e)
                        save_state(state)

        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
