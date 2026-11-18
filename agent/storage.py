"""User-scoped SQLite storage for encrypted integration settings and analysis history."""
from __future__ import annotations

import json
import os
import sqlite3
import atexit
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import ROOT


_POOL = None
_POOL_URL = None
_POOL_LOCK = threading.Lock()


def database_backend() -> str:
    """Use Neon only in Vercel; local development always keeps its SQLite file."""
    if os.getenv("VERCEL", "").strip() == "1":
        if not os.getenv("DATABASE_URL", "").strip():
            raise RuntimeError("Vercel 배포 환경에는 DATABASE_URL 설정이 필요합니다.")
        return "postgres"
    return "sqlite"


def persistent_sessions_enabled() -> bool:
    return database_backend() == "postgres"


def database_path() -> Path:
    configured = os.getenv("ISSUEFLOW_DATABASE_PATH", "").strip()
    path = Path(configured) if configured else ROOT / "instance" / "issueflow.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class _PostgresConnection:
    """Small adapter for the existing SQLite-style qmark statements."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, query: str, parameters=()):
        return self._connection.execute(query.replace("?", "%s"), parameters)


def _postgres_pool():
    global _POOL, _POOL_URL
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("Vercel 배포 환경에는 DATABASE_URL 설정이 필요합니다.")
    with _POOL_LOCK:
        if _POOL is not None and _POOL_URL == url:
            return _POOL
        if _POOL is not None:
            _POOL.close()
            _POOL = None
            _POOL_URL = None
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                conninfo=url,
                min_size=0,
                max_size=3,
                timeout=10,
                max_lifetime=300,
                max_idle=60,
                kwargs={"row_factory": dict_row},
                open=False,
            )
            pool.open(wait=False)
        except Exception:
            raise RuntimeError("Neon PostgreSQL 연결 풀을 초기화하지 못했습니다.") from None
        _POOL = pool
        _POOL_URL = url
        return _POOL


def close_pool() -> None:
    """Close the per-instance pool at process shutdown, not per request."""
    global _POOL, _POOL_URL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.close()
            _POOL = None
            _POOL_URL = None


atexit.register(close_pool)


def _cipher() -> Fernet:
    key = os.getenv("DATA_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "사용자 설정을 안전하게 저장하려면 DATA_ENCRYPTION_KEY 환경변수가 필요합니다."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("DATA_ENCRYPTION_KEY 형식이 올바르지 않습니다.") from exc


@contextmanager
def connect():
    if database_backend() == "postgres":
        pool = _postgres_pool()
        with pool.connection() as connection:
            yield _PostgresConnection(connection)
        return

    db = sqlite3.connect(database_path(), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 10000")
    db.execute("PRAGMA journal_mode = WAL")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    if database_backend() == "postgres":
        _cipher()  # The same key encrypts user settings and OAuth access tokens.
        try:
            with connect() as db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS user_settings (
                        user_id TEXT PRIMARY KEY,
                        openai_key BYTEA,
                        discord_webhook BYTEA,
                        selected_repo TEXT NOT NULL DEFAULT '',
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS analysis_history (
                        id BIGSERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        repository TEXT NOT NULL,
                        issue_number INTEGER NOT NULL,
                        issue_title TEXT NOT NULL DEFAULT '',
                        analysis_json TEXT,
                        analysis_status TEXT NOT NULL,
                        github_status TEXT NOT NULL DEFAULT 'pending',
                        github_result_json TEXT,
                        discord_status TEXT NOT NULL DEFAULT 'not_sent',
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_analysis_owner_repo
                    ON analysis_history(user_id, repository, created_at DESC)
                """)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        sid TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        encrypted_token BYTEA NOT NULL,
                        user_json TEXT NOT NULL,
                        expires_at DOUBLE PRECISION NOT NULL,
                        selected_repo TEXT NOT NULL DEFAULT '',
                        installation_id BIGINT NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry
                    ON auth_sessions(expires_at)
                """)
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("PostgreSQL 스키마 초기화에 실패했습니다.") from None
        return

    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                openai_key BLOB,
                discord_webhook BLOB,
                selected_repo TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                repository TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                issue_title TEXT NOT NULL DEFAULT '',
                analysis_json TEXT,
                analysis_status TEXT NOT NULL,
                github_status TEXT NOT NULL DEFAULT 'pending',
                github_result_json TEXT,
                discord_status TEXT NOT NULL DEFAULT 'not_sent',
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_analysis_owner_repo
                ON analysis_history(user_id, repository, created_at DESC);
        """)


