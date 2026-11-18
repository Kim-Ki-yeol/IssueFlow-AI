"""실제 GitHub Issues 조회와 라벨·댓글 수정에 사용하는 코드."""
from __future__ import annotations

import json
import re
from urllib.parse import quote

import requests

COMMENT_MARKER = "<!-- issueflow-ai:analysis:v1 "
COLORS = {
    "bug": "ef4444", "enhancement": "8b5cf6", "documentation": "0ea5e9", "question": "6b7280",
    "priority:high": "dc2626", "priority:medium": "f59e0b", "priority:low": "22c55e",
    "ai:processed": "6366f1",
}


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, repo: str, token: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise GitHubError("GITHUB_REPO는 계정명/저장소명 형식이어야 합니다.")
        if not token:
            raise GitHubError("GITHUB_TOKEN을 설정해야 합니다.")
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IssueFlowAI/2.0",
        })

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.session.request(
                method, f"https://api.github.com/repos/{self.repo}{path}", timeout=20, **kwargs
            )
        except requests.RequestException as exc:
            raise GitHubError(f"GitHub 연결 실패: {type(exc).__name__}") from exc
        if not response.ok:
            try:
                reason = response.json().get("message", "")
            except ValueError:
                reason = ""
            raise GitHubError(f"GitHub API {response.status_code}: {str(reason)[:160]}")
        return response.json() if response.content else None

    @staticmethod
    def normalize(issue: dict, repo: str) -> dict:
        return {
            "number": issue["number"],
            "title": issue.get("title", ""),
            "body": issue.get("body") or "",
            "state": issue.get("state", "open"),
            "labels": [v.get("name", "") if isinstance(v, dict) else str(v) for v in issue.get("labels", [])],
            "analysis": None,
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "html_url": issue.get("html_url"),
            "repository": repo,
        }

    def list_issues(self) -> list[dict]:
        # 최근 변경된 열린 이슈 60개만 표시. PR은 제외
        items = self.request("GET", "/issues", params={"state": "open", "per_page": 60, "sort": "updated", "direction": "desc"})
        return [self.normalize(item, self.repo) for item in items if "pull_request" not in item]

    def get_issue(self, number: int) -> dict:
        issue = self.request("GET", f"/issues/{number}")
        if "pull_request" in issue:
            raise GitHubError("Pull Request는 분석하지 않습니다.")
        result = self.normalize(issue, self.repo)
        result["analysis"] = self.read_analysis(number)
        return result

    def recent_commits(self) -> list[dict]:
        items = self.request("GET", "/commits", params={"per_page": 5})
        return [{"sha": v["sha"][:7], "message": v.get("commit", {}).get("message", "").splitlines()[0]} for v in items]

    def find_analysis_comment(self, number: int) -> tuple[int, dict] | None:
        # GitHub 댓글에 분석 결과를 보관. 재분석할 때는 같은 댓글을 수정
        comments = self.request("GET", f"/issues/{number}/comments", params={"per_page": 100})
        for comment in reversed(comments):
            body = comment.get("body") or ""
            if not body.startswith(COMMENT_MARKER):
                continue
            match = re.search(r"^<!-- issueflow-ai:analysis:v1 (\{[^\n]*\}) -->", body)
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and data.get("source") == "gpt" and data.get("category") in ("bug", "feature", "documentation", "question"):
                return comment["id"], data
        return None

    def read_analysis(self, number: int) -> dict | None:
        found = self.find_analysis_comment(number)
        return found[1] if found else None

    def ensure_labels(self, labels: list[str]) -> None:
        for label in labels:
            if label not in COLORS:
                raise GitHubError(f"허용하지 않는 라벨: {label}")
            try:
                self.request("GET", f"/labels/{quote(label, safe='')}")
            except GitHubError as exc:
                if "GitHub API 404:" not in str(exc):
                    raise
                try:
                    self.request("POST", "/labels", json={"name": label, "color": COLORS[label]})
                except GitHubError as create_error:
                    if "GitHub API 422:" not in str(create_error):
                        raise

    def apply_labels(self, number: int, labels: list[str], existing: list[str]) -> None:
        # Preserve unrelated labels; replace only conflicting priority values.
        self.ensure_labels(labels)
        has_new_priority = any(label.startswith("priority:") for label in labels)
        remaining = [
            label for label in existing
            if label not in labels
            and not (has_new_priority and label.startswith("priority:"))
        ]
        updated = list(dict.fromkeys(remaining + labels))
        self.request("PUT", f"/issues/{number}/labels", json={"labels": updated})

    def upsert_comment(self, number: int, body: str) -> None:
        previous = self.find_analysis_comment(number)
        if previous:
            self.request("PATCH", f"/issues/comments/{previous[0]}", json={"body": body})
        else:
            self.request("POST", f"/issues/{number}/comments", json={"body": body})
