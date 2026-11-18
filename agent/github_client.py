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
    "status:todo": "94a3b8", "status:in-progress": "3b82f6", "status:done": "22c55e",
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
            "assignees": [
                person.get("login", "") for person in issue.get("assignees", [])
            ],
        }

    def list_issues(self, limit: int = 1000) -> list[dict]:
        items = []
        limit = max(1, min(int(limit), 1000))
        per_page = min(limit, 100)
        for page in range(1, (limit + per_page - 1) // per_page + 1):
            batch = self.request(
                "GET", "/issues",
                params={
                    "state": "all", "per_page": per_page, "page": page,
                    "sort": "updated", "direction": "desc",
                },
            )
            items.extend(batch[:limit - len(items)])
            if len(batch) < per_page or len(items) >= limit:
                break
        return [self.normalize(item, self.repo) for item in items if "pull_request" not in item]

    def create_issue(self, title: str, body: str) -> dict:
        issue = self.request("POST", "/issues", json={"title": title, "body": body})
        return self.normalize(issue, self.repo)

    def assert_issue(self, number: int) -> dict:
        issue = self.request("GET", f"/issues/{number}")
        if "pull_request" in issue:
            raise GitHubError("Pull Request는 이슈 관리 대상이 아닙니다.")
        return issue

    def update_issue(self, number: int, *, title: str | None = None, body: str | None = None,
                     state: str | None = None) -> dict:
        self.assert_issue(number)
        payload = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
            if state == "closed":
                payload["state_reason"] = "completed"
        issue = self.request("PATCH", f"/issues/{number}", json=payload)
        if "pull_request" in issue:
            raise GitHubError("Pull Request는 이슈 관리 대상이 아닙니다.")
        return self.normalize(issue, self.repo)

    def list_assignees(self) -> list[dict]:
        people = []
        for page in range(1, 11):
            batch = self.request(
                "GET", "/assignees", params={"per_page": 100, "page": page}
            )
            people.extend(
                {"login": item.get("login", ""), "avatar_url": item.get("avatar_url", "")}
                for item in batch if item.get("login")
            )
            if len(batch) < 100:
                break
        return people

    def set_assignees(self, number: int, assignees: list[str]) -> dict:
        self.assert_issue(number)
        issue = self.request(
            "PATCH", f"/issues/{number}", json={"assignees": assignees}
        )
        return self.normalize(issue, self.repo)

    def list_comments(self, number: int) -> list[dict]:
        self.assert_issue(number)
        items = self.request(
            "GET", f"/issues/{number}/comments", params={"per_page": 100}
        )
        return [{
            "id": item["id"],
            "body": item.get("body") or "",
            "user": (item.get("user") or {}).get("login", ""),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "html_url": item.get("html_url"),
        } for item in items]

    def create_comment(self, number: int, body: str) -> dict:
        self.assert_issue(number)
        return self.request("POST", f"/issues/{number}/comments", json={"body": body})

    def update_comment(self, comment_id: int, body: str) -> dict:
        return self.request("PATCH", f"/issues/comments/{comment_id}", json={"body": body})

    def delete_comment(self, comment_id: int) -> None:
        self.request("DELETE", f"/issues/comments/{comment_id}")

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
            and not (
                any(item.startswith("status:") for item in labels)
                and label.startswith("status:")
            )
        ]
        updated = list(dict.fromkeys(remaining + labels))
        self.request("PUT", f"/issues/{number}/labels", json={"labels": updated})

    def set_priority(self, number: int, priority: str, existing: list[str]) -> None:
        if priority not in {"high", "medium", "low", "none"}:
            raise GitHubError("우선순위 값이 올바르지 않습니다.")
        selected = [] if priority == "none" else [f"priority:{priority}"]
        self.ensure_labels(selected)
        remaining = [
            label for label in existing
            if not label.startswith("priority:") and label not in selected
        ]
        self.request(
            "PUT", f"/issues/{number}/labels",
            json={"labels": list(dict.fromkeys(remaining + selected))},
        )

    def upsert_comment(self, number: int, body: str) -> None:
        previous = self.find_analysis_comment(number)
        if previous:
            self.request("PATCH", f"/issues/comments/{previous[0]}", json={"body": body})
        else:
            self.request("POST", f"/issues/{number}/comments", json={"body": body})
