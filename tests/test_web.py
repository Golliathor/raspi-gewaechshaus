from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None and importlib.util.find_spec(
    "flask_compress"
) is not None


@unittest.skipUnless(FLASK_AVAILABLE, "Flask-Abhängigkeiten sind nicht installiert")
class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temporary_directory.name)
        (self.base_dir / "web").mkdir()
        (self.base_dir / "logs").mkdir()
        environment = patch.dict(
            os.environ, {"GREENHOUSE_BASE_DIR": str(self.base_dir)}, clear=False
        )
        environment.start()
        self.addCleanup(environment.stop)

        import web.app as app_module

        self.app_module = importlib.reload(app_module)
        self.client = self.app_module.app.test_client()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_status_exposes_controller_diagnostics(self) -> None:
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["active_controller"], "adaptive_weather")
        self.assertIn("last_decision_reasons", data)
        self.assertIn("last_decision_diagnostics", data)
        self.assertIn("last_safety_overrides", data)
        self.assertIn("last_command_result", data)
        self.assertIn("watering_limits", data)
        self.assertEqual(
            data["watering_limits"]["max_pulse_seconds"], 60.0
        )
        self.assertIn("weather_available", data)
        self.assertIn("last_weather_error", data)

    def test_dashboard_exposes_model_kpis_and_explanations(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        for element_id in (
            b'id="modelName"',
            b'id="temperatureKpi"',
            b'id="soilKpi"',
            b'id="decisionReasons"',
            b'id="diagnosticGroups"',
            b'id="climateChart"',
            b'id="dailyWaterChart"',
            b'id="controllerSelect"',
            b'id="weatherForecast"',
            b'id="waterSafetySummary"',
            b'id="lastManualWateringResult"',
        ):
            self.assertIn(element_id, response.data)
        self.assertIn(b"Baseline 1", response.data)
        self.assertIn(b"Ansatz B", response.data)
        self.assertIn(b"Chart.getChart(lightCanvas)", response.data)
        self.assertIn(b"dailyChartsRefreshInFlight", response.data)
        self.assertIn(b"Gefilterter Regelwert", response.data)
        self.assertIn(b"ADC-Spanne", response.data)
        self.assertIn(b"der Safety-Layer erlaubt aktuell", response.data)
        self.assertIn(b"Wiederfreigabemodus", response.data)
        self.assertIn(b"Bodenfeuchteziel erreicht", response.data)
        for controller_id in (
            b"baseline_fixed",
            b"baseline_hysteresis",
            b"adaptive_local",
            b"adaptive_weather",
        ):
            self.assertIn(controller_id, response.data)
        self.assertNotIn(b"data: {\\n    data:", response.data)

    def test_controller_api_switches_all_models_and_rejects_unknown_id(self) -> None:
        for controller_id in (
            "baseline_fixed",
            "baseline_hysteresis",
            "adaptive_local",
            "adaptive_weather",
        ):
            with self.subTest(controller_id=controller_id):
                response = self.client.post(
                    "/api/controller",
                    json={"controller_id": controller_id},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.get_json()["controller_id"], controller_id
                )
                self.assertEqual(
                    self.app_module.load_config()["controller"]["active"],
                    controller_id,
                )

        response = self.client.post(
            "/api/controller",
            json={"controller_id": "not-a-controller"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "available_controllers",
            response.get_json(),
        )
        self.assertEqual(
            self.app_module.load_config()["controller"]["active"],
            "adaptive_weather",
        )

    def test_latest_image_timestamp_comes_from_image_file(self) -> None:
        image_dir = self.base_dir / "images"
        image_dir.mkdir()
        latest_image = image_dir / "latest.jpg"
        latest_image.write_bytes(b"test-image")
        expected = datetime(2026, 7, 24, 14, 30, 0)
        timestamp = expected.timestamp()
        os.utime(latest_image, (timestamp, timestamp))
        self.app_module.save_json(
            self.app_module.PATHS.state_path,
            {"last_image_time": "2026-05-02T13:00:18"},
        )

        response = self.client.get("/")
        status = self.client.get("/api/status").get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"2026-07-24T14:30:00", response.data)
        self.assertEqual(status["last_image_time"], "2026-07-24T14:30:00")

    def test_config_form_uses_adc_names_and_persists_address(self) -> None:
        response = self.client.get("/config")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="adc_address"', response.data)
        self.assertIn(b'name="adc_sample_count"', response.data)
        self.assertIn(b'name="adc_sample_interval_ms"', response.data)
        self.assertIn(b'name="soil_filter_alpha"', response.data)
        self.assertNotIn(b"pcf8591_address", response.data)
        self.assertIn(b'name="controller_active"', response.data)
        self.assertIn(
            b'name="adaptive_local_trend_window_seconds"',
            response.data,
        )
        self.assertIn(
            b'name="baseline_fixed_exhaust_temperature_threshold_c"',
            response.data,
        )
        self.assertIn(
            b'name="baseline_hysteresis_watering_cooldown_seconds"',
            response.data,
        )
        self.assertIn(b'name="weather_latitude"', response.data)
        self.assertIn(
            b'name="adaptive_weather_weather_max_age_seconds"',
            response.data,
        )
        self.assertEqual(
            response.data.count(b'name="safety_max_watering_pulse_seconds"'),
            1,
        )
        self.assertIn(b"600-s-Impuls", response.data)
        self.assertIn(b"Feuchteziel / obere Referenz", response.data)

        response = self.client.post(
            "/config",
            data={
                "adc_enabled": "on",
                "adc_type": "ADS1115",
                "adc_address": "73",
                "adc_sample_count": "11",
                "adc_sample_interval_ms": "25",
                "soil_filter_alpha": "0.15",
                "automation_enabled": "on",
                "watering_enabled": "on",
                "controller_active": "adaptive_local",
                "adaptive_local_trend_window_seconds": "1200",
                "weather_enabled": "on",
                "weather_latitude": "52.52",
                "weather_longitude": "13.405",
                "adaptive_weather_weather_max_age_seconds": "1800",
            },
        )
        self.assertEqual(response.status_code, 302)
        saved = self.app_module.load_config()
        self.assertEqual(saved["adc_address"], 73)
        self.assertEqual(saved["adc_sample_count"], 11)
        self.assertEqual(saved["adc_sample_interval_ms"], 25)
        self.assertEqual(saved["soil_filter_alpha"], 0.15)
        self.assertEqual(saved["controller"]["active"], "adaptive_local")
        self.assertEqual(
            saved["controllers"]["adaptive_local"]["trend_window_seconds"],
            1200,
        )
        self.assertTrue(saved["weather"]["enabled"])
        self.assertEqual(saved["weather"]["latitude"], 52.52)
        self.assertEqual(
            saved["controllers"]["adaptive_weather"][
                "weather_max_age_seconds"
            ],
            1800,
        )

    def test_manual_watering_api_reports_safety_limit(self) -> None:
        config = self.app_module.load_config()
        config["watering_seconds"] = 600
        config["safety"]["max_watering_pulse_seconds"] = 100
        config["safety"]["max_daily_watering_seconds"] = 600
        self.app_module.save_config(config)
        self.app_module.save_json(
            self.app_module.PATHS.state_path,
            {
                "relays": {"water_valve": False},
                "control_runtime": {
                    "watering_day": datetime.now().date().isoformat(),
                    "daily_watering_seconds": 0,
                },
            },
        )

        response = self.client.post("/api/water_pulse", json={"seconds": 600})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["requested_seconds"], 600)
        self.assertEqual(data["estimated_applied_seconds"], 100)
        self.assertTrue(data["will_be_limited"])
        command = json.loads(
            self.app_module.PATHS.command_path.read_text(encoding="utf-8")
        )
        self.assertEqual(command, {"type": "water_pulse", "seconds": 600.0})

        config["safety"]["max_watering_pulse_seconds"] = 600
        self.app_module.save_config(config)
        response = self.client.post("/api/water_pulse", json={"seconds": 600})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["estimated_applied_seconds"], 600)
        self.assertFalse(response.get_json()["will_be_limited"])

    def test_manual_watering_api_rejects_invalid_duration(self) -> None:
        response = self.client.post(
            "/api/water_pulse", json={"seconds": "nicht-zahl"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.app_module.PATHS.command_path.exists())

    def test_daemon_records_requested_and_applied_manual_duration(self) -> None:
        import web.automation_daemon as automation_module

        automation_module = importlib.reload(automation_module)
        config = self.app_module.load_config()
        config["safety"]["max_watering_pulse_seconds"] = 100
        config["safety"]["max_daily_watering_seconds"] = 600
        engine = automation_module.ControlEngine(
            automation_module.create_controller("legacy"), config
        )
        automation_module.save_json(
            automation_module.PATHS.command_path,
            {"type": "water_pulse", "seconds": 600},
        )

        _, state = automation_module.handle_command(
            config, {}, engine, datetime(2026, 8, 18, 12)
        )

        result = state["last_command_result"]
        self.assertEqual(result["requested_seconds"], 600)
        self.assertEqual(result["applied_seconds"], 100)
        self.assertTrue(result["limited"])
        self.assertIn("watering_pulse_limited", result["limit_reasons"])


if __name__ == "__main__":
    unittest.main()
