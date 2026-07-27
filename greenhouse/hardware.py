from __future__ import annotations

import time
from dataclasses import dataclass
from statistics import median
from typing import Protocol

from greenhouse.models import ActuatorState


class RelayOutput(Protocol):
    def set_state(self, state: ActuatorState) -> None:
        ...

    def get_state(self) -> ActuatorState:
        ...

    def all_off(self) -> None:
        ...


class RaspberryPiRelayOutput:
    """Adapter, der RPi.GPIO erst auf echter Hardware importiert."""

    def __init__(self) -> None:
        try:
            from web import relay_control
        except ImportError:
            import relay_control

        self._relay_control = relay_control
        self._relay_control.init_relays()

    def set_state(self, state: ActuatorState) -> None:
        for name, enabled in state.as_dict().items():
            self._relay_control.set_relay(name, enabled)

    def get_state(self) -> ActuatorState:
        state = self._relay_control.get_state()
        return ActuatorState(
            exhaust=bool(state.get("exhaust", False)),
            circulation=bool(state.get("circulation", False)),
            water_valve=bool(state.get("water_valve", False)),
        )

    def all_off(self) -> None:
        self._relay_control.all_off()


class MemoryRelayOutput:
    def __init__(self, initial_state: ActuatorState | None = None) -> None:
        self.state = initial_state or ActuatorState()
        self.writes: list[ActuatorState] = []

    def set_state(self, state: ActuatorState) -> None:
        self.state = state
        self.writes.append(state)

    def get_state(self) -> ActuatorState:
        return self.state

    def all_off(self) -> None:
        self.set_state(ActuatorState())


@dataclass(frozen=True)
class ADCBatchReading:
    value: int | None
    minimum: int | None
    maximum: int | None
    valid_samples: int
    requested_samples: int

    @property
    def span(self) -> int | None:
        if self.minimum is None or self.maximum is None:
            return None
        return self.maximum - self.minimum


class ADS1115Reader:
    """ADC-Adapter mit verzögerten Adafruit-Imports."""

    def __init__(self, address: int = 0x48) -> None:
        self.address = address
        self._adc = None
        self._analog_in = None
        self._ads_module = None

    def _initialize(self) -> None:
        if self._adc is not None:
            return
        import board
        import busio
        import adafruit_ads1x15.ads1115 as ADS
        from adafruit_ads1x15.analog_in import AnalogIn

        i2c = busio.I2C(board.SCL, board.SDA)
        self._adc = ADS.ADS1115(i2c, address=self.address)
        self._adc.gain = 1
        self._analog_in = AnalogIn
        self._ads_module = ADS

    def read_channel(self, channel: int) -> int | None:
        if channel not in (0, 1, 2, 3):
            return None
        try:
            self._initialize()
            analog = self._analog_in(self._adc, channel)
            return int(analog.value)
        except Exception:
            return None

    def read_channel_batch(
        self,
        channel: int,
        *,
        sample_count: int = 9,
        sample_interval_seconds: float = 0.04,
    ) -> ADCBatchReading:
        requested = max(1, min(31, int(sample_count)))
        interval = max(0.0, float(sample_interval_seconds))
        values: list[int] = []
        for sample_index in range(requested):
            value = self.read_channel(channel)
            if value is not None:
                values.append(value)
            if interval > 0 and sample_index + 1 < requested:
                time.sleep(interval)

        minimum_valid = requested // 2 + 1
        stable_value = (
            int(round(median(values)))
            if len(values) >= minimum_valid
            else None
        )
        return ADCBatchReading(
            value=stable_value,
            minimum=min(values) if values else None,
            maximum=max(values) if values else None,
            valid_samples=len(values),
            requested_samples=requested,
        )
