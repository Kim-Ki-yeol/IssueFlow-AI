"""IssueFlow 대시보드. 로컬 Flask와 Vercel에서 같은 앱을 사용."""
from __future__ import annotations

import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import HTTPException

from agent.config import get_config
from agent.github_client import GitHubError
from agent.service import Service

ROOT = Path(__file__).resolve().parent


def create_app() -> Flask:
    # Vercel은 public/을 CDN으로 제공하고, 로컬에서는 아래 파일 경로로 제공
    app = Flask(__name__, template_folder="templates", static_folder=None)
    cfg = get_config()
    service = Service(cfg)

    @app.before_request
    def protect_dashboard():
        if not cfg.dashboard_password:
            return jsonify({"error": "DASHBOARD_PASSWORD를 먼저 설정해 주세요."}), 503
        auth = request.authorization
        if not auth or not hmac.compare_digest(auth.username or "", "admin") or not hmac.compare_digest(auth.password or "", cfg.dashboard_password):
            response = jsonify({"error": "관리자 로그인이 필요합니다."})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = 'Basic realm="IssueFlow AI", charset="UTF-8"'
            return response
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # 외부 사이트에서 관리 API를 호출하지 못하도록 기본적인 출처 확인
            if request.headers.get("Sec-Fetch-Site") == "cross-site":
                return jsonify({"error": "다른 사이트에서 보낸 요청은 허용하지 않습니다."}), 403
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc.lower() != request.host.lower():
                return jsonify({"error": "허용하지 않는 요청 출처입니다."}), 403
            if not request.is_json:
                return jsonify({"error": "JSON 형식으로 요청해 주세요."}), 415
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.errorhandler(Exception)
    def handle_error(exc):
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description}), exc.code
        if isinstance(exc, LookupError):
            return jsonify({"error": str(exc)}), 404
        if isinstance(exc, (RuntimeError, GitHubError, ValueError)):
            return jsonify({"error": str(exc)}), 400
        app.logger.exception("Unexpected error")
        return jsonify({"error": "서버 오류가 발생했습니다. 로그를 확인해 주세요."}), 500

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/styles.css")
    @app.get("/app.js")
    def local_assets():
        return send_from_directory(ROOT / "public", request.path.lstrip("/"))

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/api/config")
    def config():
        # 키 원본은 프론트엔드에 절대 보내지 않음
        return jsonify({
            "repo": cfg.github_repo, "ai_enabled": cfg.ai_ready,
            "github_enabled": cfg.github_ready, "discord_enabled": bool(cfg.discord_webhook_url),
            "writes_enabled": cfg.github_writes, "model": cfg.openai_model,
        })

    @app.get("/api/issues")
    def issues():
        return jsonify({"items": service.issue_list()})

    @app.get("/api/issues/<int:number>")
    def issue_detail(number: int):
        return jsonify(service.issue_detail(number))

    @app.post("/api/issues/<int:number>/analyze")
    def analyze(number: int):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or not isinstance(body.get("force", False), bool):
            return jsonify({"error": "force 값은 true/false만 사용할 수 있습니다."}), 400
        return jsonify(service.run(number, force=body.get("force", False)))

    @app.get("/api/activity")
    def activity():
        return jsonify({"items": service.activity()})

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
