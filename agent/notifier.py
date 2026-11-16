"""실제 Discord 채널에 이슈 분석 결과를 보내는 코드."""
from __future__ import annotations

import requests


def send_discord(url: str, issue: dict, analysis: dict) -> bool:
    if not url:
        return False
    title = str(issue.get("title", ""))[:140]
    text = (
        "**IssueFlow AI · 이슈 분석 완료**\n"
        f"**#{issue['number']} {title}**\n"
        f"분류: `{analysis['category']}` · 우선순위: `{analysis['priority']}`\n"
        f"요약: {str(analysis['summary'])[:300]}\n"
        f"{issue.get('html_url') or ''}"
    )
    try:
        response = requests.post(url, json={"content": text[:1900], "allowed_mentions": {"parse": []}}, timeout=12)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        raise RuntimeError(f"Discord 알림 전송 실패: {type(exc).__name__}") from exc
