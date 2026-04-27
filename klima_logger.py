import csv
import time
from datetime import datetime
from pathlib import Path

import board
import adafruit_dht

CSV_PATH = Path("/home/grow/gewaechshaus/logs/klima.csv")
MESSINTERVALL = 60  # Sekunden

dht = adafruit_dht.DHT22(board.D4)

# CSV-Datei mit Header anlegen, falls sie noch nicht existiert
if not CSV_PATH.exists():
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "temperature_c", "humidity_percent"])

print(f"Logging nach: {CSV_PATH}")

try:
    while True:
        try:
            temperature = dht.temperature
            humidity = dht.humidity

            if temperature is not None and humidity is not None:
                timestamp = datetime.now().isoformat(timespec="seconds")

                with open(CSV_PATH, "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([timestamp, f"{temperature:.1f}", f"{humidity:.1f}"])

                print(f"{timestamp} | Temp={temperature:.1f}°C | Humidity={humidity:.1f}%")
            else:
                print("Keine gültigen Daten erhalten")

        except RuntimeError as e:
            print(f"DHT-Lesefehler: {e}")

        time.sleep(MESSINTERVALL)

except KeyboardInterrupt:
    print("\nLogger beendet.")
finally:
    dht.exit()
