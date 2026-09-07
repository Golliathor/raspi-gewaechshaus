from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from greenhouse.config import DEFAULT_CONFIG, ProjectPaths, validate_config
from greenhouse.influx import (
    InfluxSettings,
    InfluxTelemetry,
    control_to_line,
    cycle_to_lines,
    datetime_to_nanoseconds,
    event_to_line,
    snapshot_to_line,
)
from greenhouse.models import (
    ActuatorState,
    ControlDecision,
    CycleResult,
    SensorSnapshot,
    WeatherSnapshot,
)
from web import automation_daemon


MEASURED_AT = datetime(
    2026,
    9,
    7,
    12,
    34,
    56,
    789123,
    tzinfo=timezone(timedelta(hours=2)),
)


def example_result() -> CycleResult:
    snapshot = SensorSnapshot(
        timestamp=MEASURED_AT,
        temperature_c=24.5,
        humidity_percent=61.0,
        soil_moisture_percent=(31.0, None, 48.5),
        light_percent=72.0,
        quality="invalid",
        issues=("soil_2_missing",),
        weather=WeatherSnapshot(
            timestamp=MEASURED_AT - timedelta(minutes=5),
            outside_temperature_c=19.0,
            outside_humidity_percent=70.0,
            precipitation_mm=1.5,
            precipitation_probability_percent=80.0,
            provider="open_meteo",
        ),
    )
    requested = ControlDecision(
        exhaust=True,
        circulation=False,
        watering_seconds=12.0,
        reasons=("watering reason, with spaces",),
        diagnostics={
            "watering_armed": True,
            "weather_status": "fresh variable text",
            "trends_per_minute": {"temperature": 0.125},
        },
    )
    applied = ControlDecision(
        exhaust=True,
        circulation=False,
        watering_seconds=10.0,
        reasons=requested.reasons,
        diagnostics=requested.diagnostics,
    )
    return CycleResult(
        timestamp=MEASURED_AT + timedelta(seconds=1),
        controller_id="adaptive_local",
        snapshot=snapshot,
        requested=requested,
        applied=applied,
        state=ActuatorState(exhaust=True, water_valve=True),
        safety_overrides=("watering_pulse_limited",),
        transitions=("exhaust:on", "water_valve:on"),
        watering_started_seconds=10.0,
    )


class InfluxMappingTests(unittest.TestCase):
    def test_snapshot_maps_numeric_values_and_omits_none(self) -> None:
        line = snapshot_to_line(example_result().snapshot)

        self.assertTrue(line.startswith("sensor,quality=invalid,source=growpi "))
        self.assertIn("temperature_c=24.5", line)
        self.assertIn("soil1_percent=31.0", line)
        self.assertIn("soil3_percent=48.5", line)
        self.assertIn("outside_temperature_c=19.0", line)
        self.assertNotIn("soil2_percent", line)
        self.assertNotIn("None", line)

    def test_sensor_and_result_keep_their_original_timestamps(self) -> None:
        result = example_result()
        lines = cycle_to_lines(result, "trial-42")

        self.assertTrue(
            lines[0].endswith(str(datetime_to_nanoseconds(result.snapshot.timestamp)))
        )
        self.assertTrue(
            lines[1].endswith(str(datetime_to_nanoseconds(result.timestamp)))
        )
        self.assertNotEqual(lines[0].rsplit(" ", 1)[1], lines[1].rsplit(" ", 1)[1])

    def test_control_contains_controller_and_scientific_resource_fields(self) -> None:
        line = control_to_line(
            example_result(),
            "trial 42",
            water_flow_ml_per_second=25.0,
        )

        self.assertIn("controller_id=adaptive_local", line.split(" ", 1)[0])
        self.assertIn(",run_id=trial\\ 42,source=growpi ", line)
        self.assertIn("estimated_water_ml=250.0", line)
        self.assertIn("exhaust_transition=1i", line)
        self.assertIn("safety_override_count=1i", line)
        self.assertIn("diagnostic_watering_armed=true", line)
        self.assertIn("diagnostic_trends_per_minute_temperature=0.125", line)
        self.assertNotIn("fresh variable text", line)

    def test_variable_event_text_is_a_field_and_never_a_tag(self) -> None:
        line = event_to_line(example_result(), "trial-42")
        self.assertIsNotNone(line)
        assert line is not None
        tags = line.split(" ", 1)[0]

        self.assertNotIn("watering reason", tags)
        self.assertNotIn("watering_pulse_limited", tags)
        self.assertNotIn("water_valve:on", tags)
        self.assertIn('controller_reasons="watering reason, with spaces"', line)
        self.assertIn('safety_overrides="watering_pulse_limited"', line)
        self.assertIn('sensor_issues="soil_2_missing"', line)

    def test_empty_snapshot_still_produces_a_valid_quality_point(self) -> None:
        snapshot = SensorSnapshot(
            timestamp=MEASURED_AT,
            temperature_c=None,
            humidity_percent=None,
            quality="invalid",
        )
        line = snapshot_to_line(snapshot)

        self.assertIn("valid_numeric_field_count=0i", line)
        self.assertNotIn("None", line)


