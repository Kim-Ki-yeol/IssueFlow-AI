"""Authenticated settings API tests; no external service requests."""
from __future__ import annotations

import os
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from agent import storage

# app.py creates its module-level Flask app during import. Keep that startup
# initialization isolated from any developer or production database.
_BOOTSTRAP_DATABASE = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {
    "ISSUEFLOW_DATABASE_PATH": os.path.join(_BOOTSTRAP_DATABASE.name, "bootstrap.sqlite3"),
    "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
    "VERCEL": "",
}):
    import app as app_module


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "ISSUEFLOW_DATABASE_PATH": os.path.join(self.temp.name, "api.sqlite3"),
            "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "VERCEL": "",
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

    def test_clear_endpoints_only_clear_the_current_users_requested_secret(self):
        storage.save_settings(
            101, "sk-current-user", "https://discord.com/api/webhooks/123/mock",
            "test-owner/test-repo",
        )
        storage.save_settings(202, "sk-other-user", "", "other-owner/other-repo")
        storage.save_analysis(
            101, "test-owner/test-repo", 12, "Saved history",
            {"category": "bug"}, "complete",
        )

        openai_response = self.client.delete("/api/settings/openai-key", json={})
        self.assertEqual(openai_response.status_code, 200)
        current = storage.get_settings(101)
        self.assertEqual(current["openai_api_key"], "")
        self.assertTrue(openai_response.json["discord_configured"])
        self.assertEqual(current["selected_repo"], "test-owner/test-repo")
        self.assertEqual(storage.get_settings(202)["openai_api_key"], "sk-other-user")

        discord_response = self.client.delete("/api/settings/discord-webhook", json={})
        self.assertEqual(discord_response.status_code, 200)
        self.assertFalse(discord_response.json["discord_configured"])
        self.assertEqual(storage.get_settings(202)["openai_api_key"], "sk-other-user")
        self.assertEqual(len(storage.history(101, "test-owner/test-repo")), 1)

    def test_analysis_history_clear_is_authenticated_user_scoped_and_preserves_settings(self):
        storage.save_settings(
            101, "sk-current-user", "https://discord.com/api/webhooks/123/mock",
            "test-owner/test-repo",
        )
        storage.save_settings(202, "sk-other-user", "", "other-owner/other-repo")
        storage.save_analysis(
            101, "test-owner/test-repo", 12, "GitHub issue",
            {"category": "bug"}, "complete",
        )
        storage.save_analysis(
            101, "previous-owner/repo", 3, "Old repository history",
            {"category": "feature"}, "complete",
        )
        storage.save_analysis(
            202, "other-owner/other-repo", 9, "Other user's history",
            {"category": "question"}, "complete",
        )

        with patch("app.get_installation_repositories") as github_repositories, \
             patch("agent.service.GitHubClient") as github_client:
            response = self.client.delete("/api/analysis-history", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["deleted"], 2)
        self.assertEqual(response.json["statistics"], {
            "total": 0, "applied": 0, "pending": 0, "failed": 0,
        })
        github_repositories.assert_not_called()
        github_client.assert_not_called()
        self.assertEqual(storage.history(101, "test-owner/test-repo"), [])
        self.assertEqual(storage.history(101, "previous-owner/repo"), [])
        self.assertEqual(len(storage.history(202, "other-owner/other-repo")), 1)
        self.assertEqual(storage.get_settings(101), {
            "openai_api_key": "sk-current-user",
            "discord_webhook_url": "https://discord.com/api/webhooks/123/mock",
            "selected_repo": "test-owner/test-repo",
        })
        self.assertEqual(storage.get_settings(202)["openai_api_key"], "sk-other-user")

        new_id = storage.save_analysis(
            101, "test-owner/test-repo", 12, "New analysis",
            {"category": "bug"}, "complete",
        )
        self.assertGreater(new_id, 0)
        self.assertEqual(storage.statistics(101, "test-owner/test-repo")["total"], 1)

    def test_installations_and_partial_repository_access_use_only_current_oauth_user(self):
        other_sid = "other-user-sid"
        app_module.LOGIN_SESSIONS[other_sid] = app_module.LoginSession(
            token="other-user-mock-token",
            user={"id": 202, "login": "user-two"},
            expires_at=9999999999,
        )
        other_client = self.flask_app.test_client()
        with other_client.session_transaction() as browser_session:
            browser_session["sid"] = other_sid

        def list_installations(token):
            if token == "non-production-test-token":
                return [{"id": 505, "account": "user-one", "repository_selection": "selected"}]
            if token == "other-user-mock-token":
                return []
            self.fail("Unexpected OAuth token used for installation lookup")

        def list_repositories(token, installation_id):
            if token == "non-production-test-token" and installation_id == 505:
                return [{"full_name": "user-one/approved-repo"}]
            if token == "other-user-mock-token":
                return []
            self.fail("Unexpected OAuth token used for repository lookup")

        try:
            with patch("app.get_installations", side_effect=list_installations) as get_installs, \
                 patch("app.get_installation_repositories", side_effect=list_repositories) as get_repos:
                first_user = self.client.get("/api/github/installations")
                first_repositories = self.client.get(
                    "/api/github/installations/505/repositories"
                )
                second_user = other_client.get("/api/github/installations")
                second_repositories = other_client.get(
                    "/api/github/installations/505/repositories"
                )
            self.assertEqual(first_user.json["items"][0]["id"], 505)
            self.assertEqual(
                first_repositories.json["items"],
                [{"full_name": "user-one/approved-repo"}],
            )
            self.assertEqual(second_user.json["items"], [])
            self.assertEqual(second_repositories.json["items"], [])
            get_installs.assert_any_call("non-production-test-token")
            get_installs.assert_any_call("other-user-mock-token")
            get_repos.assert_any_call("non-production-test-token", 505)
            get_repos.assert_any_call("other-user-mock-token", 505)
        finally:
            app_module.LOGIN_SESSIONS.pop(other_sid, None)

    def test_installation_lookup_failure_is_not_reported_as_uninstalled(self):
        with patch(
            "app.get_installations",
            side_effect=app_module.GitHubAuthError("GitHub lookup unavailable"),
        ):
            response = self.client.get("/api/github/installations")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("items", response.json)

    def test_repository_creation_permission_error_does_not_claim_app_is_uninstalled(self):
        class Denied:
            status_code = 403

        with patch("app.requests.post", return_value=Denied()):
            response = self.client.post("/api/github/repositories", json={
                "name": "mock-repository",
                "private": True,
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("App 설치 여부와 별개", response.json["error"])
        self.assertIn("계정의 저장소 생성 권한", response.json["error"])

    def test_missing_user_openai_key_blocks_analysis_without_history_or_global_fallback(self):
        class FakeGitHub:
            pass

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-global-test-only"}), \
             patch(
                 "app.get_installation_repositories",
                 return_value=[{"full_name": "test-owner/test-repo"}],
             ), patch("agent.service.GitHubClient", return_value=FakeGitHub()), \
             patch("app.save_analysis") as save_analysis, \
             patch("agent.service.analyze_issue") as analyze_issue:
            response = self.client.post("/api/issues/12/analyze", json={})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["code"], "OPENAI_API_KEY_REQUIRED")
        analyze_issue.assert_not_called()
        save_analysis.assert_not_called()
        self.assertFalse(storage.setting_status(101)["openai_configured"])

    def test_settings_api_requires_login(self):
        self.client.delete_cookie("session")
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.delete("/api/settings/openai-key", json={}).status_code,
            401,
        )
        self.assertEqual(
            self.client.delete("/api/settings/discord-webhook", json={}).status_code,
            401,
        )
        self.assertEqual(
            self.client.delete("/api/analysis-history", json={}).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/github/installations").status_code, 401)

    def test_local_logout_revokes_session_but_preserves_user_settings(self):
        storage.save_settings(101, "sk-mock-openai-key", "", "test-owner/test-repo")
        storage.save_analysis(
            101, "test-owner/test-repo", 12, "Saved before logout",
            {"category": "bug"}, "complete",
        )
        response = self.client.post("/api/auth/logout", json={})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("test-sid", app_module.LOGIN_SESSIONS)
        self.assertFalse(self.client.get("/api/auth/me").json["authenticated"])
        self.assertEqual(self.client.get("/api/settings").status_code, 401)
        self.assertEqual(
            storage.get_settings(101)["selected_repo"],
            "test-owner/test-repo",
        )
        self.assertEqual(
            [item["issue_title"] for item in storage.history(101, "test-owner/test-repo")],
            ["Saved before logout"],
        )

    def test_account_settings_and_analysis_history_are_isolated_across_sessions(self):
        other_sid = "account-b-sid"
        other_token = "account-b-mock-token"
        repo_a = "test-owner/test-repo"
        repo_b = "other-owner/other-repo"
        storage.save_settings(
            101, "sk-user-a-only", "https://discord.com/api/webhooks/123/user-a",
            repo_a,
        )
        storage.save_analysis(
            101, repo_a, 11, "Account A issue",
            {"category": "bug", "summary": "A-only result"}, "complete",
        )
        app_module.LOGIN_SESSIONS[other_sid] = app_module.LoginSession(
            token=other_token,
            user={"id": 202, "login": "user-two"},
            expires_at=9999999999,
            repo=repo_b,
            installation_id=7,
        )
        other_client = self.flask_app.test_client()
        with other_client.session_transaction() as browser_session:
            browser_session["sid"] = other_sid

        class FakeGitHub:
            @staticmethod
            def get_issue(number):
                return {
                    "number": number, "title": "Account B issue", "body": "",
                    "labels": [], "html_url": "https://github.com/other-owner/other-repo/issues/12",
                }

            @staticmethod
            def recent_commits():
                return []

        def repositories(token, installation_id):
            if token == "non-production-test-token" and installation_id == 5:
                return [{"full_name": repo_a}]
            if token == other_token and installation_id == 7:
                return [{"full_name": repo_b}]
            self.fail("Repository access must use the authenticated user's session")

        try:
            with patch("app.get_installation_repositories", side_effect=repositories), \
                 patch("agent.service.GitHubClient", return_value=FakeGitHub()), \
                 patch("agent.service.analyze_issue") as analyze_issue:
                settings_a = self.client.get("/api/settings")
                settings_b = other_client.get("/api/settings")
                history_a = self.client.get("/api/analysis-history")
                history_b = other_client.get("/api/analysis-history")
                blocked = other_client.post("/api/issues/12/analyze", json={})
                discord_unavailable = other_client.post(
                    "/api/settings/test-discord", json={}
                )

            self.assertTrue(settings_a.json["openai_configured"])
            self.assertTrue(settings_a.json["discord_configured"])
            self.assertFalse(settings_b.json["openai_configured"])
            self.assertFalse(settings_b.json["discord_configured"])
            self.assertNotIn("sk-user-a-only", settings_b.get_data(as_text=True))
            self.assertNotIn("user-a", settings_b.get_data(as_text=True))
            self.assertEqual([item["issue_title"] for item in history_a.json["items"]],
                             ["Account A issue"])
            self.assertEqual(history_b.json["items"], [])
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json["code"], "OPENAI_API_KEY_REQUIRED")
            analyze_issue.assert_not_called()
            self.assertEqual(discord_unavailable.status_code, 400)
            self.assertIn("먼저 Discord Webhook", discord_unavailable.json["error"])

            own_key = other_client.put("/api/settings", json={
                "openai_api_key": "sk-user-b-only",
                "discord_webhook_url": "",
            })
            self.assertEqual(own_key.status_code, 200)
            self.assertTrue(own_key.json["openai_configured"])

            analysis = {
                "category": "bug", "priority": "medium", "area": "API",
                "summary": "Account B analysis", "probable_cause": "검토 필요",
                "suggestions": ["로그 확인"], "check_recent_commits": False,
                "solution": "확인", "recommended_labels": ["bug"],
            }
            with patch("app.get_installation_repositories", side_effect=repositories), \
                 patch("agent.service.GitHubClient", return_value=FakeGitHub()), \
                 patch("agent.service.analyze_issue", return_value=analysis):
                analyzed = other_client.post("/api/issues/12/analyze", json={})
                history_b_after = other_client.get("/api/analysis-history")

            self.assertEqual(analyzed.status_code, 200)
            self.assertEqual(
                [item["issue_title"] for item in history_b_after.json["items"]],
                ["Account B issue"],
            )
            self.assertEqual(
                [item["issue_title"] for item in storage.history(101, repo_a)],
                ["Account A issue"],
            )
            self.assertEqual(storage.get_settings(101)["openai_api_key"], "sk-user-a-only")
            self.assertEqual(storage.get_settings(202)["openai_api_key"], "sk-user-b-only")
        finally:
            app_module.LOGIN_SESSIONS.pop(other_sid, None)

    def test_flask_serves_frontend_assets_and_vercel_checks_filesystem_first(self):
        home = self.client.get("/")
        js = self.client.get("/app.js")
        css = self.client.get("/styles.css")
        markup = home.get_data(as_text=True)
        script = js.get_data(as_text=True)
        self.assertEqual(home.status_code, 200)
        self.assertIn('id="setupLoading"', markup)
        self.assertRegex(markup, r'id="setupLogin"[^>]*hidden')
        self.assertRegex(markup, r'<button id="welcomeStartBtn"[^>]*type="button"')
        self.assertRegex(markup, r'<section id="setupOauth"[^>]*hidden')
        self.assertRegex(markup, r'<a id="githubLoginBtn"[^>]*href="/auth/github"')
        self.assertIn('id="dashboardBackToRepositoryBtn"', markup)
        self.assertIn('id="otherGithubAccountBtn"', markup)
        self.assertIn("GitHub 브라우저 로그인은 유지됩니다.", markup)
        self.assertIn("GitHub에 로그인된 계정이 자동 선택될 수 있습니다.", markup)
        self.assertEqual(js.status_code, 200)
        self.assertEqual(css.status_code, 200)
        self.assertIn("javascript", js.mimetype)
        self.assertEqual(css.mimetype, "text/css")
        self.assertIn('id="clearOpenaiBtn"', markup)
        self.assertIn('id="clearDiscordBtn"', markup)
        self.assertIn("clearOpenaiBtn').addEventListener('click'", script)
        self.assertIn("clearDiscordBtn').addEventListener('click'", script)
        self.assertIn("dashboardBackToRepositoryBtn').addEventListener('click', openRepositorySetup)", script)
        self.assertIn("otherGithubAccountBtn').addEventListener('click'", script)
        self.assertIn("disconnectWorkspace({ switchAccount: true })", script)
        self.assertIn("resetRepositoryScopedView()", script)
        self.assertIn('id="clearAnalysisHistoryBtn"', markup)
        self.assertIn("clearAnalysisHistoryBtn').addEventListener('click'", script)
        self.assertIn("renderAnalysisStats(result.statistics)", script)
        self.assertIn('id="githubAppNotice"', markup)
        self.assertIn('href="https://github.com/apps/issueflow-ai-giyeol/installations/new"', markup)
        self.assertIn('target="_blank"', markup)
        self.assertIn('id="recheckGithubAppBtn"', markup)
        self.assertIn("renderGithubAppNotice('missing')", script)
        self.assertIn("'check-error'", script)
        self.assertIn("refreshInstallationAfterReturn", script)
        self.assertIn("installGithubAppBtn').addEventListener('click'", script)
        self.assertIn("installationPageOpened = true", script)
        self.assertIn("recheckGithubAppBtn').addEventListener('click'", script)
        self.assertIn("window.addEventListener('focus', refreshInstallationAfterReturn)", script)
        self.assertIn("state.config?.ai_enabled", script)
        self.assertIn("OPENAI_API_KEY_REQUIRED", script)
        self.assertIn("clearOpenaiBtn').addEventListener('click'", script)
        self.assertIn("clearDiscordBtn').addEventListener('click'", script)
        home.close()
        js.close()
        css.close()

        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("![IssueFlow AI 시스템 아키텍처](docs/architecture/issueflow-architecture.svg)", readme)
        self.assertTrue((root / "docs" / "architecture" / "issueflow-architecture.svg").is_file())
        self.assertTrue((root / "docs" / "architecture" / "issueflow-architecture.png").is_file())
        config = json.loads((root / "vercel.json").read_text(encoding="utf-8"))
        self.assertNotIn("routes", config)
        rewrites = {item["source"]: item["destination"] for item in config["rewrites"]}
        self.assertEqual(rewrites["/app.js"], "/public/app.js")
        self.assertEqual(rewrites["/styles.css"], "/public/styles.css")
        self.assertEqual(rewrites["/(.*)"], "/app.py")
        self.assertNotIn("functions", config)
        self.assertTrue(any(
            item["src"] == "app.py" and item["use"] == "@vercel/python"
            for item in config["builds"]
        ))
        self.assertTrue(any(
            item["src"] == "public/**" and item["use"] == "@vercel/static"
            for item in config["builds"]
        ))
        self.assertTrue((root / "templates" / "index.html").is_file())
        self.assertTrue((root / "public" / "app.js").is_file())
        self.assertTrue((root / "public" / "styles.css").is_file())

    def test_persistent_session_is_loaded_and_deleted_server_side(self):
        stored = {
            "token": "mock-encrypted-server-token",
            "user": {"id": 101, "login": "user-one"},
            "expires_at": 9999999999,
            "repo": "test-owner/test-repo",
            "installation_id": 5,
        }
        with patch("app.persistent_sessions_enabled", return_value=True), \
             patch("app.load_login_session", return_value=stored) as load_session:
            response = self.client.get("/api/auth/me")
        self.assertTrue(response.json["authenticated"])
        self.assertEqual(response.json["selected_repo"], "test-owner/test-repo")
        self.assertNotIn("token", response.get_data(as_text=True))
        load_session.assert_called_once()

        with patch("app.persistent_sessions_enabled", return_value=True), \
             patch("app.delete_login_session") as delete_session:
            response = self.client.post("/api/auth/logout", json={})
        self.assertEqual(response.status_code, 200)
        delete_session.assert_called_once_with("test-sid")
        self.assertFalse(self.client.get("/api/auth/me").json["authenticated"])
        self.assertEqual(self.client.get("/api/settings").status_code, 401)

    def test_vercel_requires_stable_flask_key_and_sets_secure_cookie(self):
        with patch.dict(os.environ, {
            "VERCEL": "1",
            "GITHUB_OAUTH_REDIRECT_URI": "https://issue-flow-ai.vercel.app/auth/github/callback",
        }), \
             patch("app.get_github_app_config", return_value=SimpleNamespace(flask_secret_key="fixed-test-key")), \
             patch("app.init_db"):
            vercel_app = app_module.create_app()
        self.assertTrue(vercel_app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(vercel_app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(vercel_app.config["SESSION_COOKIE_SAMESITE"], "Lax")

        with patch.dict(os.environ, {
            "VERCEL": "1",
            "GITHUB_OAUTH_REDIRECT_URI": "https://issue-flow-ai.vercel.app/auth/github/callback",
        }), \
             patch("app.get_github_app_config", return_value=SimpleNamespace(flask_secret_key="")), \
             patch("app.init_db"):
            with self.assertRaisesRegex(RuntimeError, "FLASK_SECRET_KEY"):
                app_module.create_app()

    def test_oauth_callback_uri_is_stored_with_state_and_reused_on_exchange(self):
        callback = "http://localhost/auth/github/callback"
        with patch("app.oauth_redirect_uri", return_value=callback), \
             patch("app.create_login_url", return_value=("https://github.test/authorize", "mock-state")) as authorize, \
             patch("app.exchange_code", return_value="mock-github-token") as exchange, \
             patch("app.get_current_user", return_value={"id": 303, "login": "mock-user"}):
            login_response = self.client.get("/auth/github")
            self.assertEqual(login_response.status_code, 302)
            authorize.assert_called_once_with(callback)
            with self.client.session_transaction() as browser_session:
                self.assertEqual(browser_session["oauth_state"], "mock-state")
                self.assertEqual(browser_session["oauth_redirect_uri"], callback)
            response = self.client.get(
                "/auth/github/callback?state=mock-state&code=mock-code"
            )
        self.assertEqual(response.status_code, 302)
        exchange.assert_called_once_with("mock-code", redirect_uri=callback)
        with self.client.session_transaction() as browser_session:
            created_sid = browser_session.get("sid")
        if created_sid:
            app_module.LOGIN_SESSIONS.pop(created_sid, None)

    def test_relogin_requires_repository_selection_and_preserves_saved_settings(self):
        callback = "http://localhost/auth/github/callback"
        storage.save_settings(
            303, "sk-mock-user-key",
            "https://discord.com/api/webhooks/123/user-303",
            "test-owner/test-repo",
        )
        storage.save_analysis(
            303, "test-owner/test-repo", 6, "Existing repository history",
            {"category": "bug"}, "complete",
        )
        with patch("app.oauth_redirect_uri", return_value=callback), \
             patch("app.create_login_url", return_value=("https://github.test/authorize", "state-relogin")), \
             patch("app.exchange_code", return_value="mock-github-token") as exchange, \
             patch("app.get_current_user", return_value={"id": 303, "login": "mock-user"}), \
             patch("app.get_installations") as installations, \
             patch("app.get_installation_repositories", return_value=[
                 {"full_name": "test-owner/test-repo"},
             ]):
            self.client.get("/auth/github")
            response = self.client.get(
                "/auth/github/callback?state=state-relogin&code=mock-code"
            )
            auth = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(auth.json["authenticated"])
        self.assertEqual(auth.json["selected_repo"], "")
        installations.assert_not_called()
        self.assertEqual(
            storage.get_settings(303)["selected_repo"],
            "test-owner/test-repo",
        )
        self.assertEqual(
            [item["issue_title"] for item in storage.history(303, "test-owner/test-repo")],
            ["Existing repository history"],
        )
        exchange.assert_called_once_with("mock-code", redirect_uri=callback)

        with patch(
            "app.get_installation_repositories",
            return_value=[{"full_name": "test-owner/other-repo"}],
        ):
            selected = self.client.post("/api/github/select-repository", json={
                "repo": "test-owner/other-repo",
                "installation_id": 7,
            })
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(
            self.client.get("/api/auth/me").json["selected_repo"],
            "",
        )
        self.assertEqual(
            storage.get_settings(303)["selected_repo"],
            "test-owner/test-repo",
        )

        with patch(
            "app.get_installation_repositories",
            return_value=[{"full_name": "test-owner/other-repo"}],
        ):
            connected = self.client.put("/api/settings", json={
                "openai_api_key": "",
                "discord_webhook_url": "",
            })
        self.assertEqual(connected.status_code, 200)
        self.assertEqual(
            self.client.get("/api/auth/me").json["selected_repo"],
            "test-owner/other-repo",
        )
        settings = storage.get_settings(303)
        self.assertEqual(settings["selected_repo"], "test-owner/other-repo")
        self.assertEqual(settings["openai_api_key"], "sk-mock-user-key")
        self.assertEqual(
            settings["discord_webhook_url"],
            "https://discord.com/api/webhooks/123/user-303",
        )
        storage.save_analysis(
            303, "test-owner/other-repo", 7, "Other repository history",
            {"category": "feature"}, "complete",
        )
        self.assertEqual(
            [item["issue_title"] for item in storage.history(303, "test-owner/test-repo")],
            ["Existing repository history"],
        )
        with patch(
            "app.get_installation_repositories",
            return_value=[{"full_name": "test-owner/other-repo"}],
        ), patch("agent.service.GitHubClient"):
            selected_history = self.client.get("/api/analysis-history")
        self.assertEqual(
            [item["issue_title"] for item in selected_history.json["items"]],
            ["Other repository history"],
        )
        with self.client.session_transaction() as browser_session:
            created_sid = browser_session.get("sid")
        if created_sid:
            app_module.LOGIN_SESSIONS.pop(created_sid, None)

    def test_callback_uri_is_environment_specific_and_validated(self):
        with patch.dict(os.environ, {
            "VERCEL": "",
            "GITHUB_OAUTH_REDIRECT_URI": "",
        }), self.flask_app.test_request_context(
            "/", base_url="http://localhost"
        ):
            self.assertEqual(
                app_module.oauth_redirect_uri(),
                "http://localhost/auth/github/callback",
            )

        production_callback = "https://issue-flow-ai.vercel.app/auth/github/callback"
        with patch.dict(os.environ, {
            "VERCEL": "1",
            "GITHUB_OAUTH_REDIRECT_URI": production_callback,
        }):
            self.assertEqual(app_module.oauth_redirect_uri(), production_callback)
        with patch.dict(os.environ, {
            "VERCEL": "1",
            "GITHUB_OAUTH_REDIRECT_URI": "http://issue-flow-ai.vercel.app/auth/github/callback",
        }):
            with self.assertRaisesRegex(RuntimeError, "형식"):
                app_module.oauth_redirect_uri()

    def test_login_path_is_auth_github_not_auth_github_login(self):
        response = self.client.get("/auth/github/login")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("로그인 검증에 실패했습니다", response.get_data(as_text=True))

    def test_oauth_start_rejects_a_preview_host_mismatched_with_registered_callback(self):
        callback = "https://issue-flow-ai.vercel.app/auth/github/callback"
        with patch("app.oauth_redirect_uri", return_value=callback), \
             patch("app.create_login_url") as create_url:
            response = self.client.get(
                "/auth/github",
                base_url="https://issue-flow-ai-preview.vercel.app",
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("auth_error=github", response.headers["Location"])
        create_url.assert_not_called()

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

    def test_issue_list_keeps_latest_successful_analysis_after_failed_retry(self):
        class FakeGitHub:
            def list_issues(self):
                return [{
                    "number": number, "title": f"Analyzed {number}", "body": "",
                    "state": "open", "labels": [], "analysis": None,
                    "created_at": "2026-01-01T00:00:00Z",
                    "repository": "test-owner/test-repo",
                } for number in (12, 13, 14)]

        successful = {
            "id": 4, "issue_number": 12, "analysis_status": "complete",
            "github_status": "applied", "discord_status": "sent",
            "analysis": {"category": "bug", "summary": "Stored result"},
        }
        failed_retry = {
            "id": 5, "issue_number": 12, "analysis_status": "failed",
            "github_status": "pending", "discord_status": "not_sent",
            "analysis": None,
        }
        other_successes = [{
            "id": number - 11, "issue_number": number, "analysis_status": "complete",
            "github_status": "pending", "discord_status": "not_sent",
            "analysis": {"category": "bug", "summary": f"Stored result {number}"},
        } for number in (13, 14)]
        with patch(
            "app.get_installation_repositories",
            return_value=[{"full_name": "test-owner/test-repo"}],
        ), patch("agent.service.GitHubClient", return_value=FakeGitHub()), \
             patch("app.get_history", return_value=[failed_retry, successful, *other_successes]):
            response = self.client.get("/api/issues")
        self.assertEqual(response.status_code, 200)
        items = response.json["items"]
        self.assertEqual(sum(bool(issue.get("analysis")) for issue in items), 3)
        issue = next(item for item in items if item["number"] == 12)
        self.assertEqual(issue["analysis_id"], 4)
        self.assertEqual(issue["analysis"]["summary"], "Stored result")
        self.assertEqual(issue["analysis_status"], "applied")

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
