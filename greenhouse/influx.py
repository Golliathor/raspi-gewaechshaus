from __future__ import annotations

import math
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from greenhouse.models import CycleResult, SensorSnapshot


StatusCallback = Callable[[str, str], None]
UrlOpener = Callable[..., Any]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_STOP = object()
_FIELD_KEY_PATTERN = re.compile(r"[^a-zA-Z0-9_]")


@dataclass(frozen=True)
class InfluxSettings:
    enabled: bool = False
    url: str = "http://100.88.152.72:8086"
    org: str = "greenhouse"
    bucket: str = "greenhouse"
    token_file: str = "/etc/gewaechshaus/influx-token"
    timeout_seconds: float = 3.0
    source: str = "growpi"

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "InfluxSettings":
        values = config.get("influxdb", {})
        if not isinstance(values, Mapping):
            values = {}
        return cls(
            enabled=bool(values.get("enabled", False)),
            url=str(values.get("url", cls.url)).rstrip("/"),
            org=str(values.get("org", cls.org)),
            bucket=str(values.get("bucket", cls.bucket)),
            token_file=str(values.get("token_file", cls.token_file)),
            timeout_seconds=float(
                values.get("timeout_seconds", cls.timeout_seconds)
            ),
            source=str(values.get("source", cls.source)),
        )


def _escape_tag(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
        .replace("=", "\\=")
    )


def _escape_field_key(value: Any) -> str:
    return _escape_tag(value)


