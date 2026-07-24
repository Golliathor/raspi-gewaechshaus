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
        self.assertEqual(data["active_controller"], "legacy")
        self.assertIn("last_decision_reasons", data)
        self.assertIn("last_safety_overrides", data)

    def test_config_form_uses_adc_names_and_persists_address(self) -> None:
        response = self.client.get("/config")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="adc_address"', response.data)
        self.assertNotIn(b"pcf8591_address", response.data)

        response = self.client.post(
            "/config",
            data={
                "adc_enabled": "on",
                "adc_type": "ADS1115",
                "adc_address": "73",
                "automation_enabled": "on",
                "watering_enabled": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.app_module.load_config()["adc_address"], 73)


if __name__ == "__main__":
    unittest.main()
