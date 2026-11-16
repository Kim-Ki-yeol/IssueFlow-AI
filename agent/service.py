"""GitHub 이슈 조회부터 GPT 분석, 결과 등록, Discord 전송까지 처리."""
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

    def activity(self) -> list[dict]:
        # 저장 DB 없이 GitHub 이슈의 최근 업데이트 내역을 보여줌
        return [{
            "issue_number": item["number"],
            "title": "이슈 업데이트",
            "detail": item["title"],
            "kind": "updated",
            "created_at": item.get("updated_at") or item.get("created_at"),
        } for item in self.issue_list()[:8]]

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
        client = self._client()
        issue = client.get_issue(number)
        already_labeled = "ai:processed" in issue.get("labels", [])
        if issue.get("analysis") and already_labeled and not force:
            return {"skipped": True, "reason": "이미 분석된 이슈입니다.", "issue": issue}

        # 댓글만 있고 라벨 등록이 실패한 경우에는 기존 결과를 재사용
        analysis = issue.get("analysis") if issue.get("analysis") and not force else None
        if analysis is None:
            analysis = analyze_issue(issue, self.cfg)
            if analysis.get("check_recent_commits"):
                try:
                    commits = client.recent_commits()
                    if commits:
                        analysis = analyze_issue(issue, self.cfg, commits=commits)
                except Exception:
                    # 커밋 조회가 실패해도 이슈 본문 분석은 그대로 사용
                    pass

        labels = [CATEGORY_LABELS[analysis["category"]], f"priority:{analysis['priority']}", "ai:processed"]
        applied = False
        if self.cfg.github_writes:
            client.upsert_comment(number, self.comment(analysis))
            # 댓글이 저장된 다음 완료 라벨 부착. 실패하면 재실행 시 복구 가능
            client.apply_labels(number, labels, issue["labels"])
            applied = True

        notification_sent = False
        notification_error = None
        if applied and self.cfg.discord_webhook_url:
            try:
                notification_sent = send_discord(self.cfg.discord_webhook_url, issue, analysis)
            except RuntimeError as exc:
                notification_error = str(exc)

        latest = client.get_issue(number) if applied else {**issue, "analysis": analysis}
        return {
            "skipped": False, "analysis": analysis, "labels": labels, "applied": applied,
            "notification_sent": notification_sent, "notification_error": notification_error,
            "issue": latest,
        }