def _field_value(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value}i"
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return repr(value)
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )
        return f'"{escaped}"'
    return None


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def datetime_to_nanoseconds(value: datetime) -> int:
    """Convert the original measurement time to an exact Unix ns timestamp.

    Project timestamps are normally naive local Raspberry-Pi times. Those are
    interpreted in the host timezone; timezone-aware replay timestamps retain
    their explicit offset.
    """

    aware = value.astimezone() if value.tzinfo is None else value
    delta = aware.astimezone(timezone.utc) - _EPOCH
    return (
        (delta.days * 86400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1000
    )


def _line(
    measurement: str,
    tags: Mapping[str, Any],
    fields: Mapping[str, Any],
    timestamp: datetime,
) -> str | None:
    serialized_fields = []
    for key in sorted(fields):
        serialized = _field_value(fields[key])
        if serialized is not None:
            serialized_fields.append(f"{_escape_field_key(key)}={serialized}")
    if not serialized_fields:
        return None

    serialized_tags = "".join(
        f",{_escape_tag(key)}={_escape_tag(tags[key])}"
        for key in sorted(tags)
        if tags[key] is not None and str(tags[key]) != ""
    )
    return (
        f"{_escape_tag(measurement)}{serialized_tags} "
        f"{','.join(serialized_fields)} {datetime_to_nanoseconds(timestamp)}"
    )


def snapshot_to_line(
    snapshot: SensorSnapshot,
    *,
    source: str = "growpi",
) -> str:
    soil = list(snapshot.soil_moisture_percent[:3])
    soil.extend([None] * (3 - len(soil)))
    weather = snapshot.weather
    fields: dict[str, Any] = {
        "temperature_c": _finite_float(snapshot.temperature_c),
        "humidity_percent": _finite_float(snapshot.humidity_percent),
        "soil1_percent": _finite_float(soil[0]),
        "soil2_percent": _finite_float(soil[1]),
        "soil3_percent": _finite_float(soil[2]),
        "light_percent": _finite_float(snapshot.light_percent),
        "outside_temperature_c": _finite_float(
            weather.outside_temperature_c if weather else None
        ),
        "outside_humidity_percent": _finite_float(
            weather.outside_humidity_percent if weather else None
        ),
        "precipitation_mm": _finite_float(
            weather.precipitation_mm if weather else None
        ),
        "precipitation_probability_percent": _finite_float(
            weather.precipitation_probability_percent if weather else None
        ),
    }
    line = _line(
        "sensor",
        {"source": source, "quality": snapshot.quality},
        fields,
        snapshot.timestamp,
    )
    if line is None:
        # A point still records data quality even when every sensor field is
        # unavailable; Influx requires at least one field.
        line = _line(
            "sensor",
            {"source": source, "quality": snapshot.quality},
            {"valid_numeric_field_count": 0},
            snapshot.timestamp,
        )
    assert line is not None
    return line


def _safe_field_component(value: Any) -> str:
    component = _FIELD_KEY_PATTERN.sub("_", str(value)).strip("_").lower()
    return component[:80]


def _numeric_diagnostics(
    values: Mapping[str, Any],
    *,
    prefix: str = "diagnostic",
    depth: int = 0,
) -> dict[str, Any]:
    """Flatten bounded controller-owned numeric diagnostics into fields."""

    result: dict[str, Any] = {}
    if depth > 3:
        return result
    for key in sorted(values, key=str):
        component = _safe_field_component(key)
        if not component:
            continue
        field_key = f"{prefix}_{component}"
        value = values[key]
        if isinstance(value, bool):
            result[field_key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            converted = _finite_float(value)
            if converted is not None:
                result[field_key] = converted
        elif isinstance(value, Mapping):
            result.update(
                _numeric_diagnostics(
                    value,
                    prefix=field_key,
                    depth=depth + 1,
                )
            )
        if len(result) >= 64:
            break
    return dict(list(result.items())[:64])


def control_to_line(
    result: CycleResult,
    run_id: str,
    *,
    source: str = "growpi",
    water_flow_ml_per_second: float = 0.0,
) -> str:
    transitions = set(result.transitions)
    fields: dict[str, Any] = {
        "requested_exhaust": result.requested.exhaust,
        "requested_circulation": result.requested.circulation,
        "requested_watering_seconds": float(result.requested.watering_seconds),
        "applied_exhaust": result.applied.exhaust,
        "applied_circulation": result.applied.circulation,
        "applied_watering_seconds": float(result.applied.watering_seconds),
        "water_valve": result.state.water_valve,
        "watering_started_seconds": float(result.watering_started_seconds),
        "estimated_water_ml": round(
            max(0.0, float(result.watering_started_seconds))
            * max(0.0, float(water_flow_ml_per_second)),
            6,
        ),
        "weather_available": result.snapshot.weather is not None,
        "reason_count": len(result.requested.reasons),
        "safety_override_count": len(result.safety_overrides),
        "sensor_issue_count": len(result.snapshot.issues),
        "transition_count": len(result.transitions),
        "exhaust_transition": int(
            "exhaust:on" in transitions or "exhaust:off" in transitions
        ),
        "circulation_transition": int(
            "circulation:on" in transitions
            or "circulation:off" in transitions
        ),
        "water_valve_transition": int(
            "water_valve:on" in transitions
            or "water_valve:off" in transitions
        ),
    }
    fields.update(_numeric_diagnostics(result.requested.diagnostics))
    line = _line(
        "control",
        {
            "source": source,
            "controller_id": result.controller_id,
            "run_id": run_id,
        },
        fields,
        result.timestamp,
    )
    assert line is not None
    return line


def event_to_line(
    result: CycleResult,
    run_id: str,
    *,
    source: str = "growpi",
    water_flow_ml_per_second: float = 0.0,
) -> str | None:
    fields: dict[str, Any] = {}
    if result.requested.reasons:
        fields["controller_reasons"] = "|".join(result.requested.reasons)
    if result.safety_overrides:
        fields["safety_overrides"] = "|".join(result.safety_overrides)
    if result.transitions:
        fields["transitions"] = "|".join(result.transitions)
    if result.snapshot.issues:
        fields["sensor_issues"] = "|".join(result.snapshot.issues)
    if result.watering_started_seconds > 0:
        fields["watering_started_seconds"] = float(
            result.watering_started_seconds
        )
        fields["estimated_water_ml"] = round(
            float(result.watering_started_seconds)
            * max(0.0, float(water_flow_ml_per_second)),
            6,
        )
    if not fields:
        return None
    return _line(
        "event",
        {
            "source": source,
            "controller_id": result.controller_id,
            "run_id": run_id,
        },
        fields,
        result.timestamp,
    )


def cycle_to_lines(
    result: CycleResult,
    run_id: str,
    *,
    source: str = "growpi",
    water_flow_ml_per_second: float = 0.0,
) -> tuple[str, ...]:
    lines = [
        snapshot_to_line(result.snapshot, source=source),
        control_to_line(
            result,
            run_id,
            source=source,
            water_flow_ml_per_second=water_flow_ml_per_second,
        ),
    ]
    event = event_to_line(
        result,
        run_id,
        source=source,
        water_flow_ml_per_second=water_flow_ml_per_second,
    )
    if event is not None:
        lines.append(event)
    return tuple(lines)


class InfluxTelemetry:
    """Best-effort InfluxDB writer isolated from the control loop."""

    _BATCH_CYCLES = 12
    _FLUSH_INTERVAL_SECONDS = 10.0
    _RETRY_INTERVAL_SECONDS = 60.0
    _QUEUE_CYCLES = 256

    def __init__(
        self,
        settings: InfluxSettings,
        token: str | None,
        *,
        status_callback: StatusCallback | None = None,
        opener: UrlOpener = urlopen,
        start_worker: bool = True,
    ) -> None:
        self.settings = settings
        self._token = token or ""
        self._status_callback = status_callback
        self._opener = opener
        self._queue: queue.Queue[tuple[str, ...] | object] = queue.Queue(
            maxsize=self._QUEUE_CYCLES
        )
        self._thread: threading.Thread | None = None
        self._closed = False
        self._last_status_at: dict[str, float] = {}
        self._write_failed = False
        self.disabled_reason: str | None = None
        self.enabled = bool(settings.enabled and self._token)
        if self.enabled and start_worker:
            self._thread = threading.Thread(
                target=self._worker,
                name="influx-telemetry",
                daemon=True,
            )
            self._thread.start()

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        status_callback: StatusCallback | None = None,
        opener: UrlOpener = urlopen,
        start_worker: bool = True,
    ) -> "InfluxTelemetry":
        try:
            settings = InfluxSettings.from_config(config)
        except (TypeError, ValueError) as error:
            settings = InfluxSettings(enabled=False)
            instance = cls(
                settings,
                None,
                status_callback=status_callback,
                opener=opener,
                start_worker=False,
            )
            instance.disabled_reason = "invalid_config"
            instance._report("influx_disabled", type(error).__name__, force=True)
            return instance
        if not settings.enabled:
            instance = cls(
                settings,
                None,
                status_callback=status_callback,
                opener=opener,
                start_worker=False,
            )
            instance.disabled_reason = "configured_off"
            return instance

        try:
            token = Path(settings.token_file).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            instance = cls(
                settings,
                None,
                status_callback=status_callback,
                opener=opener,
                start_worker=False,
            )
            instance.disabled_reason = "token_file_unreadable"
            instance._report(
                "influx_disabled",
                f"token_file_unreadable ({type(error).__name__})",
                force=True,
            )
            return instance
        if not token:
            instance = cls(
                settings,
                None,
                status_callback=status_callback,
                opener=opener,
                start_worker=False,
            )
            instance.disabled_reason = "token_file_empty"
            instance._report("influx_disabled", "token_file_empty", force=True)
            return instance
        try:
            return cls(
                settings,
                token,
                status_callback=status_callback,
                opener=opener,
                start_worker=start_worker,
            )
        except Exception as error:
            instance = cls(
                settings,
                None,
                status_callback=status_callback,
                opener=opener,
                start_worker=False,
            )
            instance.disabled_reason = "worker_start_failed"
            instance._report(
                "influx_disabled",
                f"worker_start_failed ({type(error).__name__})",
                force=True,
            )
            return instance

    def submit_cycle(
        self,
        result: CycleResult,
        run_id: str,
        water_flow_ml_per_second: float,
    ) -> bool:
        if not self.enabled or self._closed:
            return False
        try:
            lines = cycle_to_lines(
                result,
                run_id,
                source=self.settings.source,
                water_flow_ml_per_second=water_flow_ml_per_second,
            )
            self._queue.put_nowait(lines)
            return True
        except queue.Full:
            self._report(
                "influx_queue_full",
                "telemetry cycle dropped",
            )
        except Exception as error:
            self._report(
                "influx_mapping_failed",
                self._safe_error(error),
            )
        return False

    def write_lines_now(self, lines: Iterable[str]) -> bool:
        """Write a batch without ever propagating errors to the caller."""

        if not self.enabled or self._closed:
            return False
        try:
            payload = "\n".join(line for line in lines if line)
            if not payload:
                return True
            query = urlencode(
                {
                    "org": self.settings.org,
                    "bucket": self.settings.bucket,
                    "precision": "ns",
                }
            )
            request = Request(
                f"{self.settings.url}/api/v2/write?{query}",
                data=payload.encode("utf-8"),
                headers={
                    "Authorization": f"Token {self._token}",
                    "Content-Type": "text/plain; charset=utf-8",
                    "Accept": "application/json",
                    "User-Agent": "raspi-gewaechshaus/1",
                },
                method="POST",
            )
            with self._opener(
                request,
                timeout=self.settings.timeout_seconds,
            ) as response:
                status = int(getattr(response, "status", 204))
                if status < 200 or status >= 300:
                    raise OSError(f"HTTP status {status}")
        except Exception as error:
            self._write_failed = True
            self._report("influx_write_failed", self._safe_error(error))
            return False

        if self._write_failed:
            self._report("influx_write_recovered", "write successful", force=True)
        self._write_failed = False
        return True

    def close(self, timeout_seconds: float = 1.0) -> None:
        if self._closed:
            return
        self._closed = True
        if self._thread is not None:
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(_STOP)
                except (queue.Empty, queue.Full):
                    pass
            self._thread.join(timeout=max(0.0, timeout_seconds))
        self._token = ""

    def _worker(self) -> None:
        pending: list[tuple[str, ...]] = []
        last_flush = time.monotonic()
        next_attempt = 0.0
        while True:
            timeout = max(
                0.05,
                self._FLUSH_INTERVAL_SECONDS - (time.monotonic() - last_flush),
            )
            item: tuple[str, ...] | object | None
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = None

            if item is None and not pending:
                last_flush = time.monotonic()
                continue

            should_stop = item is _STOP
            if item is not None and item is not _STOP:
                pending.append(item)

            due = (
                should_stop
                or len(pending) >= self._BATCH_CYCLES
                or time.monotonic() - last_flush >= self._FLUSH_INTERVAL_SECONDS
            )
            if pending and due:
                now = time.monotonic()
                if now >= next_attempt:
                    lines = [line for cycle in pending for line in cycle]
                    if not self.write_lines_now(lines):
                        next_attempt = now + self._RETRY_INTERVAL_SECONDS
                pending.clear()
                last_flush = now
            if should_stop:
                return

    def _safe_error(self, error: Exception) -> str:
        # Exception strings from HTTP libraries may contain request headers.
        # Only structural metadata is logged so a token cannot leak even from
        # an unexpected third-party opener implementation.
        details = type(error).__name__
        status = getattr(error, "code", None)
        if isinstance(status, int):
            details += f"; http_status={status}"
        reason = getattr(error, "reason", None)
        if reason is not None:
            details += f"; reason_type={type(reason).__name__}"
        return details

    def _report(
        self,
        event: str,
        details: str,
        *,
        force: bool = False,
    ) -> None:
        if self._status_callback is None:
            return
        now = time.monotonic()
        if not force and now - self._last_status_at.get(event, -1e12) < 60.0:
            return
        self._last_status_at[event] = now
        try:
            self._status_callback(event, details)
        except Exception:
            # Telemetry diagnostics are also best effort and must not affect
            # the daemon if its action log is temporarily unavailable.
            pass
