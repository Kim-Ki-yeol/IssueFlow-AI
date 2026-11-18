"""외부 API를 호출하지 않고 실제 모드의 처리 흐름만 확인."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agent.config import Config
from agent.github_client import COMMENT_MARKER, GitHubClient
from agent.service import Service


def make_config(writes=True):
    return Config(
        github_repo="test-owner/test-repo", github_token="test-token", openai_api_key="test-openai",
        openai_model="gpt-4.1-mini", discord_webhook_url="", github_writes=writes,
        dashboard_password="test-long-dashboard-password",
    )


ISSUE = {
    "number": 12, "title": "로그인 오류", "body": "로그인 시 500 오류가 발생합니다.",
    "labels": [], "analysis": None, "html_url": "https://github.com/test-owner/test-repo/issues/12",
    "created_at": "2026-11-01T12:00:00Z", "updated_at": "2026-11-01T12:00:00Z",
}
ANALYSIS = {
    "category": "bug", "priority": "high", "area": "인증 API", "summary": "로그인 실패",
    "probable_cause": "서버 오류 가능성", "suggestions": ["서버 로그 확인", "재현 조건 확인"],
    "check_recent_commits": False, "source": "gpt",
}


class FakeGitHub:
    def __init__(self):
        self.issue = dict(ISSUE)
        self.applied = []
        self.comments = []

    def get_issue(self, number):
        assert number == 12
        return dict(self.issue)

    def list_issues(self, limit=1000):
        return [dict(self.issue)][:limit]

    def apply_labels(self, number, labels, existing):
        self.applied.append((number, labels, existing))
        self.issue["labels"] = labels

    def upsert_comment(self, number, body):
        self.comments.append((number, body))
        self.issue["analysis"] = ANALYSIS

    def recent_commits(self):
        return []


class ServiceTests(unittest.TestCase):
    def test_analysis_never_writes_without_explicit_approval(self):
        github = FakeGitHub()
        service = Service(make_config())
        service.github = github
        with patch("agent.service.analyze_issue", return_value=ANALYSIS) as mock_gpt:
            result = service.run(12)
        mock_gpt.assert_called_once()
        self.assertFalse(result["applied"])
        self.assertEqual(github.applied, [])
        self.assertEqual(github.comments, [])
        self.assertEqual(result["analysis"], ANALYSIS)

    def test_approved_selected_changes_are_applied(self):
        github = FakeGitHub()
        service = Service(make_config())
        service.github = github
        with patch("agent.service.send_discord") as send_discord:
            result = service.apply(12, ANALYSIS, apply_labels=True, post_comment=True)
        self.assertEqual(result["status"], "applied")
        self.assertTrue(result["labels_applied"])
        self.assertTrue(result["comment_applied"])
        self.assertIn(COMMENT_MARKER, github.comments[0][1])
        send_discord.assert_not_called()

    def test_read_only_still_calls_real_gpt_but_does_not_write(self):
        github = FakeGitHub()
        service = Service(make_config(writes=False))
        service.github = github
        with patch("agent.service.analyze_issue", return_value=ANALYSIS) as mock_gpt:
            result = service.run(12)
        mock_gpt.assert_called_once()
        self.assertFalse(result["applied"])
        self.assertEqual(github.applied, [])
        self.assertEqual(github.comments, [])
        self.assertEqual(result["analysis"]["source"], "gpt")

    def test_comment_json_is_readable(self):
        raw = Service.comment(ANALYSIS)
        encoded = raw.split(COMMENT_MARKER, 1)[1].split(" -->", 1)[0]
        self.assertEqual(json.loads(encoded), ANALYSIS)

    def test_recent_activity_only_uses_github(self):
        service = Service(make_config())
        service.github = FakeGitHub()
        items = service.activity()
        self.assertEqual(items[0]["issue_number"], 12)
        self.assertEqual(items[0]["title"], "이슈 업데이트")

    def test_issueflow_labels_preserve_existing_and_replace_priority(self):
        c = GitHubClient("test-owner/test-repo", "dummy")
        calls = []
        c.ensure_labels = lambda labels: None
        c.request = lambda method, path, **kw: calls.append((method, path, kw))
        c.apply_labels(12, ["documentation", "priority:low", "ai:processed"], ["bug", "priority:high", "customer", "ai:processed"])
        self.assertEqual(calls[0][0], "PUT")
        self.assertEqual(calls[0][2]["json"]["labels"], ["bug", "customer", "documentation", "priority:low", "ai:processed"])

    def test_reanalysis_updates_previous_comment(self):
        c = GitHubClient("test-owner/test-repo", "dummy")
        calls = []
        c.find_analysis_comment = lambda number: (77, ANALYSIS)
        c.request = lambda method, path, **kw: calls.append((method, path, kw))
        c.upsert_comment(12, "comment")
        self.assertEqual(calls[0][:2], ("PATCH", "/issues/comments/77"))

    def test_issue_create_update_and_state_use_github_issue_api(self):
        c = GitHubClient("test-owner/test-repo", "dummy")
        calls = []
        response = {**ISSUE, "assignees": []}
        c.request = lambda method, path, **kw: calls.append((method, path, kw)) or response
        c.create_issue("New issue", "Description")
        c.update_issue(12, title="Edited", body="Updated")
        c.update_issue(12, state="closed")
        self.assertEqual(calls[0][:2], ("POST", "/issues"))
        self.assertEqual(calls[1][:2], ("GET", "/issues/12"))
        self.assertEqual(calls[2][2]["json"], {"title": "Edited", "body": "Updated"})
        self.assertEqual(calls[4][2]["json"], {"state": "closed", "state_reason": "completed"})

    def test_comments_assignment_and_priority_are_scoped_to_issue(self):
        c = GitHubClient("test-owner/test-repo", "dummy")
        calls = []
        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {**ISSUE, "assignees": []} if path == "/issues/12" else {}
        c.request = fake_request
        c.create_comment(12, "hello")
        c.update_comment(99, "updated")
        c.delete_comment(99)
        c.set_assignees(12, ["octocat"])
        c.ensure_labels = lambda labels: None
        c.set_priority(12, "medium", ["priority:high", "customer"])
        self.assertEqual(calls[0][:2], ("GET", "/issues/12"))
        self.assertEqual(calls[1][:2], ("POST", "/issues/12/comments"))
        self.assertEqual(calls[2][:2], ("PATCH", "/issues/comments/99"))
        self.assertEqual(calls[3][:2], ("DELETE", "/issues/comments/99"))
        self.assertEqual(calls[5][2]["json"], {"assignees": ["octocat"]})
        self.assertEqual(calls[6][2]["json"]["labels"], ["customer", "priority:medium"])

    def test_board_status_replaces_only_board_status_label(self):
        c = GitHubClient("test-owner/test-repo", "dummy")
        calls = []
        c.ensure_labels = lambda labels: None
        c.request = lambda method, path, **kw: calls.append((method, path, kw))
        c.apply_labels(12, ["status:in-progress"], ["status:todo", "priority:high", "customer"])
        self.assertEqual(
            calls[0][2]["json"]["labels"],
            ["priority:high", "customer", "status:in-progress"],
        )


if __name__ == "__main__":
    unittest.main()
