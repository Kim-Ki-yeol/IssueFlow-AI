
"""IssueFlow AI Flask 서버 - GitHub App 로그인 지원."""

from __future__ import annotations

import hmac
import base64
import os
import re
import requests
import secrets
import threading
import time

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

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
from agent.notifier import send_discord
from agent.storage import (
    delete_expired_login_sessions, delete_login_session,
    get_settings, history as get_history, init_db, load_login_session,
    persistent_sessions_enabled, save_analysis, save_login_session,
    save_settings, setting_status, statistics, update_analysis,
)


ROOT = Path(__file__).resolve().parent
SESSION_LIFETIME = 8 * 60 * 60
OAUTH_CALLBACK_PATH = "/auth/github/callback"


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
    if persistent_sessions_enabled():
        delete_expired_login_sessions()
        return

    with SESSION_LOCK:
        for sid, data in list(LOGIN_SESSIONS.items()):
            if data.expires_at <= now:
                del LOGIN_SESSIONS[sid]


# 현재 로그인 정보 확인
def get_login_session() -> LoginSession | None:
    sid = session.get("sid")

    if not sid:
        return None

    if persistent_sessions_enabled():
        data = load_login_session(sid)
        if data is None:
            session.pop("sid", None)
            return None
        return LoginSession(**data)

    with SESSION_LOCK:
        data = LOGIN_SESSIONS.get(sid)

    if data is None:
        session.pop("sid", None)
        return None

    if data.expires_at <= time.time():
        with SESSION_LOCK:
            LOGIN_SESSIONS.pop(sid, None)

        session.pop("sid", None)
        return None

    return data


# 로그인하지 않은 사용자는 접근 차단
def require_login() -> LoginSession:
    data = get_login_session()

    if data is None:
        raise GitHubAuthError("GitHub 로그인이 필요합니다.")

    return data


def validate_oauth_redirect_uri(uri: str, *, require_https: bool = False) -> str:
    parsed = urlsplit(uri)
    schemes = {"https"} if require_https else {"http", "https"}
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path != OAUTH_CALLBACK_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("GITHUB_OAUTH_REDIRECT_URI 형식이 올바르지 않습니다.")
    return uri


