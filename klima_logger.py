from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path

from greenhouse.config import ProjectPaths, save_json


PATHS = ProjectPaths.from_env()
CSV_PATH = PATHS.climate_csv_path
LATEST_CLIMATE_PATH = PATHS.latest_climate_path
MESSINTERVALL = 60  # Sekunden


def ensure_climate_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "temperature_c", "humidity_percent"])


def append_climate_csv(
    path: Path,
    timestamp: str,
    temperature_c: float,
    humidity_percent: float,
) -> None:
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [timestamp, f"{temperature_c:.1f}", f"{humidity_percent:.1f}"]
        )


def write_latest_climate(
    path: Path,
    timestamp: str,
    temperature_c: float,
    humidity_percent: float,
) -> None:
    save_json(
        path,
        {
            "timestamp": timestamp,
            "temperature_c": round(float(temperature_c), 1),
            "humidity_percent": round(float(humidity_percent), 1),
        },
    )


def main() -> None:
    # Hardware imports stay inside the hardware process so file helpers remain
    # importable during tests on a development computer.
    import adafruit_dht
    import board

    dht = adafruit_dht.DHT22(board.D4)
    ensure_climate_csv(CSV_PATH)
    print(f"Logging nach: {CSV_PATH}")
    print(f"Aktueller Klimawert: {LATEST_CLIMATE_PATH}")

    try:
        while True:
            try:
                temperature = dht.temperature
                humidity = dht.humidity

                if temperature is not None and humidity is not None:
                    timestamp = datetime.now().isoformat(timespec="seconds")
                    try:
                        append_climate_csv(
                            CSV_PATH,
                            timestamp,
                            temperature,
                            humidity,
                        )
                    except OSError as error:
                        print(f"CSV-Schreibfehler: {type(error).__name__}")
                    try:
                        write_latest_climate(
                            LATEST_CLIMATE_PATH,
                            timestamp,
                            temperature,
                            humidity,
                        )
                    except OSError as error:
                        print(f"Snapshot-Schreibfehler: {type(error).__name__}")

                    print(
                        f"{timestamp} | Temp={temperature:.1f}°C | "
                        f"Humidity={humidity:.1f}%"
                    )
                else:
                    print("Keine gültigen Daten erhalten")

            except RuntimeError as error:
                print(f"DHT-Lesefehler: {error}")

            time.sleep(MESSINTERVALL)

    except KeyboardInterrupt:
        print("\nLogger beendet.")
    finally:
        dht.exit()


if __name__ == "__main__":
    main()