def _owner(user_id: object) -> str:
    value = str(user_id or "").strip()
    if not value or len(value) > 100:
        raise ValueError("GitHub 사용자 식별 정보를 확인할 수 없습니다.")
    return value


def save_settings(user_id: object, openai_key: str, discord_webhook: str, repo: str) -> None:
    owner = _owner(user_id)
    cipher = _cipher()
    encrypted_key = cipher.encrypt(openai_key.encode()) if openai_key else None
    encrypted_webhook = cipher.encrypt(discord_webhook.encode()) if discord_webhook else None
    with connect() as db:
        db.execute("""
            INSERT INTO user_settings(user_id, openai_key, discord_webhook, selected_repo)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                openai_key = CASE WHEN excluded.openai_key IS NULL
                    THEN user_settings.openai_key ELSE excluded.openai_key END,
                discord_webhook = CASE WHEN excluded.discord_webhook IS NULL
                    THEN user_settings.discord_webhook ELSE excluded.discord_webhook END,
                selected_repo = excluded.selected_repo,
                updated_at = CURRENT_TIMESTAMP
        """, (owner, encrypted_key, encrypted_webhook, repo))


def clear_user_integration(user_id: object, integration: str) -> None:
    """Clear one encrypted credential while preserving the user's other settings."""
    columns = {
        "openai": "openai_key",
        "discord": "discord_webhook",
    }
    column = columns.get(integration)
    if column is None:
        raise ValueError("연동 종류가 올바르지 않습니다.")
    with connect() as db:
        db.execute(
            f"UPDATE user_settings SET {column} = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE user_id = ?",
            (_owner(user_id),),
        )


def get_settings(user_id: object) -> dict:
    owner = _owner(user_id)
    with connect() as db:
        row = db.execute("SELECT * FROM user_settings WHERE user_id = ?", (owner,)).fetchone()
    if row is None:
        return {"openai_api_key": "", "discord_webhook_url": "", "selected_repo": ""}
    cipher = _cipher() if row["openai_key"] or row["discord_webhook"] else None
    try:
        return {
            "openai_api_key": cipher.decrypt(row["openai_key"]).decode() if row["openai_key"] else "",
            "discord_webhook_url": cipher.decrypt(row["discord_webhook"]).decode() if row["discord_webhook"] else "",
            "selected_repo": row["selected_repo"],
        }
    except InvalidToken as exc:
        raise RuntimeError(
            "저장된 설정을 복호화할 수 없습니다. DATA_ENCRYPTION_KEY가 이전 값과 같은지 확인해 주세요."
        ) from exc


def setting_status(user_id: object) -> dict:
    owner = _owner(user_id)
    with connect() as db:
        row = db.execute(
            "SELECT openai_key, discord_webhook FROM user_settings WHERE user_id = ?",
            (owner,),
        ).fetchone()
    return {
        "openai_configured": bool(row and row["openai_key"]),
        "discord_configured": bool(row and row["discord_webhook"]),
    }


def save_login_session(
    sid: str, user: dict, token: str, expires_at: float,
    repo: str = "", installation_id: int = 0,
) -> None:
    """Persist a server-side OAuth session with the access token encrypted."""
    sid = str(sid or "").strip()
    if not sid or len(sid) > 200 or not token:
        raise ValueError("로그인 세션 정보가 올바르지 않습니다.")
    encrypted = _cipher().encrypt(token.encode("utf-8"))
    with connect() as db:
        db.execute("""
            INSERT INTO auth_sessions
                (sid, user_id, encrypted_token, user_json, expires_at, selected_repo, installation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sid) DO UPDATE SET
                user_id = excluded.user_id,
                encrypted_token = excluded.encrypted_token,
                user_json = excluded.user_json,
                expires_at = excluded.expires_at,
                selected_repo = excluded.selected_repo,
                installation_id = excluded.installation_id,
                updated_at = CURRENT_TIMESTAMP
        """, (
            sid, _owner(user.get("id")), encrypted,
            json.dumps(user, ensure_ascii=False), float(expires_at),
            str(repo or ""), int(installation_id or 0),
        ))


