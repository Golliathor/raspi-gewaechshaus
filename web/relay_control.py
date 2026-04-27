try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ModuleNotFoundError:
    GPIO = None
    GPIO_AVAILABLE = False

RELAY_PINS = {
    "exhaust": 17,
    "circulation": 27,
    "water_valve": 22,
}

ACTIVE_LOW = True

_initialized = False
_state = {
    "exhaust": False,
    "circulation": False,
    "water_valve": False,
}


def _gpio_on_level():
    return GPIO.LOW if ACTIVE_LOW else GPIO.HIGH


def _gpio_off_level():
    return GPIO.HIGH if ACTIVE_LOW else GPIO.LOW


def init_relays():
    global _initialized
    if _initialized:
        return

    if not GPIO_AVAILABLE:
        _initialized = True
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for pin in RELAY_PINS.values():
        GPIO.setup(pin, GPIO.OUT)

    _initialized = True


def set_relay(name: str, on: bool):
    init_relays()

    if name not in RELAY_PINS:
        raise ValueError(f"Unbekanntes Relais: {name}")

    if GPIO_AVAILABLE:
        pin = RELAY_PINS[name]
        GPIO.output(pin, _gpio_on_level() if on else _gpio_off_level())

    _state[name] = bool(on)


def get_state():
    return dict(_state)


def all_off():
    for name in RELAY_PINS:
        set_relay(name, False)
