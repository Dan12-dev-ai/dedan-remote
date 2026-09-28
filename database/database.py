"""
SQLite database layer with async support.
Handles schema creation, job storage, deduplication, and execution history.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from models.job import Job


class Database:
    """Async-compatible SQLite database manager."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        settings = get_settings()
        self._db_path = db_path or settings.DATABASE_PATH
        self._ensure_dir()
        self._conn: Optional[sqlite3.Connection] = None

    def _ensure_dir(self) -> None:
        """Create database directory if it doesn't exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Create and return a synchronous connection (used internally)."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def create_tables(self) -> None:
        """Create all required tables if they don't exist."""
        conn = self.connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                salary TEXT,
                country TEXT,
                remote INTEGER DEFAULT 1,
                posted_date TEXT,
                description TEXT,
                tags TEXT,
                discovered_at TEXT NOT NULL,
                score REAL DEFAULT 0,
                notified INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                sent_at TEXT,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );

            CREATE TABLE IF NOT EXISTS execution_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                jobs_found INTEGER DEFAULT 0,
                jobs_new INTEGER DEFAULT 0,
                jobs_notified INTEGER DEFAULT 0,
                errors TEXT,
                duration_seconds REAL
            );

            CREATE TABLE IF NOT EXISTS website_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL UNIQUE,
                last_checked TEXT,
                last_success TEXT,
                last_error TEXT,
                consecutive_failures INTEGER DEFAULT 0,
                circuit_open INTEGER DEFAULT 0,
                circuit_open_until TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_notified ON jobs(notified);
            CREATE INDEX IF NOT EXISTS idx_jobs_discovered ON jobs(discovered_at DESC);
        """)
        conn.commit()

    def job_exists(self, job_id: str) -> bool:
        """Check if a job with the given id already exists."""
        conn = self.connect()
        cursor = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
        return cursor.fetchone() is not None

    def insert_job(self, job: Job, score: float = 0.0) -> bool:
        """
        Insert a new job. Returns True if inserted, False if duplicate.
        """
        if self.job_exists(job.id):
            return False
        conn = self.connect()
        data = job.to_dict()
        conn.execute(
            """INSERT OR IGNORE INTO jobs
               (id, title, company, url, source, salary, country, remote,
                posted_date, description, tags, discovered_at, score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data["title"],
                data["company"],
                data["url"],
                data["source"],
                data["salary"],
                data["country"],
                data["remote"],
                data["posted_date"],
                data["description"],
                data["tags"],
                data["discovered_at"],
                score,
            ),
        )
        conn.commit()
        return True

    def mark_notified(self, job_id: str, channel: str = "email") -> None:
        """Mark a job as notified."""
        conn = self.connect()
        conn.execute(
            "UPDATE jobs SET notified = 1 WHERE id = ?",
            (job_id,),
        )
        conn.execute(
            """INSERT INTO notifications (job_id, channel, status, sent_at)
               VALUES (?, ?, 'sent', datetime('now'))""",
            (job_id, channel),
        )
        conn.commit()

    def get_new_unnotified_jobs(self, min_score: float = 0.0) -> list[dict[str, object]]:
        """Get jobs that haven't been notified yet, optionally filtered by score."""
        conn = self.connect()
        cursor = conn.execute(
            """SELECT * FROM jobs
               WHERE notified = 0 AND score >= ?
               ORDER BY score DESC, discovered_at DESC""",
            (min_score,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def start_execution(self) -> int:
        """Record execution start and return execution id."""
        conn = self.connect()
        cursor = conn.execute(
            "INSERT INTO execution_history (started_at, status) VALUES (datetime('now'), 'running')"
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def complete_execution(
        self,
        execution_id: int,
        jobs_found: int,
        jobs_new: int,
        jobs_notified: int,
        errors: Optional[str] = None,
    ) -> None:
        """Mark execution as completed with stats."""
        conn = self.connect()
        conn.execute(
            """UPDATE execution_history SET
               completed_at = datetime('now'),
               status = 'completed',
               jobs_found = ?,
               jobs_new = ?,
               jobs_notified = ?,
               errors = ?,
               duration_seconds = (
                   julianday('now') - julianday(started_at)
               ) * 86400
               WHERE id = ?""",
            (jobs_found, jobs_new, jobs_notified, errors, execution_id),
        )
        conn.commit()

    def update_website_status(
        self,
        source: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Update website status for circuit breaker."""
        conn = self.connect()
        now = "datetime('now')"
        if success:
            conn.execute(
                f"""INSERT INTO website_status
                    (source, last_checked, last_success, consecutive_failures, circuit_open)
                    VALUES (?, {now}, {now}, 0, 0)
                    ON CONFLICT(source) DO UPDATE SET
                        last_checked = {now},
                        last_success = {now},
                        last_error = NULL,
                        consecutive_failures = 0,
                        circuit_open = 0,
                        circuit_open_until = NULL""",
                (source,),
            )
        else:
            conn.execute(
                f"""INSERT INTO website_status
                    (source, last_checked, last_error, consecutive_failures)
                    VALUES (?, {now}, ?, 1)
                    ON CONFLICT(source) DO UPDATE SET
                        last_checked = {now},
                        last_error = ?,
                        consecutive_failures = consecutive_failures + 1""",
                (source, error, error),
            )
        conn.commit()

    def is_circuit_open(self, source: str) -> bool:
        """Check if circuit breaker is open for a source."""
        conn = self.connect()
        cursor = conn.execute(
            """SELECT circuit_open, circuit_open_until FROM website_status
               WHERE source = ?""",
            (source,),
        )
        row = cursor.fetchone()
        if not row:
            return False
        if row["circuit_open"] and row["circuit_open_until"]:
            from datetime import datetime, timezone

            until_str = row["circuit_open_until"]
            try:
                until = datetime.fromisoformat(until_str)
            except (ValueError, TypeError):
                return False
            # Make both naive or both aware for comparison
            now = datetime.now(timezone.utc) if until.tzinfo else datetime.now()
            if now < until:
                return True
            # Reset circuit
            conn.execute(
                "UPDATE website_status SET circuit_open = 0, circuit_open_until = NULL WHERE source = ?",
                (source,),
            )
            conn.commit()
        return False

    def open_circuit(self, source: str, timeout_seconds: int = 60) -> None:
        """Open circuit breaker for a source."""
        from datetime import datetime, timedelta, timezone

        until = (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()
        conn = self.connect()
        conn.execute(
            """INSERT INTO website_status (source, circuit_open, circuit_open_until)
               VALUES (?, 1, ?)
               ON CONFLICT(source) DO UPDATE SET
                   circuit_open = 1,
                   circuit_open_until = ?""",
            (source, until, until),
        )
        conn.commit()

    def get_stats(self) -> dict[str, int]:
        """Get quick stats about the database."""
        conn = self.connect()
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        notified = conn.execute("SELECT COUNT(*) FROM jobs WHERE notified = 1").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE notified = 0").fetchone()[0]
        executions = conn.execute("SELECT COUNT(*) FROM execution_history").fetchone()[0]
        return {
            "total_jobs": total,
            "notified_jobs": notified,
            "pending_jobs": pending,
            "total_executions": executions,
        }
