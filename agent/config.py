
"""IssueFlow AI 환경변수 및 사용자별 연결 설정."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {
        "1", "true", "yes", "on"
    }


@dataclass(frozen=True)
class Config:
    github_repo: str
    github_token: str
    openai_api_key: str
    openai_model: str
    discord_webhook_url: str
    github_writes: bool
    dashboard_password: str

    @property
    def ai_ready(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def github_ready(self) -> bool:
        return bool(self.github_repo and self.github_token)


@dataclass(frozen=True)
class GitHubAppConfig:
    app_id: str
    client_id: str
    client_secret: str
    app_slug: str
    flask_secret_key: str

    @property
    def ready(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and self.flask_secret_key
        )


def get_github_app_config() -> GitHubAppConfig:
    """GitHub App 로그인에 필요한 서버 설정."""

    return GitHubAppConfig(
        app_id=os.getenv("GITHUB_APP_ID", "").strip(),
        client_id=os.getenv("GITHUB_APP_CLIENT_ID", "").strip(),
        client_secret=os.getenv("GITHUB_APP_CLIENT_SECRET", "").strip(),
        app_slug=os.getenv("GITHUB_APP_SLUG", "").strip(),
        flask_secret_key=os.getenv("FLASK_SECRET_KEY", "").strip(),
    )


def get_config() -> Config:
    """기존 GitHub Actions 및 환경변수 기반 설정 유지."""

    return Config(
        github_repo=(
            os.getenv("GITHUB_REPO", "").strip()
            or os.getenv("GITHUB_REPOSITORY", "").strip()
        ),
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv(
            "OPENAI_MODEL", "gpt-4.1-mini"
        ).strip(),
        discord_webhook_url=os.getenv(
            "DISCORD_WEBHOOK_URL", ""
        ).strip(),
        github_writes=truthy(
            os.getenv("ENABLE_GITHUB_WRITES", "false")
        ),
        dashboard_password=os.getenv(
            "DASHBOARD_PASSWORD", ""
        ).strip(),
    )


def get_user_config(data: dict) -> Config:
    """로그인한 사용자의 저장소 및 OpenAI 설정 생성."""

    if not isinstance(data, dict):
        raise ValueError("설정 데이터가 올바르지 않습니다.")

    repo = str(data.get("github_repo") or "").strip()
    token = str(data.get("github_token") or "").strip()
    api_key = str(data.get("openai_api_key") or "").strip()
    discord = str(data.get("discord_webhook_url") or "").strip()

    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo
    ):
        raise ValueError(
            "GitHub 저장소 형식이 올바르지 않습니다."
        )

    if not token:
        raise ValueError("GitHub 인증이 필요합니다.")

    if not api_key:
        raise ValueError("OpenAI API Key를 입력해 주세요.")

    if discord and not re.fullmatch(
        r"https://(?:discord\.com|discordapp\.com)"
        r"/api/webhooks/[0-9]+/[A-Za-z0-9_-]+",
        discord,
    ):
        raise ValueError(
            "Discord Webhook URL 형식이 올바르지 않습니다."
        )

    return Config(
        github_repo=repo,
        github_token=token,
        openai_api_key=api_key,
        openai_model="gpt-5.4-mini",
        discord_webhook_url=discord,
        github_writes=True,
        dashboard_password="",
    )
