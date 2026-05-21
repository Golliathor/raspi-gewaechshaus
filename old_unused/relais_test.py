import RPi.GPIO as GPIO
import time

RELAIS_PINS = [17, 27, 22, 23]

GPIO.setmode(GPIO.BCM)

for pin in RELAIS_PINS:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.HIGH)  # alles AUS

print("Alle Relais AUS")
time.sleep(2)

for pin in RELAIS_PINS:
    print(f"Relais {pin} EIN")
    GPIO.output(pin, GPIO.LOW)
    time.sleep(2)
    GPIO.output(pin, GPIO.HIGH)

print("Fertig")

GPIO.cleanup()
