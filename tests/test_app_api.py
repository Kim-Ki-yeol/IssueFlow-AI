"""Authenticated settings API tests; no external service requests."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

import app as app_module


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "ISSUEFLOW_DATABASE_PATH": os.path.join(self.temp.name, "api.sqlite3"),
            "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        })
        self.env.start()
        self.flask_app = app_module.create_app()
        self.client = self.flask_app.test_client()
        app_module.LOGIN_SESSIONS["test-sid"] = app_module.LoginSession(
            token="non-production-test-token",
            user={"id": 101, "login": "user-one"},
            expires_at=9999999999,
        )
        with self.client.session_transaction() as browser_session:
            browser_session["sid"] = "test-sid"

    def tearDown(self):
        app_module.LOGIN_SESSIONS.pop("test-sid", None)
        self.env.stop()
        self.temp.cleanup()

    def test_secret_is_saved_but_never_returned(self):
        response = self.client.put("/api/settings", json={
            "openai_api_key": "sk-test-never-return-this",
            "discord_webhook_url": "https://discord.com/api/webhooks/123/test_token",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["openai_configured"])
        self.assertNotIn("sk-test-never-return-this", response.get_data(as_text=True))
        status = self.client.get("/api/settings")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(set(status.json), {"openai_configured", "discord_configured"})

    def test_settings_api_requires_login(self):
        self.client.delete_cookie("session")
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
