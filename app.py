
"""IssueFlow AI Flask 서버 - GitHub App 로그인 지원."""

from __future__ import annotations

import hmac
import os
import re
import requests
import secrets
import threading
import time

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.exceptions import HTTPException

from agent.config import get_github_app_config, get_user_config
from agent.github_auth import (
    GitHubAuthError,
    create_login_url,
    exchange_code,
    get_current_user,
    get_installations,
    get_installation_repositories,
)
from agent.github_client import GitHubError
from agent.service import Service


ROOT = Path(__file__).resolve().parent
SESSION_LIFETIME = 8 * 60 * 60


@dataclass
class LoginSession:
    token: str
    user: dict
    expires_at: float
    repo: str = ""
    installation_id: int = 0
    lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )


# 로그인 정보를 서버 메모리에 저장
LOGIN_SESSIONS: dict[str, LoginSession] = {}
SESSION_LOCK = threading.Lock()


# 만료된 로그인 정보 정리
def clean_sessions():
    now = time.time()

    with SESSION_LOCK:
        for sid, data in list(LOGIN_SESSIONS.items()):
            if data.expires_at <= now:
                del LOGIN_SESSIONS[sid]


# 현재 로그인 정보 확인
def get_login_session() -> LoginSession | None:
    sid = session.get("sid")

    if not sid:
        return None

    with SESSION_LOCK:
        data = LOGIN_SESSIONS.get(sid)

    if data is None or data.expires_at <= time.time():
        return None

    return data


