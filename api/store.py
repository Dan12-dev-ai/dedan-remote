"""
User data store for DEDAN Remote.

Separate SQLite database — the discovery engine's data/opportunities.db is
never modified by user actions (save, applications, preferences, sessions).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
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


def _empty_preferences() -> dict[str, Any]:
    return {
        "categories": [],
        "experience": None,
        "regions": [],
        "remote_only": False,
        "beginner_friendly": False,
        "updated_at": None,
    }


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
                -- Multi-provider authentication identities (email/password, Google, etc.)
                CREATE TABLE IF NOT EXISTS user_identities (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,          -- 'email', 'google', etc.
                    provider_user_id TEXT NOT NULL,  -- provider-specific id
                    created_at TEXT NOT NULL,
                    UNIQUE (provider, provider_user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_identities_user
                    ON user_identities (user_id);
                -- Lookup indexes for the hot paths: session validation on
                -- every authenticated request, plus per-user list queries.
                CREATE INDEX IF NOT EXISTS idx_sessions_expires
                    ON sessions (expires_at);
                CREATE INDEX IF NOT EXISTS idx_saved_jobs_user
                    ON saved_jobs (user_id);
                CREATE INDEX IF NOT EXISTS idx_applications_user
                    ON applications (user_id);

                -- Professional profile (identity stays on `users`).
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    headline TEXT,
                    bio TEXT,
                    location TEXT,
                    timezone TEXT,
                    languages TEXT NOT NULL DEFAULT '[]',
                    skills TEXT NOT NULL DEFAULT '[]',
                    experience_level TEXT,
                    portfolio_url TEXT,
                    github_url TEXT,
                    linkedin_url TEXT,
                    resume_filename TEXT,
                    resume_mime TEXT,
                    resume_size INTEGER,
                    resume_updated_at TEXT,
                    visibility TEXT NOT NULL DEFAULT 'private',
                    updated_at TEXT
                );

                -- One row per user; each column is one settings domain kept as
                -- a JSON object so domains merge independently and a whole
                -- domain can never be clobbered by a partial patch.
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    notifications TEXT NOT NULL DEFAULT '{}',
                    email_prefs TEXT NOT NULL DEFAULT '{}',
                    interview TEXT NOT NULL DEFAULT '{}',
                    voice TEXT NOT NULL DEFAULT '{}',
                    privacy TEXT NOT NULL DEFAULT '{}',
                    appearance TEXT NOT NULL DEFAULT '{}',
                    accessibility TEXT NOT NULL DEFAULT '{}',
                    discovery TEXT NOT NULL DEFAULT '{}',
                    personalization TEXT NOT NULL DEFAULT '{}',
                    region TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT
                );
                """
            )
            conn.executescript(
                """
                -- Server-side security audit trail. Deliberately no foreign key
                -- to users: the record of a security event must survive account
                -- deletion (the "retained for security reasons" data the danger
                -- zone tells the user about).
                CREATE TABLE IF NOT EXISTS security_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_security_events_user
                    ON security_events (user_id, created_at DESC);

                -- TOTP second factor (RFC 6238). `confirmed` distinguishes an
                -- enrollment in progress from a live factor, so a half-finished
                -- setup can never gate login.
                CREATE TABLE IF NOT EXISTS mfa_factors (
                    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    secret TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS mfa_recovery_codes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    code_hash TEXT NOT NULL,
                    used_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mfa_recovery_user
                    ON mfa_recovery_codes (user_id);

                -- Short-lived single-use challenges issued when a password
                -- login needs a second factor (never a session by itself).
                CREATE TABLE IF NOT EXISTS mfa_challenges (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                );

                -- Async data-export jobs; the file lives on disk, the row owns
                -- expiry and ownership.
                CREATE TABLE IF NOT EXISTS data_exports (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'preparing',
                    file_path TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_data_exports_user
                    ON data_exports (user_id, created_at DESC);
                """
            )
            # Additive migrations for databases created before these columns
            # existed. ALTER TABLE has no IF NOT EXISTS, so inspect first.
            self._ensure_columns(
                conn,
                "users",
                {
                    "email_verified": "INTEGER NOT NULL DEFAULT 0",
                    "pending_email": "TEXT",
                    "pending_email_purpose": "TEXT",
                    "pending_email_token_hash": "TEXT",
                    "pending_email_expires_at": "TEXT",
                    "reauth_hash": "TEXT",
                    "reauth_expires_at": "TEXT",
                    "password_set_by_user": "INTEGER NOT NULL DEFAULT 1",
                },
            )
            self._ensure_columns(
                conn,
                "sessions",
                {"id": "TEXT", "user_agent": "TEXT", "last_seen_at": "TEXT"},
            )
            self._ensure_columns(
                conn,
                "preferences",
                {"remote_only": "INTEGER NOT NULL DEFAULT 0",
                 "beginner_friendly": "INTEGER NOT NULL DEFAULT 0"},
            )
            # Sessions created before the id migration get an opaque id so the
            # API never exposes a token hash as an identifier.
            conn.execute(
                "UPDATE sessions SET id = lower(hex(randomblob(16))) WHERE id IS NULL"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_id ON sessions (id)"
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_columns(
        conn: sqlite3.Connection, table: str, columns: dict[str, str]
    ) -> None:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

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

    def get_user_by_identity(
        self, provider: str, provider_user_id: str
    ) -> Optional[dict[str, Any]]:
        """Find a user by their authentication provider identity."""
        conn = self.connect()
        try:
            row = conn.execute(
                """SELECT u.* FROM users u
                   JOIN user_identities ui ON ui.user_id = u.id
                   WHERE ui.provider = ? AND ui.provider_user_id = ?""",
                (provider, provider_user_id),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def create_user_identity(self, user_id: str, provider: str, provider_user_id: str) -> None:
        """Link an authentication identity to an existing user."""
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO user_identities (id, user_id, provider, provider_user_id, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:16], user_id, provider, provider_user_id, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def add_identity_to_user(self, user_id: str, provider: str, provider_user_id: str) -> None:
        """Attach a new auth provider identity to an existing user."""
        self.create_user_identity(user_id, provider, provider_user_id)

    def create_session(
        self,
        token_hash: str,
        user_id: str,
        expires_at: str,
        user_agent: Optional[str] = None,
    ) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO sessions (token_hash, id, user_id, created_at,"
                " expires_at, user_agent, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    token_hash,
                    uuid.uuid4().hex[:16],
                    user_id,
                    _now(),
                    expires_at,
                    (user_agent or "")[:300] or None,
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def touch_session(self, token_hash: str, user_agent: Optional[str] = None) -> None:
        """
        Record activity for the current session, at most once every few
        minutes. Called from the auth dependency; the staleness guard keeps
        the hot path from writing on every request.
        """
        now = _now()
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT last_seen_at, user_agent FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if not row:
                return
            last = row["last_seen_at"] or ""
            ua = (user_agent or "")[:300] or None
            # Compare real datetimes — ISO-8601 UTC strings would only work
            # while the offset stays identical.
            fresh = False
            if last:
                try:
                    delta = datetime.now(timezone.utc) - datetime.fromisoformat(last)
                    fresh = delta < timedelta(minutes=5)
                except ValueError:
                    fresh = False
            if fresh:
                if ua and ua != row["user_agent"]:
                    conn.execute(
                        "UPDATE sessions SET user_agent = ? WHERE token_hash = ?",
                        (ua, token_hash),
                    )
                    conn.commit()
                return
            conn.execute(
                "UPDATE sessions SET last_seen_at = ?, user_agent = COALESCE(?, user_agent)"
                " WHERE token_hash = ?",
                (now, ua, token_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT id, user_agent, created_at, last_seen_at, expires_at"
                " FROM sessions WHERE user_id = ? AND expires_at > ?"
                " ORDER BY COALESCE(last_seen_at, created_at) DESC",
                (user_id, _now()),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def revoke_session(self, user_id: str, session_id: str) -> bool:
        """Delete one session — only if it belongs to user_id (IDOR guard)."""
        conn = self.connect()
        try:
            cur = conn.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
            conn.commit()
            return bool(cur.rowcount)
        finally:
            conn.close()

    def revoke_other_sessions(self, user_id: str, keep_token_hash: str) -> int:
        conn = self.connect()
        try:
            cur = conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user_id, keep_token_hash),
            )
            conn.commit()
            return int(cur.rowcount or 0)
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
                return _empty_preferences()
            return {
                "categories": json.loads(row["categories"] or "[]"),
                "experience": row["experience"],
                "regions": json.loads(row["regions"] or "[]"),
                "remote_only": bool(row["remote_only"]),
                "beginner_friendly": bool(row["beginner_friendly"]),
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
        remote_only: Optional[bool] = None,
        beginner_friendly: Optional[bool] = None,
    ) -> dict[str, Any]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM preferences WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                current = {
                    "categories": json.loads(row["categories"] or "[]"),
                    "experience": row["experience"],
                    "regions": json.loads(row["regions"] or "[]"),
                    "remote_only": bool(row["remote_only"]),
                    "beginner_friendly": bool(row["beginner_friendly"]),
                }
            else:
                current = dict(_empty_preferences())
                current.pop("updated_at", None)
            new_cats = categories if categories is not None else current["categories"]
            new_exp = experience if experience is not None else current["experience"]
            new_regs = regions if regions is not None else current["regions"]
            new_remote = remote_only if remote_only is not None else current["remote_only"]
            new_beginner = (
                beginner_friendly
                if beginner_friendly is not None
                else current["beginner_friendly"]
            )
            conn.execute(
                "INSERT INTO preferences (user_id, categories, experience, regions,"
                " remote_only, beginner_friendly, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET categories = excluded.categories,"
                " experience = excluded.experience, regions = excluded.regions,"
                " remote_only = excluded.remote_only,"
                " beginner_friendly = excluded.beginner_friendly,"
                " updated_at = excluded.updated_at",
                (
                    user_id,
                    json.dumps(new_cats),
                    new_exp,
                    json.dumps(new_regs),
                    1 if new_remote else 0,
                    1 if new_beginner else 0,
                    _now(),
                ),
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


    # ── Profile (professional identity) ──────────────────────────────────────

    def get_profile(self, user_id: str) -> dict[str, Any]:
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                return {
                    "headline": None,
                    "bio": None,
                    "location": None,
                    "timezone": None,
                    "languages": [],
                    "skills": [],
                    "experience_level": None,
                    "portfolio_url": None,
                    "github_url": None,
                    "linkedin_url": None,
                    "resume": None,
                    "visibility": "private",
                    "updated_at": None,
                }
            return {
                "headline": row["headline"],
                "bio": row["bio"],
                "location": row["location"],
                "timezone": row["timezone"],
                "languages": json.loads(row["languages"] or "[]"),
                "skills": json.loads(row["skills"] or "[]"),
                "experience_level": row["experience_level"],
                "portfolio_url": row["portfolio_url"],
                "github_url": row["github_url"],
                "linkedin_url": row["linkedin_url"],
                "resume": (
                    {
                        "filename": row["resume_filename"],
                        "mime": row["resume_mime"],
                        "size": row["resume_size"],
                        "updated_at": row["resume_updated_at"],
                    }
                    if row["resume_filename"]
                    else None
                ),
                "visibility": row["visibility"] or "private",
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def update_profile(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Merge an explicit-field patch into the profile row."""
        allowed = {
            "headline",
            "bio",
            "location",
            "timezone",
            "languages",
            "skills",
            "experience_level",
            "portfolio_url",
            "github_url",
            "linkedin_url",
            "visibility",
        }
        fields = {k: v for k, v in patch.items() if k in allowed and v is not None}
        conn = self.connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not exists:
                conn.execute("INSERT INTO profiles (user_id) VALUES (?)", (user_id,))
            for key, value in fields.items():
                if key in ("languages", "skills"):
                    value = json.dumps(value)
                conn.execute(
                    f"UPDATE profiles SET {key} = ? WHERE user_id = ?",
                    (value, user_id),
                )
            conn.execute(
                "UPDATE profiles SET updated_at = ? WHERE user_id = ?",
                (_now(), user_id),
            )
            conn.commit()
            return self.get_profile(user_id)
        finally:
            conn.close()


    def set_resume(
        self, user_id: str, filename: str, mime: str, size: int, data: bytes
    ) -> dict[str, Any]:
        conn = self.connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not exists:
                conn.execute("INSERT INTO profiles (user_id) VALUES (?)", (user_id,))
            conn.execute(
                "UPDATE profiles SET resume_filename = ?, resume_mime = ?,"
                " resume_size = ?, resume_updated_at = ? WHERE user_id = ?",
                (filename[:200], mime[:100], size, _now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()
        path = self._resume_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.get_profile(user_id)

    def get_resume(self, user_id: str) -> Optional[dict[str, Any]]:
        profile = self.get_profile(user_id)
        if not profile.get("resume"):
            return None
        path = self._resume_path(user_id)
        if not path.exists():
            return None
        return {**profile["resume"], "data": path.read_bytes()}

    def delete_resume(self, user_id: str) -> dict[str, Any]:
        conn = self.connect()
        try:
            conn.execute(
                "UPDATE profiles SET resume_filename = NULL, resume_mime = NULL,"
                " resume_size = NULL, resume_updated_at = NULL WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
        finally:
            conn.close()
        self._resume_path(user_id).unlink(missing_ok=True)
        return self.get_profile(user_id)

    @staticmethod
    def _resume_path(user_id: str) -> Path:
        # user_id is a server-generated uuid hex — never client input.
        return Path(USER_DB_PATH).parent / "user_files" / user_id / "resume"

    # ── Settings domains ────────────────────────────────────────────────────

    SETTINGS_DOMAINS = (
        "notifications",
        "email_prefs",
        "interview",
        "voice",
        "privacy",
        "appearance",
        "accessibility",
        "discovery",
        "personalization",
        "region",
    )

    def get_settings_domain(self, user_id: str, domain: str) -> dict[str, Any]:
        conn = self.connect()
        try:
            row = conn.execute(
                f"SELECT {domain} FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not row:
                return {}
            return json.loads(row[domain] or "{}")
        finally:
            conn.close()

    def get_all_settings(self, user_id: str) -> dict[str, Any]:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if not row:
                out: dict[str, Any] = {d: {} for d in self.SETTINGS_DOMAINS}
                out["updated_at"] = None
                return out
            out = {d: json.loads(row[d] or "{}") for d in self.SETTINGS_DOMAINS}
            out["updated_at"] = row["updated_at"]
            return out
        finally:
            conn.close()

    def patch_settings_domain(
        self, user_id: str, domain: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Shallow-merge a patch into one domain column. Only keys present in the
        patch change; unknown keys were already rejected by the schema layer.
        `domain` is always one of SETTINGS_DOMAINS (validated by the router),
        never raw client input.
        """
        conn = self.connect()
        try:
            row = conn.execute(
                f"SELECT {domain} FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            current = json.loads(row[domain] or "{}") if row else {}
            merged = {**current, **patch}
            conn.execute(
                f"INSERT INTO user_settings (user_id, {domain}, updated_at)"
                f" VALUES (?, ?, ?)"
                f" ON CONFLICT(user_id) DO UPDATE SET {domain} = excluded.{domain},"
                f" updated_at = excluded.updated_at",
                (user_id, json.dumps(merged), _now()),
            )
            conn.commit()
            return merged
        finally:
            conn.close()


    # ── Security audit & MFA ─────────────────────────────────────────────────

    def add_security_event(
        self, user_id: str, event: str, detail: Optional[dict[str, Any]] = None
    ) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO security_events (id, user_id, event, detail, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:16], user_id, event, json.dumps(detail or {}), _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def list_security_events(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT id, event, detail, created_at FROM security_events"
                " WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "event": r["event"],
                    "detail": json.loads(r["detail"] or "{}"),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_mfa(self, user_id: str) -> Optional[dict[str, Any]]:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT secret, confirmed, created_at, confirmed_at"
                " FROM mfa_factors WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            return dict(row)
        finally:
            conn.close()

    def set_mfa_secret(self, user_id: str, secret: str) -> None:
        """Stage (or re-stage) an unconfirmed TOTP secret."""
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO mfa_factors (user_id, secret, confirmed, created_at)"
                " VALUES (?, ?, 0, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET secret = excluded.secret,"
                " confirmed = 0, created_at = excluded.created_at, confirmed_at = NULL",
                (user_id, secret, _now()),
            )
            conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    def confirm_mfa(self, user_id: str) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "UPDATE mfa_factors SET confirmed = 1, confirmed_at = ?"
                " WHERE user_id = ?",
                (_now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def disable_mfa(self, user_id: str) -> None:
        conn = self.connect()
        try:
            conn.execute("DELETE FROM mfa_factors WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM mfa_recovery_codes WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM mfa_challenges WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()

    def add_recovery_codes(self, user_id: str, code_hashes: list[str]) -> None:
        conn = self.connect()
        try:
            conn.executemany(
                "INSERT INTO mfa_recovery_codes (id, user_id, code_hash, created_at)"
                " VALUES (?, ?, ?, ?)",
                [(uuid.uuid4().hex[:16], user_id, h, _now()) for h in code_hashes],
            )
            conn.commit()
        finally:
            conn.close()

    def consume_recovery_code(self, user_id: str, code_hash: str) -> bool:
        """Atomically mark one recovery code used; False when unknown/used."""
        conn = self.connect()
        try:
            cur = conn.execute(
                "UPDATE mfa_recovery_codes SET used_at = ?"
                " WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
                (_now(), user_id, code_hash),
            )
            conn.commit()
            return bool(cur.rowcount)
        finally:
            conn.close()

    def count_unused_recovery_codes(self, user_id: str) -> int:
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mfa_recovery_codes"
                " WHERE user_id = ? AND used_at IS NULL",
                (user_id,),
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    def create_mfa_challenge(self, token_hash: str, user_id: str, expires_at: str) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO mfa_challenges (token_hash, user_id, expires_at)"
                " VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )
            conn.commit()
        finally:
            conn.close()

    def consume_mfa_challenge(self, token_hash: str) -> Optional[str]:
        """Single-use: the UPDATE only succeeds once, so a race can't redeem twice."""
        conn = self.connect()
        try:
            cur = conn.execute(
                "UPDATE mfa_challenges SET used = 1"
                " WHERE token_hash = ? AND used = 0 AND expires_at > ?",
                (token_hash, _now()),
            )
            if not cur.rowcount:
                return None
            conn.commit()
            row = conn.execute(
                "SELECT user_id FROM mfa_challenges WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            return row["user_id"] if row else None
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
