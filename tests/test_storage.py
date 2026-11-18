"""SQLite storage tests without contacting external services."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from agent import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "ISSUEFLOW_DATABASE_PATH": os.path.join(self.temp.name, "test.sqlite3"),
            "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        })
        self.env.start()
        storage.init_db()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_secrets_are_encrypted_and_user_scoped(self):
        storage.save_settings("user-1", "sk-sensitive-test", "https://discord.com/api/webhooks/12/token", "owner/repo")
        with open(os.environ["ISSUEFLOW_DATABASE_PATH"], "rb") as database_file:
            raw = database_file.read()
        self.assertNotIn(b"sk-sensitive-test", raw)
        self.assertNotIn(b"discord.com", raw)
        self.assertTrue(storage.setting_status("user-1")["openai_configured"])
        self.assertFalse(storage.setting_status("user-2")["openai_configured"])
        self.assertEqual(storage.get_settings("user-2")["selected_repo"], "")

    def test_history_and_statistics_are_scoped_by_user_and_repository(self):
        item_id = storage.save_analysis(
            "user-1", "owner/repo", 4, "Bug title",
            {"category": "bug"}, "complete",
        )
        storage.update_analysis("user-1", item_id, github_status="applied", discord_status="sent")
        storage.save_analysis(
            "user-2", "owner/repo", 7, "Other",
            None, "failed", "test error",
        )
        self.assertEqual(len(storage.history("user-1", "owner/repo")), 1)
        self.assertEqual(storage.statistics("user-1", "owner/repo"), {
            "total": 1, "applied": 1, "pending": 0, "failed": 0,
        })


if __name__ == "__main__":
    unittest.main()