# 로그인하지 않은 사용자는 접근 차단
def require_login() -> LoginSession:
    data = get_login_session()

    if data is None:
        raise GitHubAuthError("GitHub 로그인이 필요합니다.")

    return data


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder=None,
    )

    github_cfg = get_github_app_config()

    # Flask 세션 암호화에 사용할 키 설정
    app.secret_key = (
        github_cfg.flask_secret_key
        or secrets.token_hex(32)
    )

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(
            os.getenv("COOKIE_SECURE", "false").lower() == "true"
        ),
        MAX_CONTENT_LENGTH=32 * 1024,
    )

    # 외부 사이트에서 보내는 데이터 변경 요청 차단
    @app.before_request
    def check_request():
        # 일반 페이지 이동과 GitHub 로그인 요청은 허용
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None

        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify({
                "error": "허용되지 않은 요청입니다."
            }), 403

        # 요청을 보낸 사이트 확인
        origin = request.headers.get("Origin")

        if origin:
            parsed = urlsplit(origin)

            if (
                parsed.scheme not in {"http", "https"}
                or parsed.netloc.lower() != request.host.lower()
            ):
                return jsonify({
                    "error": "허용되지 않은 요청 출처입니다."
                }), 403

        # 데이터 변경 요청은 JSON만 허용
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if not request.is_json:
                return jsonify({
                    "error": "JSON 요청만 허용합니다."
                }), 415

        return None

    # 브라우저에 보안 관련 헤더 추가
    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https://avatars.githubusercontent.com data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )

        return response

    # 서버에서 발생한 오류 처리
    @app.errorhandler(Exception)
    def handle_error(exc):
        if isinstance(exc, HTTPException):
            return jsonify({
                "error": exc.description
            }), exc.code

        if isinstance(exc, GitHubAuthError):
            return jsonify({
                "error": str(exc)
            }), 401

        if isinstance(exc, LookupError):
            return jsonify({
                "error": str(exc)
            }), 404

        if isinstance(exc, (ValueError, GitHubError, RuntimeError)):
            return jsonify({
                "error": str(exc)
            }), 400

        app.logger.exception("Unexpected error")

        return jsonify({
            "error": "서버 오류가 발생했습니다."
        }), 500

    # 선택한 GitHub 저장소와 사용자 설정 불러오기
    def current_service() -> Service:
        login = require_login()

        with login.lock:
            repo = login.repo
            installation_id = login.installation_id

        if not repo or not installation_id:
            raise ValueError(
                "GitHub 저장소를 먼저 선택해 주세요."
            )

        # 선택한 저장소에 접근 권한이 있는지 확인
        repos = get_installation_repositories(
            login.token,
            installation_id,
        )

        if not any(
            item["full_name"] == repo for item in repos
        ):
            raise GitHubAuthError(
                "선택한 저장소의 접근 권한이 없습니다."
            )

        api_key = request.headers.get(
            "X-IssueFlow-OpenAI-Key", ""
        )

        discord = request.headers.get(
            "X-IssueFlow-Discord-Webhook", ""
        )

        cfg = get_user_config({
            "github_repo": repo,
            "github_token": login.token,
            "openai_api_key": api_key,
            "discord_webhook_url": discord,
        })

        return Service(cfg)

    # 메인 대시보드
    @app.get("/")
    def home():
        return render_template("index.html")

    # CSS와 JavaScript 파일 제공
    @app.get("/styles.css")
    @app.get("/app.js")
    def assets():
        return send_from_directory(
            ROOT / "public",
            request.path.lstrip("/"),
        )

    # 서버 실행 상태 확인
    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    # GitHub 로그인 시작
    @app.get("/auth/github")
    def github_login():
        url, state = create_login_url()

        session["oauth_state"] = state

        return redirect(url)

    # GitHub 로그인 후 돌아오는 주소
    @app.get("/auth/github/callback")
    def github_callback():
        expected = session.pop("oauth_state", None)
        received = request.args.get("state", "")
        code = request.args.get("code", "")

        # 로그인 요청과 응답이 일치하는지 확인
        if (
            not expected
            or not received
            or not hmac.compare_digest(expected, received)
        ):
            raise GitHubAuthError(
                "로그인 검증에 실패했습니다. 다시 시도해 주세요."
            )

        # GitHub 인증 코드를 토큰으로 교환
        token = exchange_code(code)
        user = get_current_user(token)

        clean_sessions()
        sid = secrets.token_urlsafe(32)

        # 로그인 정보를 서버에 저장
        with SESSION_LOCK:
            LOGIN_SESSIONS[sid] = LoginSession(
                token=token,
                user=user,
                expires_at=time.time() + SESSION_LIFETIME,
            )

        session.clear()
        session["sid"] = sid

        return redirect(url_for("home"))

    # 현재 로그인한 사용자 정보
    @app.get("/api/auth/me")
    def auth_me():
        login = get_login_session()

        if login is None:
            return jsonify({
                "authenticated": False,
                "user": None,
            })

        return jsonify({
            "authenticated": True,
            "user": login.user,
            "selected_repo": login.repo,
        })

    # GitHub App 설치 목록 조회
    @app.get("/api/github/installations")
    def installations():
        login = require_login()

        return jsonify({
            "items": get_installations(login.token)
        })

    # GitHub App에 연결된 저장소 조회
    @app.get("/api/github/installations/<int:installation_id>/repositories")
    def repositories(installation_id: int):
        login = require_login()

        items = get_installation_repositories(
            login.token,
            installation_id,
        )

        return jsonify({"items": items})

    # 사용자가 작업할 저장소 선택
    @app.post("/api/github/select-repository")
    def select_repository():
        login = require_login()
        body = request.get_json(silent=True) or {}

        repo = str(body.get("repo") or "").strip()
        installation_id = body.get("installation_id")

        if (
            not isinstance(installation_id, int)
            or isinstance(installation_id, bool)
            or installation_id <= 0
        ):
            raise ValueError(
                "올바른 GitHub App 설치를 선택해 주세요."
            )

        items = get_installation_repositories(
            login.token,
            installation_id,
        )

        if not any(
            item["full_name"] == repo for item in items
        ):
            raise ValueError(
                "선택한 저장소에 접근할 수 없습니다."
            )

        with login.lock:
            login.repo = repo
            login.installation_id = installation_id

        return jsonify({
            "ok": True,
            "repo": repo,
        })

    # GitHub에 새 저장소 생성
    @app.post("/api/github/repositories")
    def create_repository():
        login = require_login()
        body = request.get_json(silent=True) or {}

        name = str(body.get("name") or "").strip()
        description = str(body.get("description") or "").strip()
        private = body.get("private", True)

        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
            raise ValueError(
                "저장소 이름을 확인해 주세요."
            )

        if len(description) > 350:
            raise ValueError(
                "저장소 설명은 350자 이하로 입력해 주세요."
            )

        if not isinstance(private, bool):
            raise ValueError(
                "공개 범위가 올바르지 않습니다."
            )

        try:
            response = requests.post(
                "https://api.github.com/user/repos",
                headers={
                    "Authorization": f"Bearer {login.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "IssueFlow-AI",
                },
                json={
                    "name": name,
                    "description": description,
                    "private": private,
                    "auto_init": True,
                    "has_issues": True,
                },
                timeout=20,
            )

        except requests.RequestException as exc:
            app.logger.warning(
                "GitHub repository creation request failed: %s",
                type(exc).__name__,
            )
            raise RuntimeError(
                "GitHub 서버에 연결하지 못했습니다."
            ) from exc

        if response.status_code == 401:
            raise GitHubAuthError(
                "GitHub 인증이 만료되었거나 유효하지 않습니다."
            )

        if response.status_code == 403:
            raise ValueError(
                "저장소 생성 권한이 없습니다. GitHub App 권한을 확인해 주세요."
            )

        if response.status_code == 422:
            raise ValueError(
                "이미 존재하거나 사용할 수 없는 저장소 이름입니다."
            )

        if response.status_code != 201:
            app.logger.warning(
                "GitHub repository creation failed: %s",
                response.status_code,
            )
            raise RuntimeError(
                "GitHub 저장소 생성에 실패했습니다."
            )

        repo = response.json()

        return jsonify({
            "ok": True,
            "repo": repo["full_name"],
            "html_url": repo["html_url"],
            "requires_installation": True,
        }), 201

    # GitHub 로그아웃
    @app.post("/api/auth/logout")
    def logout():
        sid = session.get("sid")

        if sid:
            with SESSION_LOCK:
                LOGIN_SESSIONS.pop(sid, None)

        session.clear()
        return jsonify({"ok": True})

    # 대시보드 설정 조회
    @app.get("/api/config")
    def config():
        service = current_service()
        cfg = service.cfg

        return jsonify({
            "repo": cfg.github_repo,
            "ai_enabled": cfg.ai_ready,
            "github_enabled": cfg.github_ready,
            "discord_enabled": bool(cfg.discord_webhook_url),
            "writes_enabled": cfg.github_writes,
            "model": cfg.openai_model,
        })

    # GitHub 이슈 목록 조회
    @app.get("/api/issues")
    def issues():
        service = current_service()

        return jsonify({
            "items": service.issue_list()
        })

    # GitHub 이슈 상세 조회
    @app.get("/api/issues/<int:number>")
    def issue_detail(number: int):
        service = current_service()

        return jsonify(
            service.issue_detail(number)
        )

    # GPT로 이슈 분석
    @app.post("/api/issues/<int:number>/analyze")
    def analyze(number: int):
        body = request.get_json(silent=True) or {}
        force = body.get("force", False)

        if not isinstance(force, bool):
            raise ValueError(
                "force는 true 또는 false여야 합니다."
            )

        service = current_service()

        return jsonify(
            service.run(number, force=force)
        )

    # 최근 이슈 활동 조회
    @app.get("/api/activity")
    def activity():
        service = current_service()

        return jsonify({
            "items": service.activity()
        })

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
        debug=False,
    )