def oauth_redirect_uri() -> str:
    """Resolve a stable, environment-appropriate OAuth callback URL."""
    is_vercel = os.getenv("VERCEL", "").strip() == "1"
    configured = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return validate_oauth_redirect_uri(configured, require_https=is_vercel)
    if is_vercel:
        raise RuntimeError(
            "Vercel 환경에는 GITHUB_OAUTH_REDIRECT_URI를 설정해야 합니다."
        )
    return validate_oauth_redirect_uri(
        url_for("github_callback", _external=True),
    )


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder=None,
    )

    github_cfg = get_github_app_config()
    is_vercel = os.getenv("VERCEL", "").strip() == "1"
    if is_vercel and not github_cfg.flask_secret_key:
        raise RuntimeError("Vercel 환경에는 고정된 FLASK_SECRET_KEY 설정이 필요합니다.")
    if is_vercel and not os.getenv("GITHUB_OAUTH_REDIRECT_URI", "").strip():
        raise RuntimeError("Vercel 환경에는 GITHUB_OAUTH_REDIRECT_URI 설정이 필요합니다.")
    configured_redirect = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    if configured_redirect:
        validate_oauth_redirect_uri(configured_redirect, require_https=is_vercel)

    # Flask 세션 암호화에 사용할 키 설정
    app.secret_key = (
        github_cfg.flask_secret_key
        or secrets.token_hex(32)
    )

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(
            is_vercel or os.getenv("COOKIE_SECURE", "false").lower() == "true"
        ),
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
        MAX_CONTENT_LENGTH=32 * 1024,
    )
    init_db()

    # 외부 사이트에서 보내는 데이터 변경 요청 차단
    @app.before_request
    def check_request():
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None

        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify({
                "error": "허용되지 않은 요청입니다."
            }), 403

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

        app.logger.error("Unexpected server error: %s", type(exc).__name__)

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

        saved = get_settings(login.user.get("id"))

        cfg = get_user_config({
            "github_repo": repo,
            "github_token": login.token,
            "openai_api_key": saved["openai_api_key"],
            "discord_webhook_url": saved["discord_webhook_url"],
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
        redirect_uri = oauth_redirect_uri()
        if urlsplit(redirect_uri).netloc.lower() != request.host.lower():
            raise GitHubAuthError(
                "GitHub 로그인은 Callback URL과 같은 도메인에서 시작해 주세요."
            )
        url, state = create_login_url(redirect_uri)

        session["oauth_state"] = state
        session["oauth_redirect_uri"] = redirect_uri

        return redirect(url)

    # GitHub 로그인 후 돌아오는 주소
    @app.get("/auth/github/callback")
    def github_callback():
        expected = session.pop("oauth_state", None)
        redirect_uri = session.pop("oauth_redirect_uri", None)
        received = request.args.get("state", "")
        code = request.args.get("code", "")

        if (
            not expected
            or not received
            or not hmac.compare_digest(expected, received)
        ):
            raise GitHubAuthError(
                "로그인 검증에 실패했습니다. 다시 시도해 주세요."
            )

        # GitHub 인증 코드를 토큰으로 교환
        token = exchange_code(code, redirect_uri=redirect_uri or oauth_redirect_uri())
        user = get_current_user(token)
        saved = get_settings(user.get("id"))
        restored_repo, restored_installation = saved["selected_repo"], 0
        if restored_repo:
            for installation in get_installations(token):
                repos = get_installation_repositories(token, installation["id"])
                if any(item["full_name"] == restored_repo for item in repos):
                    restored_installation = installation["id"]
                    break
            if not restored_installation:
                restored_repo = ""

        clean_sessions()

        # 이전 로그인 세션이 남아 있다면 정리
        old_sid = session.get("sid")

        if old_sid:
            if persistent_sessions_enabled():
                delete_login_session(old_sid)
            else:
                with SESSION_LOCK:
                    LOGIN_SESSIONS.pop(old_sid, None)

        sid = secrets.token_urlsafe(32)

        # Store OAuth tokens only on the server; Vercel uses encrypted persistent sessions.
        login_data = LoginSession(
            token=token,
            user=user,
            expires_at=time.time() + SESSION_LIFETIME,
            repo=restored_repo,
            installation_id=restored_installation,
        )
        if persistent_sessions_enabled():
            save_login_session(
                sid, user, token, login_data.expires_at,
                restored_repo, restored_installation,
            )
        else:
            with SESSION_LOCK:
                LOGIN_SESSIONS[sid] = login_data

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
                "selected_repo": "",
            })

        with login.lock:
            selected_repo = login.repo

        return jsonify({
            "authenticated": True,
            "user": login.user,
            "selected_repo": selected_repo,
        })

    # 저장된 설정은 비밀값이 아닌 등록 여부만 반환
    @app.get("/api/settings")
    def settings_status():
        login = require_login()
        return jsonify(setting_status(login.user.get("id")))

    @app.put("/api/settings")
    def save_user_settings():
        login = require_login()
        body = request.get_json(silent=True) or {}
        api_key = str(body.get("openai_api_key") or "").strip()
        discord = str(body.get("discord_webhook_url") or "").strip()
        repo = str(login.repo or "").strip()
        if len(api_key) > 500 or len(discord) > 500:
            raise ValueError("입력한 설정 값이 너무 깁니다.")
        if api_key and not api_key.startswith("sk-"):
            raise ValueError("OpenAI API Key 형식을 확인해 주세요.")
        if discord:
            get_user_config({
                "github_repo": repo or "owner/repo",
                "github_token": login.token,
                "openai_api_key": "",
                "discord_webhook_url": discord,
            })
        save_settings(login.user.get("id"), api_key, discord, repo)
        return jsonify({"ok": True, **setting_status(login.user.get("id"))})

    @app.post("/api/settings/test-openai")
    def test_openai():
        login = require_login()
        saved = get_settings(login.user.get("id"))
        if not saved["openai_api_key"]:
            raise ValueError("먼저 OpenAI API Key를 저장해 주세요.")
        try:
            response = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {saved['openai_api_key']}"},
                timeout=15,
            )
            if not response.ok:
                raise ValueError("OpenAI 연결에 실패했습니다. 키와 계정 상태를 확인해 주세요.")
        except requests.RequestException as exc:
            app.logger.warning("OpenAI connection test failed: %s", type(exc).__name__)
            raise RuntimeError("OpenAI 서버에 연결하지 못했습니다.") from exc
        return jsonify({"ok": True, "message": "OpenAI 연결이 확인되었습니다."})

    @app.post("/api/settings/test-discord")
    def test_discord():
        login = require_login()
        saved = get_settings(login.user.get("id"))
        if not saved["discord_webhook_url"]:
            raise ValueError("먼저 Discord Webhook URL을 저장해 주세요.")
        try:
            response = requests.post(
                saved["discord_webhook_url"],
                json={"content": "IssueFlow AI 연결 테스트입니다.", "allowed_mentions": {"parse": []}},
                timeout=12,
            )
            if not response.ok:
                raise ValueError("Discord 테스트 메시지 전송에 실패했습니다. Webhook 설정을 확인해 주세요.")
        except requests.RequestException as exc:
            app.logger.warning("Discord connection test failed: %s", type(exc).__name__)
            raise RuntimeError("Discord 서버에 연결하지 못했습니다.") from exc
        return jsonify({"ok": True, "message": "Discord 테스트 메시지를 전송했습니다."})

    # GitHub 계정 전환 안내 정보
    @app.get("/api/auth/account-switch")
    def account_switch():
        return jsonify({
            "ok": True,
            "message": (
                "다른 GitHub 계정을 사용하려면 "
                "GitHub에서 계정을 전환한 뒤 "
                "다시 로그인해 주세요."
            ),
            "github_url": "https://github.com/",
            "login_url": url_for("github_login"),
        })

    # GitHub App 설치 목록 조회
    @app.get("/api/github/installations")
    def installations():
        login = require_login()

        return jsonify({
            "items": get_installations(login.token)
        })

    # GitHub App에 연결된 저장소 조회
    @app.get(
        "/api/github/installations/<int:installation_id>/repositories"
    )
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
        if persistent_sessions_enabled():
            save_login_session(
                session.get("sid"), login.user, login.token, login.expires_at,
                repo, installation_id,
            )
        saved = get_settings(login.user.get("id"))
        save_settings(
            login.user.get("id"), saved["openai_api_key"],
            saved["discord_webhook_url"], repo,
        )

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
        initialize_readme = body.get("initialize_readme", True)
        initialize_labels = body.get("initialize_labels", False)
        initialize_templates = body.get("initialize_templates", False)

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
        if any(not isinstance(value, bool) for value in (
            initialize_readme, initialize_labels, initialize_templates
        )):
            raise ValueError("저장소 초기 설정 옵션이 올바르지 않습니다.")
        if initialize_templates and not initialize_readme:
            raise ValueError("템플릿 파일을 추가하려면 README 초기화(첫 커밋)가 필요합니다.")

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
                    "auto_init": initialize_readme,
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
        initialization = {"errors": [], "readme": initialize_readme, "labels": [], "templates": []}
        headers = {
            "Authorization": f"Bearer {login.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IssueFlow-AI",
        }

        def setup_request(method: str, path: str, **kwargs):
            try:
                return requests.request(
                    method, f"https://api.github.com/repos/{repo['full_name']}{path}",
                    headers=headers, timeout=20, **kwargs,
                )
            except requests.RequestException as exc:
                app.logger.warning("GitHub initial setup failed: %s", type(exc).__name__)
                return None

        if initialize_labels:
            labels = [
                ("bug", "ef4444"), ("enhancement", "8b5cf6"),
                ("documentation", "0ea5e9"), ("priority:high", "dc2626"),
                ("priority:medium", "f59e0b"), ("priority:low", "22c55e"),
                ("status:todo", "94a3b8"), ("status:in-progress", "3b82f6"),
                ("status:done", "22c55e"),
            ]
            for label_name, color in labels:
                exists = setup_request("GET", f"/labels/{quote(label_name, safe='')}")
                if exists is not None and exists.status_code == 200:
                    continue
                if exists is None or exists.status_code != 404:
                    initialization["errors"].append(f"라벨 확인 실패: {label_name}")
                    continue
                created = setup_request("POST", "/labels", json={"name": label_name, "color": color})
                if created is not None and created.status_code == 201:
                    initialization["labels"].append(label_name)
                else:
                    initialization["errors"].append(f"라벨 생성 실패: {label_name}")

        if initialize_templates:
            templates = {
                ".github/ISSUE_TEMPLATE/bug_report.md": (
                    "---\nname: Bug report\nabout: Report a reproducible problem\n"
                    "title: '[Bug] '\nlabels: bug\n---\n\n"
                    "## 문제 설명\n\n## 재현 단계\n\n## 기대한 결과\n\n## 실제 결과\n"
                ),
                ".github/ISSUE_TEMPLATE/feature_request.md": (
                    "---\nname: Feature request\nabout: Suggest an improvement\n"
                    "title: '[Feature] '\nlabels: enhancement\n---\n\n"
                    "## 해결하려는 문제\n\n## 제안하는 해결 방법\n\n## 추가 정보\n"
                ),
            }
            for file_path, content in templates.items():
                encoded_path = quote(file_path, safe="/")
                existing = setup_request("GET", f"/contents/{encoded_path}")
                if existing is not None and existing.status_code == 200:
                    continue
                if existing is None or existing.status_code != 404:
                    initialization["errors"].append(f"템플릿 확인 실패: {file_path}")
                    continue
                created = setup_request("PUT", f"/contents/{encoded_path}", json={
                    "message": "Add default IssueFlow issue template",
                    "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                })
                if created is not None and created.status_code == 201:
                    initialization["templates"].append(file_path)
                else:
                    initialization["errors"].append(f"템플릿 생성 실패: {file_path}")

        return jsonify({
            "ok": True,
            "repo": repo["full_name"],
            "html_url": repo["html_url"],
            "requires_installation": True,
            "initialization": initialization,
        }), 201

    # GitHub 로그아웃
    @app.post("/api/auth/logout")
    def logout():
        sid = session.get("sid")

        # 서버에 저장된 로그인 정보 삭제
        if sid:
            if persistent_sessions_enabled():
                delete_login_session(sid)
            else:
                with SESSION_LOCK:
                    LOGIN_SESSIONS.pop(sid, None)

        # 브라우저의 Flask 세션 초기화
        session.clear()

        response = jsonify({
            "ok": True,
            "authenticated": False,
            "message": "IssueFlow AI에서 로그아웃되었습니다.",
        })

        # Flask 세션 쿠키 만료
        response.delete_cookie(
            app.config.get("SESSION_COOKIE_NAME", "session"),
            path=app.config.get("SESSION_COOKIE_PATH") or "/",
            domain=app.config.get("SESSION_COOKIE_DOMAIN"),
            secure=app.config.get("SESSION_COOKIE_SECURE", False),
            httponly=app.config.get("SESSION_COOKIE_HTTPONLY", True),
            samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
        )

        return response

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
        login = require_login()
        recent = get_history(login.user.get("id"), service.cfg.github_repo)
        by_issue = {}
        for entry in recent:
            by_issue.setdefault(entry["issue_number"], entry)
        items = service.issue_list()
        for item in items:
            entry = by_issue.get(item["number"])
            if entry and entry["analysis_status"] == "complete":
                item["analysis"] = entry["analysis"]
                item["analysis_id"] = entry["id"]
                item["analysis_status"] = entry["github_status"]
                item["discord_status"] = entry["discord_status"]

        return jsonify({"items": items})

    # GitHub 이슈 상세 조회
    @app.get("/api/issues/<int:number>")
    def issue_detail(number: int):
        service = current_service()
        issue = service.issue_detail(number)
        login = require_login()
        entries = get_history(login.user.get("id"), service.cfg.github_repo, number)
        latest = next((item for item in entries if item["analysis"]), None)
        if latest:
            issue["analysis"] = latest["analysis"]
            issue["analysis_id"] = latest["id"]
            issue["analysis_status"] = latest["github_status"]
            issue["discord_status"] = latest["discord_status"]
            previous = next(
                (item for item in entries if item["analysis"] and item["id"] != latest["id"]),
                None,
            )
            if previous:
                fields = ("category", "priority", "probable_cause", "solution", "recommended_labels")
                issue["analysis_comparison"] = {
                    key: {"previous": previous["analysis"].get(key), "current": latest["analysis"].get(key)}
                    for key in fields
                    if previous["analysis"].get(key) != latest["analysis"].get(key)
                }
        return jsonify(issue)

    @app.post("/api/issues")
    def create_issue():
        service = current_service()
        body = request.get_json(silent=True) or {}
        title = str(body.get("title") or "").strip()
        description = str(body.get("body") or "")
        if not title or len(title) > 256:
            raise ValueError("이슈 제목은 1~256자로 입력해 주세요.")
        if len(description) > 65536:
            raise ValueError("이슈 본문이 너무 깁니다.")
        return jsonify({"item": service.create_issue(title, description)}), 201

    @app.patch("/api/issues/<int:number>")
    def update_issue(number: int):
        service = current_service()
        body = request.get_json(silent=True) or {}
        changes = {}
        if "title" in body:
            title = str(body.get("title") or "").strip()
            if not title or len(title) > 256:
                raise ValueError("이슈 제목은 1~256자로 입력해 주세요.")
            changes["title"] = title
        if "body" in body:
            description = str(body.get("body") or "")
            if len(description) > 65536:
                raise ValueError("이슈 본문이 너무 깁니다.")
            changes["body"] = description
        if not changes:
            raise ValueError("수정할 제목 또는 본문을 입력해 주세요.")
        return jsonify({"item": service.update_issue(number, **changes)})

    @app.post("/api/issues/<int:number>/state")
    def set_issue_state(number: int):
        service = current_service()
        body = request.get_json(silent=True) or {}
        value = body.get("state")
        if not isinstance(value, str) or value not in {"open", "closed"}:
            raise ValueError("이슈 상태는 open 또는 closed여야 합니다.")
        return jsonify({"item": service.update_issue(number, state=value)})

    @app.get("/api/assignees")
    def assignees():
        service = current_service()
        return jsonify({"items": service.list_assignees()})

    @app.put("/api/issues/<int:number>/assignees")
    def set_issue_assignees(number: int):
        service = current_service()
        body = request.get_json(silent=True) or {}
        values = body.get("assignees")
        if not isinstance(values, list) or len(values) > 10:
            raise ValueError("담당자는 최대 10명까지 선택할 수 있습니다.")
        names = list(dict.fromkeys(
            str(value).strip() for value in values
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9-]{1,39}", value.strip())
        ))
        if len(names) != len(values):
            raise ValueError("담당자 목록에 올바르지 않은 값이 있습니다.")
        available = {item["login"] for item in service.list_assignees()}
        if not set(names).issubset(available):
            raise ValueError("이 저장소에 접근 가능한 담당자만 지정할 수 있습니다.")
        return jsonify({"item": service.set_assignees(number, names)})

    @app.put("/api/issues/<int:number>/priority")
    def set_issue_priority(number: int):
        service = current_service()
        value = (request.get_json(silent=True) or {}).get("priority")
        if not isinstance(value, str) or value not in {"high", "medium", "low", "none"}:
            raise ValueError("우선순위 값이 올바르지 않습니다.")
        return jsonify({"item": service.set_priority(number, value)})

    @app.put("/api/issues/<int:number>/board-status")
    def set_board_status(number: int):
        service = current_service()
        value = (request.get_json(silent=True) or {}).get("status")
        return jsonify({"item": service.set_board_status(number, value)})

    @app.get("/api/issues/<int:number>/comments")
    def list_issue_comments(number: int):
        service = current_service()
        return jsonify({"items": service.list_comments(number)})

    @app.post("/api/issues/<int:number>/comments")
    def create_issue_comment(number: int):
        service = current_service()
        body = request.get_json(silent=True) or {}
        text = str(body.get("body") or "").strip()
        if not text or len(text) > 65536:
            raise ValueError("댓글은 1~65,536자로 입력해 주세요.")
        return jsonify({"item": service.create_comment(number, text)}), 201

    def verify_issue_comment(service: Service, number: int, comment_id: int) -> None:
        if not any(item["id"] == comment_id for item in service.list_comments(number)):
            raise LookupError("해당 이슈의 댓글을 찾을 수 없습니다.")

    @app.patch("/api/issues/<int:number>/comments/<int:comment_id>")
    def update_issue_comment(number: int, comment_id: int):
        service = current_service()
        verify_issue_comment(service, number, comment_id)
        # Preserve Markdown-significant whitespace exactly; only use strip for validation.
        text = str((request.get_json(silent=True) or {}).get("body") or "")
        if not text.strip() or len(text) > 65536:
            raise ValueError("댓글은 1~65,536자로 입력해 주세요.")
        return jsonify({"item": service.update_comment(comment_id, text)})

    @app.delete("/api/issues/<int:number>/comments/<int:comment_id>")
    def delete_issue_comment(number: int, comment_id: int):
        service = current_service()
        verify_issue_comment(service, number, comment_id)
        service.delete_comment(comment_id)
        return jsonify({"ok": True})

    @app.post("/api/issues/bulk")
    def bulk_issue_action():
        service = current_service()
        body = request.get_json(silent=True) or {}
        numbers = body.get("numbers")
        action = body.get("action")
        if not isinstance(numbers, list) or not 1 <= len(numbers) <= 50:
            raise ValueError("일괄 작업은 1~50개 이슈를 선택해 주세요.")
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in numbers):
            raise ValueError("이슈 번호 목록이 올바르지 않습니다.")
        numbers = list(dict.fromkeys(numbers))
        results = []
        for number in numbers:
            try:
                if action == "close":
                    service.update_issue(number, state="closed")
                elif action == "reopen":
                    service.update_issue(number, state="open")
                elif action == "assign":
                    assignees = body.get("assignees")
                    if not isinstance(assignees, list):
                        raise ValueError("담당자를 선택해 주세요.")
                    service.set_assignees(number, assignees)
                elif action == "priority":
                    service.set_priority(number, body.get("priority", ""))
                else:
                    raise ValueError("지원하지 않는 일괄 작업입니다.")
                results.append({"number": number, "ok": True})
            except Exception as exc:
                results.append({
                    "number": number, "ok": False,
                    "error": str(exc)[:240],
                })
        return jsonify({
            "items": results,
            "succeeded": sum(1 for item in results if item["ok"]),
            "failed": sum(1 for item in results if not item["ok"]),
        })

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

        login = require_login()
        previous = get_history(login.user.get("id"), service.cfg.github_repo, number)
        try:
            result = service.run(number, force=force)
            history_id = save_analysis(
                login.user.get("id"), service.cfg.github_repo, number,
                result["issue"].get("title", ""), result["analysis"], "complete",
            )
        except Exception as exc:
            save_analysis(
                login.user.get("id"), service.cfg.github_repo, number,
                "", None, "failed", str(exc),
            )
            raise
        result["history_id"] = history_id
        old = next((item for item in previous if item.get("analysis")), None)
        if old:
            earlier = old["analysis"]
            result["comparison"] = {
                key: {"previous": earlier.get(key), "current": result["analysis"].get(key)}
                for key in ("category", "priority", "probable_cause", "solution", "recommended_labels")
                if earlier.get(key) != result["analysis"].get(key)
            }
        return jsonify(result)

    @app.post("/api/issues/<int:number>/apply")
    def apply_analysis(number: int):
        login = require_login()
        service = current_service()
        body = request.get_json(silent=True) or {}
        history_id = body.get("history_id")
        apply_labels = body.get("apply_labels", False)
        post_comment = body.get("post_comment", False)
        if not isinstance(history_id, int) or isinstance(history_id, bool):
            raise ValueError("분석 이력 정보가 올바르지 않습니다.")
        if not isinstance(apply_labels, bool) or not isinstance(post_comment, bool):
            raise ValueError("적용 옵션이 올바르지 않습니다.")
        entry = next(
            (item for item in get_history(login.user.get("id"), service.cfg.github_repo, number)
             if item["id"] == history_id),
            None,
        )
        if entry is None or not entry["analysis"]:
            raise LookupError("이 분석 이력에 접근할 수 없습니다.")
        if entry["github_status"] == "applied":
            raise ValueError("이 분석 결과는 이미 GitHub에 적용되었습니다.")
        try:
            outcome = service.apply(
                number, entry["analysis"], apply_labels=apply_labels,
                post_comment=post_comment,
                send_notification=entry["discord_status"] != "sent",
            )
            update_analysis(
                login.user.get("id"), history_id,
                github_status=outcome["status"],
                github_result={
                    "labels": outcome["labels"] if outcome["labels_applied"] else [],
                    "comment_applied": outcome["comment_applied"],
                },
                discord_status=(
                    "sent" if outcome["notification_sent"]
                    else "failed" if outcome["notification_error"]
                    else entry["discord_status"]
                ),
                error="; ".join(outcome["errors"]) or outcome["notification_error"] or "",
            )
            outcome["issue"]["analysis_id"] = history_id
            outcome["issue"]["discord_status"] = (
                "sent" if outcome["notification_sent"]
                else "failed" if outcome["notification_error"]
                else entry["discord_status"]
            )
            return jsonify(outcome)
        except Exception as exc:
            update_analysis(
                login.user.get("id"), history_id,
                github_status="failed", error=str(exc),
            )
            raise

    @app.post("/api/analysis-history/<int:history_id>/retry-discord")
    def retry_discord(history_id: int):
        login = require_login()
        service = current_service()
        entry = next(
            (item for item in get_history(login.user.get("id"), service.cfg.github_repo)
             if item["id"] == history_id),
            None,
        )
        if entry is None:
            raise LookupError("분석 이력을 찾을 수 없습니다.")
        if entry["github_status"] not in {"applied", "partial"}:
            raise ValueError("GitHub 반영이 성공한 분석만 알림을 재시도할 수 있습니다.")
        if entry["discord_status"] == "sent":
            raise ValueError("이 분석 결과의 Discord 알림은 이미 전송되었습니다.")
        if not service.cfg.discord_webhook_url:
            raise ValueError("먼저 Discord Webhook URL을 설정해 주세요.")
        issue = service._client().get_issue(entry["issue_number"])
        try:
            send_discord(service.cfg.discord_webhook_url, issue, entry["analysis"])
            update_analysis(login.user.get("id"), history_id, discord_status="sent", error="")
            return jsonify({"ok": True, "message": "Discord 알림을 전송했습니다."})
        except RuntimeError as exc:
            update_analysis(login.user.get("id"), history_id, discord_status="failed", error=str(exc))
            raise

    @app.get("/api/analysis-history")
    def analysis_history():
        login = require_login()
        service = current_service()
        return jsonify({
            "items": get_history(login.user.get("id"), service.cfg.github_repo),
            "statistics": statistics(login.user.get("id"), service.cfg.github_repo),
        })

    @app.get("/api/issues/<int:number>/analysis-history")
    def issue_analysis_history(number: int):
        login = require_login()
        service = current_service()
        return jsonify({"items": get_history(login.user.get("id"), service.cfg.github_repo, number)})

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