def load_login_session(sid: str) -> dict | None:
    sid = str(sid or "").strip()
    if not sid or len(sid) > 200:
        return None
    with connect() as db:
        row = db.execute(
            "SELECT * FROM auth_sessions WHERE sid = ?", (sid,)
        ).fetchone()
    if row is None:
        return None
    if float(row["expires_at"]) <= time.time():
        delete_login_session(sid)
        return None
    try:
        token = _cipher().decrypt(row["encrypted_token"]).decode("utf-8")
        user = json.loads(row["user_json"])
    except (InvalidToken, ValueError, TypeError) as exc:
        raise RuntimeError(
            "저장된 로그인 세션을 복호화할 수 없습니다. DATA_ENCRYPTION_KEY를 확인해 주세요."
        ) from exc
    return {
        "token": token,
        "user": user,
        "expires_at": float(row["expires_at"]),
        "repo": row["selected_repo"],
        "installation_id": int(row["installation_id"]),
    }


def delete_login_session(sid: str) -> None:
    if not sid:
        return
    with connect() as db:
        db.execute("DELETE FROM auth_sessions WHERE sid = ?", (str(sid),))


def delete_expired_login_sessions() -> None:
    with connect() as db:
        db.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (time.time(),))


def save_analysis(
    user_id: object, repo: str, issue_number: int, issue_title: str,
    analysis: dict | None, status: str, error: str | None = None,
) -> int:
    query = """
        INSERT INTO analysis_history
            (user_id, repository, issue_number, issue_title, analysis_json, analysis_status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    if database_backend() == "postgres":
        query += " RETURNING id"
    with connect() as db:
        cursor = db.execute(query, (
            _owner(user_id), repo, issue_number, issue_title,
            json.dumps(analysis, ensure_ascii=False) if analysis is not None else None,
            status, (error or "")[:500] or None,
        ))
        if database_backend() == "postgres":
            return int(cursor.fetchone()["id"])
        return int(cursor.lastrowid)


def update_analysis(
    user_id: object, history_id: int, *, github_status: str | None = None,
    github_result: dict | None = None, discord_status: str | None = None,
    error: str | None = None,
) -> None:
    assignments, values = [], []
    for column, value in (
        ("github_status", github_status),
        ("github_result_json", json.dumps(github_result, ensure_ascii=False) if github_result is not None else None),
        ("discord_status", discord_status),
        ("error", (error or "")[:500] or None if error is not None else None),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            values.append(value)
    if not assignments:
        return
    values.extend([_owner(user_id), history_id])
    with connect() as db:
        cursor = db.execute(
            f"UPDATE analysis_history SET {', '.join(assignments)} WHERE user_id = ? AND id = ?",
            values,
        )
        if cursor.rowcount != 1:
            raise LookupError("분석 이력을 찾을 수 없습니다.")


def clear_analysis_history(user_id: object) -> int:
    """Delete only analysis-history rows owned by one authenticated user."""
    with connect() as db:
        cursor = db.execute(
            "DELETE FROM analysis_history WHERE user_id = ?",
            (_owner(user_id),),
        )
        return max(int(cursor.rowcount), 0)


def history(user_id: object, repo: str, issue_number: int | None = None) -> list[dict]:
    query = """
        SELECT id, repository, issue_number, issue_title, analysis_json,
               analysis_status, github_status, github_result_json,
               discord_status, error, created_at
        FROM analysis_history WHERE user_id = ? AND repository = ?
    """
    args: list[object] = [_owner(user_id), repo]
    if issue_number is not None:
        query += " AND issue_number = ?"
        args.append(issue_number)
    query += " ORDER BY id DESC LIMIT 100"
    with connect() as db:
        rows = db.execute(query, args).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        created_at = item.get("created_at")
        if hasattr(created_at, "isoformat"):
            item["created_at"] = created_at.isoformat()
        item["analysis"] = json.loads(row["analysis_json"]) if row["analysis_json"] else None
        item["github_result"] = json.loads(row["github_result_json"]) if row["github_result_json"] else None
        items.append(item)
    return items


def statistics(user_id: object, repo: str) -> dict:
    with connect() as db:
        row = db.execute("""
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN github_status = 'applied' THEN 1 ELSE 0 END) AS applied,
                SUM(CASE WHEN analysis_status = 'complete' AND github_status <> 'applied' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN analysis_status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM analysis_history WHERE user_id = ? AND repository = ?
        """, (_owner(user_id), repo)).fetchone()
    return {
        "total": row["total"] or 0, "applied": row["applied"] or 0,
        "pending": row["pending"] or 0, "failed": row["failed"] or 0,
    }
