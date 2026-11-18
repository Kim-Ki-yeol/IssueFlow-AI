"""GitHub 이슈 조회, 수동 GPT 분석, 사용자 승인 후 반영."""
from __future__ import annotations

import json

from .analyzer import analyze_issue
from .config import Config
from .github_client import COMMENT_MARKER, GitHubClient
from .notifier import send_discord

CATEGORY_LABELS = {"bug": "bug", "feature": "enhancement", "documentation": "documentation", "question": "question"}


class Service:
    def __init__(self, config: Config):
        self.cfg = config
        self.github = GitHubClient(config.github_repo, config.github_token) if config.github_ready else None

    def _client(self) -> GitHubClient:
        if self.github is None:
            raise RuntimeError("GITHUB_REPO와 GITHUB_TOKEN을 먼저 설정해 주세요.")
        return self.github

    def issue_list(self) -> list[dict]:
        return self._client().list_issues()

    def issue_detail(self, number: int) -> dict:
        return self._client().get_issue(number)

    def create_issue(self, title: str, body: str) -> dict:
        return self._client().create_issue(title, body)

    def update_issue(self, number: int, **changes) -> dict:
        return self._client().update_issue(number, **changes)

    def list_assignees(self) -> list[dict]:
        return self._client().list_assignees()

    def set_assignees(self, number: int, assignees: list[str]) -> dict:
        return self._client().set_assignees(number, assignees)

    def list_comments(self, number: int) -> list[dict]:
        return self._client().list_comments(number)

    def create_comment(self, number: int, body: str) -> dict:
        return self._client().create_comment(number, body)

    def update_comment(self, comment_id: int, body: str) -> dict:
        return self._client().update_comment(comment_id, body)

    def delete_comment(self, comment_id: int) -> None:
        self._client().delete_comment(comment_id)

    def set_priority(self, number: int, priority: str) -> dict:
        if not isinstance(priority, str) or priority not in {"high", "medium", "low", "none"}:
            raise ValueError("우선순위 값이 올바르지 않습니다.")
        issue = self._client().get_issue(number)
        self._client().set_priority(number, priority, issue["labels"])
        return self._client().get_issue(number)

    def set_board_status(self, number: int, status: str) -> dict:
        labels = {
            "todo": "status:todo",
            "in_progress": "status:in-progress",
            "done": "status:done",
        }
        if not isinstance(status, str) or status not in labels:
            raise ValueError("칸반 상태 값이 올바르지 않습니다.")
        issue = self._client().get_issue(number)
        self._client().apply_labels(number, [labels[status]], issue["labels"])
        return self._client().get_issue(number)

    def activity(self) -> list[dict]:
        # 저장 DB 없이 GitHub 이슈의 최근 업데이트 내역을 보여줌
        return [{
            "issue_number": item["number"],
            "title": "이슈 업데이트",
            "detail": item["title"],
            "kind": "updated",
            "created_at": item.get("updated_at") or item.get("created_at"),
        } for item in self._client().list_issues(limit=8)]

    @staticmethod
    def comment(analysis: dict) -> str:
        # JSON은 나중에 대시보드에서 결과를 다시 표시할 때 사용
        data = json.dumps(analysis, ensure_ascii=False, separators=(",", ":")).replace("-->", "--\\u003e")
        tips = "\n".join(f"{index}. {item}" for index, item in enumerate(analysis["suggestions"], start=1))
        return (
            f"{COMMENT_MARKER}{data} -->\n"
            "## IssueFlow AI 분석 결과\n\n"
            f"- **분류:** `{analysis['category']}`\n"
            f"- **우선순위:** `{analysis['priority']}`\n"
            f"- **관련 영역:** {analysis['area']}\n\n"
            f"**요약:** {analysis['summary']}\n\n"
            f"**추정 원인:** {analysis['probable_cause']}\n\n"
            f"**권장 점검:**\n{tips}\n\n"
            "> AI 분석은 추정이므로 실제 코드와 로그를 확인해야 합니다."
        )

    def run(self, number: int, force: bool = False) -> dict:
        """Backward-compatible manual analysis entry point; never writes externally."""
        client = self._client()
        issue = client.get_issue(number)
        analysis = analyze_issue(issue, self.cfg)
        if analysis.get("check_recent_commits"):
            try:
                commits = client.recent_commits()
                if commits:
                    analysis = analyze_issue(issue, self.cfg, commits=commits)
            except Exception:
                # Commit context is optional; keep the issue-only result.
                pass
        issue["analysis"] = analysis
        return {"skipped": False, "analysis": analysis, "applied": False, "issue": issue}

    def apply(
        self, number: int, analysis: dict, *, apply_labels: bool, post_comment: bool,
        send_notification: bool = True,
    ) -> dict:
        """Apply only explicitly selected changes. Discord follows successful writes."""
        if not self.cfg.github_writes:
            raise RuntimeError("GitHub 쓰기 권한이 없습니다.")
        if not (apply_labels or post_comment):
            raise ValueError("적용할 항목을 하나 이상 선택해 주세요.")

        client = self._client()
        issue = client.get_issue(number)
        errors: list[str] = []
        labels_applied = False
        comment_applied = False
        labels = list(dict.fromkeys(
            analysis.get("recommended_labels")
            or [CATEGORY_LABELS[analysis["category"]], f"priority:{analysis['priority']}"]
        ))
        if apply_labels:
            try:
                client.apply_labels(number, labels, issue["labels"])
                labels_applied = True
            except (GitHubError, RuntimeError) as exc:
                errors.append(f"라벨: {str(exc)[:240]}")
        if post_comment:
            try:
                client.upsert_comment(number, self.comment(analysis))
                comment_applied = True
            except (GitHubError, RuntimeError) as exc:
                errors.append(f"댓글: {str(exc)[:240]}")

        succeeded = labels_applied or comment_applied
        all_selected_succeeded = (
            (not apply_labels or labels_applied)
            and (not post_comment or comment_applied)
        )
        status = "applied" if all_selected_succeeded else ("partial" if succeeded else "failed")
        notification_sent = False
        notification_error = None
        if succeeded and send_notification and self.cfg.discord_webhook_url:
            try:
                notification_sent = send_discord(self.cfg.discord_webhook_url, issue, analysis)
            except RuntimeError as exc:
                notification_error = str(exc)

        latest = client.get_issue(number) if succeeded else issue
        return {
            "status": status, "applied": succeeded, "labels_applied": labels_applied,
            "comment_applied": comment_applied, "labels": labels,
            "errors": errors, "notification_sent": notification_sent,
            "notification_error": notification_error, "issue": latest,
        }
