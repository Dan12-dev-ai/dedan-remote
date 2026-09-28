"""
User data store for DEDAN Remote.

Separate SQLite database — the discovery engine's data/opportunities.db is
never modified by user actions (save, applications, preferences, sessions).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

USER_DB_PATH = "data/dedan_users.db"

VALID_STATUSES = (
    "viewed",
    "saved",
    "application_started",
    "applied",
    "rejected",
    "interview",
    "offer",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserStore:
    """SQLite-backed store for users, sessions, saved jobs, applications."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or USER_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    # == CREATE_TABLES ==

    def create_tables(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saved_jobs (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,
                    note TEXT,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'application_started',
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (user_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    categories TEXT NOT NULL DEFAULT '[]',
                    experience TEXT,
                    regions TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT
                );
                -- Lookup indexes for the hot paths: session validation on
                -- every authenticated request, plus per-user list queries.
                CREATE INDEX IF NOT EXISTS idx_sessions_expires
                    ON sessions (expires_at);
                CREATE INDEX IF NOT EXISTS idx_saved_jobs_user
                    ON saved_jobs (user_id);
                CREATE INDEX IF NOT EXISTS idx_applications_user
                    ON applications (user_id);
                """
            )
            conn.commit()
        finally:
            conn.close()

    # == USERS_SESSIONS ==

    def create_user(
        self, email: str, password_hash: str, display_name: Optional[str] = None
    ) -> dict[str, Any]:
        conn = self.connect()
        try:
            user_id = uuid.uuid4().hex
            now = _now()
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, email.lower(), password_hash, display_name, now),
            )
            conn.execute(
                "INSERT INTO preferences (user_id) VALUES (?) ON CONFLICT DO NOTHING",
                (user_id,),
            )
            conn.commit()
            return {
                "id": user_id,
                "email": email.lower(),
                "display_name": display_name,
                "created_at": now,
            }
        finally:
            conn.close()

    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def create_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (token_hash, user_id, _now(), expires_at),
            )
            conn.commit()
        finally:
            conn.close()

    def get_session_user(self, token_hash: str) -> Optional[str]:
        """Return user_id for a valid, unexpired session token hash."""
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] < _now():
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                conn.commit()
                return None
            return row["user_id"]
        finally:
            conn.close()

    def delete_session(self, token_hash: str) -> None:
        conn = self.connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()
        finally:
            conn.close()

    def purge_expired_sessions(self) -> int:
        """
        Delete every expired session row and return how many were removed.

        Sessions are already rejected individually on lookup, but without this
        the table grows forever on a long-lived deployment. Called at startup
        so expired tokens never accumulate across restarts.
        """
        conn = self.connect()
        try:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
            conn.commit()
            return int(cursor.rowcount or 0)
        finally:
            conn.close()

    # == SAVED_JOBS ==

    def save_job(self, user_id: str, job_id: str, note: Optional[str]) -> str:
        conn = self.connect()
        try:
            now = _now()
            conn.execute(
                "INSERT INTO saved_jobs (user_id, job_id, note, saved_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(user_id, job_id) DO UPDATE SET note = excluded.note",
                (user_id, job_id, note, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT saved_at FROM saved_jobs WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            ).fetchone()
            return row["saved_at"] if row else now
        finally:
            conn.close()

    def unsave_job(self, user_id: str, job_id: str) -> bool:
        conn = self.connect()
        try:
            cur = conn.execute(
                "DELETE FROM saved_jobs WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_saved(self, user_id: str) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT job_id, note, saved_at FROM saved_jobs"
                " WHERE user_id = ? ORDER BY saved_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def is_saved(self, user_id: str, job_ids: list[str]) -> set[str]:
        if not job_ids:
            return set()
        conn = self.connect()
        try:
            placeholders = ",".join("?" * len(job_ids))
            rows = conn.execute(
                f"SELECT job_id FROM saved_jobs WHERE user_id = ? AND job_id IN ({placeholders})",
                [user_id, *job_ids],
            ).fetchall()
            return {r["job_id"] for r in rows}
        finally:
            conn.close()

    # == APPLICATIONS ==

    def create_application(
        self, user_id: str, job_id: str, status: str, note: Optional[str]
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        conn = self.connect()
        try:
            now = _now()
            existing = conn.execute(
                "SELECT * FROM applications WHERE user_id = ? AND job_id = ?",
                (user_id, job_id),
            ).fetchone()
            if existing:
                final_note = note if note is not None else existing["note"]
                conn.execute(
                    "UPDATE applications SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                    (status, final_note, now, existing["id"]),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM applications WHERE id = ?", (existing["id"],)
                ).fetchone()
                return dict(row)
            app_id = uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT INTO applications (id, user_id, job_id, status, note,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (app_id, user_id, job_id, status, note, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
            return dict(row)
        finally:
            conn.close()

    def patch_application(
        self, user_id: str, app_id: str, status: Optional[str], note: Optional[str]
    ) -> Optional[dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ? AND user_id = ?",
                (app_id, user_id),
            ).fetchone()
            if not row:
                return None
            new_status = status if status is not None else row["status"]
            if new_status not in VALID_STATUSES:
                raise ValueError(f"invalid status: {new_status}")
            new_note = note if note is not None else row["note"]
            conn.execute(
                "UPDATE applications SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                (new_status, new_note, _now(), app_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
            return dict(row)
        finally:
            conn.close()

    def list_applications(self, user_id: str) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM applications WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_application_status_for_jobs(self, user_id: str, job_ids: list[str]) -> dict[str, str]:
        if not job_ids:
            return {}
        conn = self.connect()
        try:
            placeholders = ",".join("?" * len(job_ids))
            rows = conn.execute(
                f"SELECT job_id, status FROM applications WHERE user_id = ?"
                f" AND job_id IN ({placeholders})",
                [user_id, *job_ids],
            ).fetchall()
            return {r["job_id"]: r["status"] for r in rows}
        finally:
            conn.close()

    # ── Preferences ─────────────────────────────────────────────────────────

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM preferences WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                return {"categories": [], "experience": None, "regions": [], "updated_at": None}
            return {
                "categories": json.loads(row["categories"] or "[]"),
                "experience": row["experience"],
                "regions": json.loads(row["regions"] or "[]"),
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def update_preferences(
        self,
        user_id: str,
        categories: Optional[list[str]],
        experience: Optional[str],
        regions: Optional[list[str]],
    ) -> dict[str, Any]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM preferences WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                current = {
                    "categories": json.loads(row["categories"] or "[]"),
                    "experience": row["experience"],
                    "regions": json.loads(row["regions"] or "[]"),
                }
            else:
                current = {"categories": [], "experience": None, "regions": []}
            new_cats = categories if categories is not None else current["categories"]
            new_exp = experience if experience is not None else current["experience"]
            new_regs = regions if regions is not None else current["regions"]
            conn.execute(
                "INSERT INTO preferences (user_id, categories, experience, regions,"
                " updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET categories = excluded.categories,"
                " experience = excluded.experience, regions = excluded.regions,"
                " updated_at = excluded.updated_at",
                (user_id, json.dumps(new_cats), new_exp, json.dumps(new_regs), _now()),
            )
            conn.commit()
            return self.get_preferences(user_id)
        finally:
            conn.close()

    def update_display_name(self, user_id: str, display_name: str) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?",
                (display_name, user_id),
            )
            conn.commit()
        finally:
            conn.close()


_store: Optional[UserStore] = None


def get_user_store() -> UserStore:
    """Singleton user store with tables ensured on first use."""
    global _store
    if _store is None:
        _store = UserStore()
        _store.create_tables()
    return _store


def reset_user_store_for_tests(path: str) -> UserStore:
    """Replace the singleton (tests only)."""
    global _store
    _store = UserStore(path)
    _store.create_tables()
    return _store
