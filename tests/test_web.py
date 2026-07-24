from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
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
        ):
            self.assertIn(element_id, response.data)
        self.assertIn(b"Baseline 1", response.data)
        self.assertIn(b"Ansatz B", response.data)
        self.assertNotIn(b"data: {\\n    data:", response.data)

    def test_config_form_uses_adc_names_and_persists_address(self) -> None:
        response = self.client.get("/config")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="adc_address"', response.data)
        self.assertNotIn(b"pcf8591_address", response.data)
        self.assertIn(b'name="controller_active"', response.data)
        self.assertIn(
            b'name="adaptive_local_trend_window_seconds"',
            response.data,
        )
        self.assertIn(b'name="weather_latitude"', response.data)
        self.assertIn(
            b'name="adaptive_weather_weather_max_age_seconds"',
            response.data,
        )

        response = self.client.post(
            "/config",
            data={
                "adc_enabled": "on",
                "adc_type": "ADS1115",
                "adc_address": "73",
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


if __name__ == "__main__":
    unittest.main()
