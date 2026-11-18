"""User-scoped SQLite storage for encrypted integration settings and analysis history."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import ROOT


def database_path() -> Path:
    configured = os.getenv("ISSUEFLOW_DATABASE_PATH", "").strip()
    path = Path(configured) if configured else ROOT / "instance" / "issueflow.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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


def save_analysis(
    user_id: object, repo: str, issue_number: int, issue_title: str,
    analysis: dict | None, status: str, error: str | None = None,
) -> int:
    with connect() as db:
        cursor = db.execute("""
            INSERT INTO analysis_history
                (user_id, repository, issue_number, issue_title, analysis_json, analysis_status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            _owner(user_id), repo, issue_number, issue_title,
            json.dumps(analysis, ensure_ascii=False) if analysis is not None else None,
            status, (error or "")[:500] or None,
        ))
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
    return [{
        **dict(row),
        "analysis": json.loads(row["analysis_json"]) if row["analysis_json"] else None,
        "github_result": json.loads(row["github_result_json"]) if row["github_result_json"] else None,
    } for row in rows]


def statistics(user_id: object, repo: str) -> dict:
    with connect() as db:
        row = db.execute("""
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN github_status = 'applied' THEN 1 ELSE 0 END) AS applied,
                SUM(CASE WHEN analysis_status = 'complete' AND github_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN analysis_status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM analysis_history WHERE user_id = ? AND repository = ?
        """, (_owner(user_id), repo)).fetchone()
    return {
        "total": row["total"] or 0, "applied": row["applied"] or 0,
        "pending": row["pending"] or 0, "failed": row["failed"] or 0,
    }
