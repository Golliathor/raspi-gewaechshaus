import time
import board
import adafruit_dht

dht = adafruit_dht.DHT22(board.D4)

while True:
    try:
        temperature = dht.temperature
        humidity = dht.humidity

        if temperature is not None and humidity is not None:
            print(f"Temp={temperature:.1f}°C  Humidity={humidity:.1f}%")
        else:
            print("Keine gültigen Daten")

    except RuntimeError as e:
        # typisch beim DHT → einfach nochmal versuchen
        print(f"Messfehler: {e}")

    time.sleep(2)
