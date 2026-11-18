
"""GitHub App 사용자 로그인 및 API 인증."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import requests

from agent.config import get_github_app_config


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"


class GitHubAuthError(RuntimeError):
    """GitHub 인증 과정에서 발생한 오류."""


def create_login_url(redirect_uri: str | None = None) -> tuple[str, str]:
    """GitHub 로그인 주소와 CSRF 방지용 state 생성."""

    cfg = get_github_app_config()

    if not cfg.ready:
        raise GitHubAuthError(
            "GitHub App 환경변수가 설정되지 않았습니다."
        )

    state = secrets.token_urlsafe(32)

    params = {
        "client_id": cfg.client_id,
        "state": state,
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    url = f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"

    return url, state


def exchange_code(code: str, redirect_uri: str | None = None) -> str:
    """GitHub에서 받은 인증 코드를 사용자 토큰으로 교환."""

    cfg = get_github_app_config()

    if not cfg.ready:
        raise GitHubAuthError(
            "GitHub App 환경변수가 설정되지 않았습니다."
        )

    if not code:
        raise GitHubAuthError("GitHub 인증 코드가 없습니다.")

    try:
        token_data = {
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "code": code,
        }
        if redirect_uri:
            token_data["redirect_uri"] = redirect_uri
        response = requests.post(
            GITHUB_TOKEN_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "IssueFlow-AI",
            },
            data=token_data,
            timeout=15,
        )
        response.raise_for_status()

        data = response.json()

    except requests.RequestException as exc:
        raise GitHubAuthError(
            "GitHub 로그인 서버와 통신하지 못했습니다."
        ) from exc

    if data.get("error"):
        raise GitHubAuthError(
            "GitHub 인증에 실패했습니다. 다시 로그인해 주세요."
        )

    token = data.get("access_token")

    if not token:
        raise GitHubAuthError(
            "GitHub 액세스 토큰을 발급받지 못했습니다."
        )

    return token


def github_api_get(
    path: str,
    token: str,
    params: dict | None = None,
) -> dict | list:
    """로그인한 사용자 토큰으로 GitHub API 호출."""

    if not token:
        raise GitHubAuthError("GitHub 로그인이 필요합니다.")

    try:
        response = requests.get(
            f"{GITHUB_API_URL}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "IssueFlow-AI",
            },
            params=params,
            timeout=15,
        )
        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        raise GitHubAuthError(
            "GitHub 정보를 불러오지 못했습니다. "
            "로그인 상태와 접근 권한을 확인해 주세요."
        ) from exc


def get_current_user(token: str) -> dict:
    """로그인한 GitHub 사용자 정보 조회."""

    data = github_api_get("/user", token)

    return {
        "id": data.get("id"),
        "login": data.get("login"),
        "avatar_url": data.get("avatar_url"),
        "html_url": data.get("html_url"),
    }


def get_installations(token: str) -> list[dict]:
    """현재 사용자가 접근 가능한 GitHub App 설치 목록."""

    data = github_api_get(
        "/user/installations",
        token,
        {"per_page": 100},
    )

    return [
        {
            "id": item.get("id"),
            "account": (
                item.get("account") or {}
            ).get("login"),
            "repository_selection": item.get(
                "repository_selection"
            ),
        }
        for item in data.get("installations", [])
    ]


def get_installation_repositories(
    token: str,
    installation_id: int,
) -> list[dict]:
    """특정 GitHub App 설치에 연결된 저장소 목록."""

    data = github_api_get(
        f"/user/installations/{installation_id}/repositories",
        token,
        {"per_page": 100},
    )

    return [
        {
            "id": repo.get("id"),
            "full_name": repo.get("full_name"),
            "private": repo.get("private", False),
            "html_url": repo.get("html_url"),
            "description": repo.get("description"),
        }
        for repo in data.get("repositories", [])
    ]
