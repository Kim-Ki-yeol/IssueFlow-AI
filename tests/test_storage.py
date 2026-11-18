"""SQLite storage tests without contacting external services."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
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
            "VERCEL": "",
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
        storage.init_db()
        self.assertTrue(storage.setting_status("user-1")["openai_configured"])
        self.assertEqual(
            storage.get_settings("user-1")["openai_api_key"],
            "sk-sensitive-test",
        )
        self.assertFalse(storage.setting_status("user-2")["openai_configured"])
        self.assertEqual(storage.get_settings("user-2")["selected_repo"], "")

    def test_clearing_one_integration_preserves_other_user_data_and_history(self):
        storage.save_settings(
            "user-1", "sk-user-one", "https://discord.com/api/webhooks/12/token",
            "owner/repo",
        )
        storage.save_settings("user-2", "sk-user-two", "", "owner/other")
        storage.save_analysis(
            "user-1", "owner/repo", 9, "Existing history",
            {"category": "bug"}, "complete",
        )

        storage.clear_user_integration("user-1", "openai")
        first = storage.get_settings("user-1")
        self.assertEqual(first["openai_api_key"], "")
        self.assertEqual(first["discord_webhook_url"], "https://discord.com/api/webhooks/12/token")
        self.assertEqual(first["selected_repo"], "owner/repo")
        self.assertEqual(len(storage.history("user-1", "owner/repo")), 1)
        self.assertEqual(storage.get_settings("user-2")["openai_api_key"], "sk-user-two")

        storage.clear_user_integration("user-1", "discord")
        self.assertFalse(storage.setting_status("user-1")["discord_configured"])
        self.assertTrue(storage.setting_status("user-2")["openai_configured"])
        self.assertEqual(len(storage.history("user-1", "owner/repo")), 1)

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
        failed_apply_id = storage.save_analysis(
            "user-1", "owner/repo", 5, "Apply failed",
            {"category": "bug"}, "complete",
        )
        storage.update_analysis("user-1", failed_apply_id, github_status="failed")
        partial_apply_id = storage.save_analysis(
            "user-1", "owner/repo", 6, "Partial apply",
            {"category": "feature"}, "complete",
        )
        storage.update_analysis("user-1", partial_apply_id, github_status="partial")
        self.assertEqual(len(storage.history("user-1", "owner/repo")), 3)
        self.assertEqual(storage.statistics("user-1", "owner/repo"), {
            "total": 3, "applied": 1, "pending": 2, "failed": 0,
        })
        self.assertEqual(storage.statistics("user-2", "owner/repo"), {
            "total": 1, "applied": 0, "pending": 0, "failed": 1,
        })

    def test_login_session_token_is_encrypted_and_expiry_is_enforced(self):
        database = sqlite3.connect(os.environ["ISSUEFLOW_DATABASE_PATH"])
        try:
            database.execute("""
                CREATE TABLE auth_sessions (
                    sid TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    encrypted_token BLOB NOT NULL, user_json TEXT NOT NULL,
                    expires_at REAL NOT NULL, selected_repo TEXT NOT NULL DEFAULT '',
                    installation_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            database.commit()
        finally:
            database.close()
        token = "gho-mock-token-that-must-not-be-plaintext"
        storage.save_login_session(
            "random-session-id", {"id": 42, "login": "mock-user"},
            token, time.time() + 600, "owner/repo", 7,
        )
        with open(os.environ["ISSUEFLOW_DATABASE_PATH"], "rb") as database_file:
            raw = database_file.read()
        self.assertNotIn(token.encode(), raw)
        restored = storage.load_login_session("random-session-id")
        self.assertEqual(restored["token"], token)
        self.assertEqual(restored["repo"], "owner/repo")
        storage.delete_login_session("random-session-id")
        self.assertIsNone(storage.load_login_session("random-session-id"))

    def test_database_selection_uses_sqlite_locally_and_postgres_only_on_vercel(self):
        with patch.dict(os.environ, {
            "VERCEL": "", "DATABASE_URL": "postgresql://mock.invalid/db",
        }):
            self.assertEqual(storage.database_backend(), "sqlite")
        with patch.dict(os.environ, {
            "VERCEL": "1", "DATABASE_URL": "postgresql://mock.invalid/db",
        }):
            self.assertEqual(storage.database_backend(), "postgres")
        with patch.dict(os.environ, {"VERCEL": "1", "DATABASE_URL": ""}):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                storage.database_backend()

    def test_postgres_adapter_translates_existing_qmark_sql(self):
        class FakeConnection:
            def execute(self, query, parameters):
                return query, parameters

        adapter = storage._PostgresConnection(FakeConnection())
        self.assertEqual(
            adapter.execute("SELECT * FROM t WHERE id = ? AND owner = ?", (7, "u")),
            ("SELECT * FROM t WHERE id = %s AND owner = %s", (7, "u")),
        )


if __name__ == "__main__":
    unittest.main()
