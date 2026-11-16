"""GitHub, OpenAI, Discord 연결에 필요한 환경변수를 읽는 파일."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# 로컬에서는 .env를 쓰고, Vercel/Actions에서는 각 서비스의 Secret을 사용
load_dotenv(ROOT / ".env")


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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


def get_config() -> Config:
    return Config(
        github_repo=(os.getenv("GITHUB_REPO", "").strip() or os.getenv("GITHUB_REPOSITORY", "").strip()),
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip(),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        github_writes=truthy(os.getenv("ENABLE_GITHUB_WRITES", "false")),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", "").strip(),
    )