class InfluxFailureIsolationTests(unittest.TestCase):
    def test_http_failure_is_reported_but_never_raised(self) -> None:
        events: list[tuple[str, str]] = []

        def failing_opener(*args, **kwargs):
            raise TimeoutError("server unavailable; secret-token")

        telemetry = InfluxTelemetry(
            InfluxSettings(enabled=True),
            "secret-token",
            status_callback=lambda event, details: events.append((event, details)),
            opener=failing_opener,
            start_worker=False,
        )

        self.assertFalse(telemetry.write_lines_now(("sensor value=1i 1",)))
        self.assertFalse(telemetry.write_lines_now(("sensor value=2i 2",)))
        self.assertEqual(events[0][0], "influx_write_failed")
        self.assertEqual(len(events), 1)
        self.assertNotIn("secret-token", events[0][1])

    def test_successful_http_write_uses_v2_endpoint_and_token_header(self) -> None:
        captured = {}

        class Response:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["body"] = request.data.decode("utf-8")
            captured["timeout"] = timeout
            return Response()

        settings = InfluxSettings(
            enabled=True,
            url="http://influx.test:8086",
            org="my org",
            bucket="greenhouse",
            timeout_seconds=2.5,
        )
        telemetry = InfluxTelemetry(
            settings,
            "secret-token",
            opener=opener,
            start_worker=False,
        )

        self.assertTrue(telemetry.write_lines_now(("sensor value=1i 1",)))
        self.assertIn("/api/v2/write?", captured["url"])
        self.assertIn("org=my+org", captured["url"])
        self.assertIn("precision=ns", captured["url"])
        self.assertEqual(captured["authorization"], "Token secret-token")
        self.assertEqual(captured["timeout"], 2.5)

    def test_missing_token_disables_only_telemetry(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["influxdb"].update(
            {
                "enabled": True,
                "token_file": "/definitely/missing/influx-token",
            }
        )
        events = []

        telemetry = InfluxTelemetry.from_config(
            config,
            status_callback=lambda event, details: events.append((event, details)),
        )

        self.assertFalse(telemetry.enabled)
        self.assertEqual(telemetry.disabled_reason, "token_file_unreadable")
        self.assertFalse(telemetry.submit_cycle(example_result(), "trial", 25.0))
        self.assertEqual(events[0][0], "influx_disabled")

    def test_disabled_configuration_does_not_read_token_or_open_network(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["influxdb"]["enabled"] = False
        config["influxdb"]["token_file"] = "/definitely/missing/token"

        telemetry = InfluxTelemetry.from_config(
            config,
            opener=lambda *args, **kwargs: self.fail("network must stay unused"),
        )

        self.assertFalse(telemetry.enabled)
        self.assertEqual(telemetry.disabled_reason, "configured_off")
        self.assertFalse(telemetry.submit_cycle(example_result(), "trial", 25.0))

    def test_csv_records_survive_a_telemetry_exception(self) -> None:
        class ExplodingTelemetry:
            def submit_cycle(self, *args, **kwargs):
                raise RuntimeError("telemetry defect")

        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            with (
                patch.object(automation_daemon, "PATHS", paths),
                patch.object(automation_daemon, "RUN_ID", "csv-first-test"),
            ):
                automation_daemon.record_cycle(
                    example_result(),
                    ExplodingTelemetry(),  # type: ignore[arg-type]
                    {"water_flow_ml_per_second": 25.0},
                )

            snapshot_csv = paths.snapshot_log_path.read_text(encoding="utf-8")
            decision_csv = paths.decision_log_path.read_text(encoding="utf-8")

        self.assertIn("temperature_c", snapshot_csv)
        self.assertIn("24.5", snapshot_csv)
        self.assertIn("controller_id", decision_csv)
        self.assertIn("adaptive_local", decision_csv)
        self.assertIn("csv-first-test", decision_csv)


class InfluxConfigTests(unittest.TestCase):
    def test_defaults_are_valid_and_invalid_endpoint_is_reported(self) -> None:
        self.assertEqual(validate_config(DEFAULT_CONFIG), [])
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["influxdb"]["url"] = "influx-without-scheme"
        config["influxdb"]["timeout_seconds"] = 0

        errors = validate_config(config)

        self.assertTrue(any("influxdb.url" in error for error in errors))
        self.assertTrue(any("influxdb.timeout_seconds" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
