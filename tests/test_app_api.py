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
            repo="test-owner/test-repo",
            installation_id=5,
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

    def test_issue_crud_and_comments_routes_use_mocked_github_client(self):
        class FakeGitHub:
            def __init__(self):
                self.calls = []

            def create_issue(self, title, body):
                self.calls.append(("create", title, body))
                return {"number": 8, "title": title, "body": body}

            def update_issue(self, number, **changes):
                self.calls.append(("update", number, changes))
                return {"number": number, **changes}

            def list_comments(self, number):
                self.calls.append(("comments", number))
                return [{
                    "id": 77,
                    "body": '<!-- issueflow-ai:analysis:v1 {"category":"bug"} -->\n## AI 분석',
                }]

            def create_comment(self, number, body):
                self.calls.append(("comment", number, body))
                return {"id": 78, "body": body}

            def update_comment(self, comment_id, body):
                self.calls.append(("edit-comment", comment_id, body))
                return {"id": comment_id, "body": body}

            def delete_comment(self, comment_id):
                self.calls.append(("delete-comment", comment_id))

        fake = FakeGitHub()
        with patch(
            "app.get_installation_repositories",
            return_value=[{"full_name": "test-owner/test-repo"}],
        ), patch("agent.service.GitHubClient", return_value=fake):
            created = self.client.post("/api/issues", json={"title": "Test", "body": "Body"})
            edited = self.client.patch("/api/issues/8", json={"title": "Updated"})
            comment = self.client.post("/api/issues/8/comments", json={"body": "Hello"})
            metadata = '<!-- issueflow-ai:analysis:v1 {"category":"bug"} -->\n'
            edited_markdown = "## Updated Markdown  \n\n- detail\n\n"
            edited_comment = self.client.patch(
                "/api/issues/8/comments/77",
                json={"body": metadata + edited_markdown},
            )
            deleted = self.client.delete("/api/issues/8/comments/77", json={})

        self.assertEqual(created.status_code, 201)
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(comment.status_code, 201)
        self.assertEqual(edited_comment.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        self.assertIn(("create", "Test", "Body"), fake.calls)
        self.assertIn(("update", 8, {"title": "Updated"}), fake.calls)
        self.assertIn(("delete-comment", 77), fake.calls)
        self.assertIn(
            ("edit-comment", 77, metadata + edited_markdown),
            fake.calls,
        )

    def test_repository_initial_setup_never_overwrites_existing_labels_or_templates(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "full_name": "user-one/new-repo",
                    "html_url": "https://github.com/user-one/new-repo",
                }

        class Created(Response):
            status_code = 201

        with patch("app.requests.post", return_value=Created()) as create_request, \
             patch("app.requests.request", return_value=Response()) as setup_request:
            response = self.client.post("/api/github/repositories", json={
                "name": "new-repo",
                "private": True,
                "initialize_readme": True,
                "initialize_labels": True,
                "initialize_templates": True,
            })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(create_request.call_args.kwargs["json"]["auto_init"])
        self.assertTrue(all(call.args[0] == "GET" for call in setup_request.call_args_list))
        self.assertEqual(response.json["initialization"]["errors"], [])


if __name__ == "__main__":
    unittest.main()
