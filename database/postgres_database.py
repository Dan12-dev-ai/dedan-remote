"""
PostgreSQL database layer with async support.
Handles schema creation, job storage, deduplication, and execution history.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import asyncpg

from config.settings import get_settings
from models.job import Job


class PostgresDatabase:
    """Async PostgreSQL database manager."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self._host = host or settings.AEOS_POSTGRES_HOST
        self._port = port or settings.AEOS_POSTGRES_PORT
        self._user = user or settings.AEOS_POSTGRES_USER
        self._password = password or settings.AEOS_POSTGRES_PASSWORD
        self._database = database or "dedan_remote"  # Default database name
        self._pool: Optional[asyncpg.Pool] = None

    async def _ensure_pool(self) -> None:
        """Create connection pool if it doesn't exist."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
                min_size=1,
                max_size=10,
            )

    async def close(self) -> None:
        """Close the database connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[asyncpg.Connection, None]:
        """Create and return a connection from the pool."""
        await self._ensure_pool()
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        async with self._pool.acquire() as connection:
            yield connection

    async def create_tables(self) -> None:
        """Create all required tables if they don't exist."""
        async with self.connect() as conn:
            # Jobs table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    salary TEXT,
                    country TEXT,
                    remote BOOLEAN DEFAULT TRUE,
                    posted_date TIMESTAMP WITH TIME ZONE,
                    description TEXT,
                    tags TEXT[],
                    discovered_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    score REAL DEFAULT 0,
                    notified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)

            # Notifications table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    sent_at TIMESTAMP WITH TIME ZONE,
                    error TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                )
            """)

            # Execution history table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_history (
                    id SERIAL PRIMARY KEY,
                    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    completed_at TIMESTAMP WITH TIME ZONE,
                    status TEXT NOT NULL DEFAULT 'running',
                    jobs_found INTEGER DEFAULT 0,
                    jobs_new INTEGER DEFAULT 0,
                    jobs_notified INTEGER DEFAULT 0,
                    errors TEXT,
                    duration_seconds REAL
                )
            """)

            # Website status table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS website_status (
                    id SERIAL PRIMARY KEY,
                    source TEXT NOT NULL UNIQUE,
                    last_checked TIMESTAMP WITH TIME ZONE,
                    last_success TIMESTAMP WITH TIME ZONE,
                    last_error TEXT,
                    consecutive_failures INTEGER DEFAULT 0,
                    circuit_open BOOLEAN DEFAULT FALSE,
                    circuit_open_until TIMESTAMP WITH TIME ZONE
                )
            """)

            # Create indexes for better performance
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_jobs_discovered_at ON jobs(discovered_at DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_notifications_job_id ON notifications(job_id)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_website_status_source ON website_status(source)
            """)

    async def execute(self, query: str, *args) -> str:
        """Execute a query and return the status."""
        async with self.connect() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> list:
        """Fetch rows from a query."""
        async with self.connect() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> Optional[asyncpg.Record]:
        """Fetch a single row from a query."""
        async with self.connect() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args) -> Any:  # asyncpg value type depends on the query
        """Fetch a single value from a query."""
        async with self.connect() as conn:
            return await conn.fetchval(query, *args)

    def _job_from_record(self, record: asyncpg.Record) -> Job:
        """Convert a database record to a Job object."""
        return Job(
            title=record["title"],
            company=record["company"],
            url=record["url"],
            source=record["source"],
            salary=record["salary"],
            country=record["country"],
            remote=record["remote"],
            posted_date=record["posted_date"].isoformat() if record["posted_date"] else None,
            description=record["description"],
            tags=list(record["tags"]) if record["tags"] else [],
        )

    async def job_exists(self, job_id: str) -> bool:
        """Check if a job already exists in the database."""
        result = await self.fetchval("SELECT 1 FROM jobs WHERE id = $1", job_id)
        return result is not None

    async def insert_job(self, job: Job, score: float = 0.0) -> None:
        """Insert a job into the database."""
        await self.execute(
            """
            INSERT INTO jobs (
                id, title, company, url, source, salary, country, remote,
                posted_date, description, tags, discovered_at, score
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT (id) DO NOTHING
        """,
            job.id,
            job.title,
            job.company,
            job.url,
            job.source,
            job.salary,
            job.country,
            job.remote,
            job.posted_date,
            job.description,
            job.tags,
            job.discovered_at,
            score,
        )

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by its ID."""
        record = await self.fetchrow(
            """
            SELECT * FROM jobs WHERE id = $1
            """,
            job_id,
        )
        if record:
            return self._job_from_record(record)
        return None

    async def update_website_status(
        self, source: str, success: bool = True, error: Optional[str] = None
    ) -> None:
        """Update website status for circuit breaker functionality."""
        now = datetime.now(timezone.utc)

        if success:
            await self.execute(
                """
                INSERT INTO website_status (
                    source, last_checked, last_success, consecutive_failures, circuit_open
                ) VALUES ($1, $2, $3, 0, FALSE)
                ON CONFLICT (source) DO UPDATE SET
                    last_checked = EXCLUDED.last_checked,
                    last_success = EXCLUDED.last_success,
                    last_error = NULL,
                    consecutive_failures = 0,
                    circuit_open = FALSE,
                    circuit_open_until = NULL
            """,
                source,
                now,
                now,
            )
        else:
            await self.execute(
                """
                INSERT INTO website_status (
                    source, last_checked, last_error, consecutive_failures
                ) VALUES ($1, $2, $3, 1)
                ON CONFLICT (source) DO UPDATE SET
                    last_checked = EXCLUDED.last_checked,
                    last_error = EXCLUDED.last_error,
                    consecutive_failures = website_status.consecutive_failures + 1
            """,
                source,
                now,
                error or "Unknown error",
            )

    async def is_circuit_open(self, source: str) -> bool:
        """Check if circuit breaker is open for a source."""
        record = await self.fetchrow(
            """
            SELECT circuit_open, circuit_open_until FROM website_status
            WHERE source = $1
        """,
            source,
        )

        if not record:
            return False

        if record["circuit_open"] and record["circuit_open_until"]:
            until = record["circuit_open_until"]
            now = datetime.now(timezone.utc)
            # Make both naive or both aware for comparison
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if now < until:
                return True
            # Reset circuit if expired
            await self.execute(
                """
                UPDATE website_status SET circuit_open = FALSE, circuit_open_until = NULL
                WHERE source = $1
            """,
                source,
            )

        return False

    async def open_circuit(self, source: str, timeout_seconds: int = 60) -> None:
        """Open circuit breaker for a source."""
        from datetime import timedelta

        until = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        await self.execute(
            """
            INSERT INTO website_status (source, circuit_open, circuit_open_until)
            VALUES ($1, TRUE, $2)
            ON CONFLICT (source) DO UPDATE SET
                circuit_open = EXCLUDED.circuit_open,
                circuit_open_until = EXCLUDED.circuit_open_until
        """,
            source,
            until,
        )

    async def start_execution(self) -> int:
        """Start a new execution cycle and return its ID."""
        record = await self.fetchrow("""
            INSERT INTO execution_history (started_at, status)
            VALUES (NOW(), 'running')
            RETURNING id
        """)
        return record["id"]

    async def complete_execution(
        self,
        execution_id: int,
        jobs_found: int,
        jobs_new: int,
        jobs_notified: int,
        errors: Optional[str] = None,
    ) -> None:
        """Complete an execution cycle."""
        await self.execute(
            """
            UPDATE execution_history SET
                completed_at = NOW(),
                status = 'completed',
                jobs_found = $2,
                jobs_new = $3,
                jobs_notified = $4,
                errors = $5,
                duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at))
            WHERE id = $1
        """,
            execution_id,
            jobs_found,
            jobs_new,
            jobs_notified,
            errors,
        )

    async def get_stats(self) -> dict[str, int]:
        """Get quick stats about the database."""
        total = await self.fetchval("SELECT COUNT(*) FROM jobs")
        notified = await self.fetchval("SELECT COUNT(*) FROM jobs WHERE notified = TRUE")
        pending = await self.fetchval("SELECT COUNT(*) FROM jobs WHERE notified = FALSE")
        executions = await self.fetchval("SELECT COUNT(*) FROM execution_history")

        return {
            "total_jobs": total or 0,
            "notified_jobs": notified or 0,
            "pending_jobs": pending or 0,
            "total_executions": executions or 0,
        }

    async def get_recent_jobs(self, limit: int = 50) -> list[Job]:
        """Get recent jobs ordered by discovery date."""
        records = await self.fetch(
            """
            SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT $1
        """,
            limit,
        )
        return [self._job_from_record(record) for record in records]

    async def search_jobs(self, query: str, limit: int = 50) -> list[Job]:
        """Search jobs by title, company, or description."""
        search_pattern = f"%{query}%"
        records = await self.fetch(
            """
            SELECT * FROM jobs
            WHERE title ILIKE $1
               OR company ILIKE $1
               OR description ILIKE $1
            ORDER BY discovered_at DESC
            LIMIT $2
        """,
            search_pattern,
            limit,
        )
        return [self._job_from_record(record) for record in records]
