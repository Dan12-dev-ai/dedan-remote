"""
Tests for the async PostgreSQL database layer.

These are unit tests: connections are mocked, no live PostgreSQL required.
The import itself is a regression guard for a historical syntax error that
rendered the whole module unloadable.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.postgres_database import PostgresDatabase
from models.job import Job


EXPECTED_METHODS = [
    "__init__", "_ensure_pool", "close", "connect", "create_tables",
    "execute", "fetch", "fetchrow", "fetchval", "_job_from_record",
    "job_exists", "insert_job", "get_job", "update_website_status",
    "is_circuit_open", "open_circuit", "start_execution",
    "complete_execution", "get_stats", "get_recent_jobs", "search_jobs",
]


class TestPostgresDatabaseModule:
    """Module import + class shape regression guards."""

    def test_module_imports_cleanly(self) -> None:
        """Regression: this module once had a SyntaxError at line 215."""
        import database.postgres_database as mod
        assert hasattr(mod, "PostgresDatabase")

    @pytest.mark.parametrize("method", EXPECTED_METHODS)
    def test_class_exposes_method(self, method: str) -> None:
        """Every helper must belong to the PostgresDatabase class."""
        assert hasattr(PostgresDatabase, method), (
            f"PostgresDatabase.{method} is missing — check indentation/def"
        )

    def test_instance_initialises_without_connection(self) -> None:
        db = PostgresDatabase(host="localhost", port=5432, user="u",
                               password="p", database="d")
        assert db._pool is None
        assert db._host == "localhost"
        assert db._database == "d"


class TestJobRecordConversion:
    """_job_from_record maps rows to Job objects faithfully."""

    def setup_method(self) -> None:
        self.db = PostgresDatabase(host="localhost", port=5432, user="u",
                                   password="p", database="d")

    @staticmethod
    def make_record(**overrides: object) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        record: dict[str, object] = {
            "title": "AI Trainer",
            "company": "TestCorp",
            "url": "https://example.com/job/1",
            "source": "outlier",
            "salary": "$50/hr",
            "country": None,
            "remote": True,
            "posted_date": now,
            "description": "Train models",
            "tags": ["ai", "training"],
        }
        record.update(overrides)
        return record

    def test_converts_full_record(self) -> None:
        job = self.db._job_from_record(self.make_record())  # type: ignore[arg-type]
        assert isinstance(job, Job)
        assert job.title == "AI Trainer"
        assert job.company == "TestCorp"
        assert job.source == "outlier"
        assert job.salary == "$50/hr"
        assert job.remote is True
        assert job.tags == ["ai", "training"]
        assert job.posted_date is not None  # ISO-formatted string

    def test_handles_null_optionals(self) -> None:
        job = self.db._job_from_record(  # type: ignore[arg-type]
            self.make_record(salary=None, country=None,
                             posted_date=None, description=None, tags=[]),
        )
        assert job.salary is None
        assert job.country is None
        assert job.posted_date is None
        assert job.description is None
        assert job.tags == []

    def test_roundtrips_through_job_serialisation(self) -> None:
        job = self.db._job_from_record(self.make_record())  # type: ignore[arg-type]
        clone = Job.from_dict(job.to_dict())
        assert clone.title == job.title
        assert clone.id == job.id


class TestQueryHelpers:
    """execute/fetch/fetchrow/fetchval delegate through the pool."""

    def setup_method(self) -> None:
        self.db = PostgresDatabase(host="localhost", port=5432, user="u",
                                   password="p", database="d")

    async def test_fetchval_delegates_to_connection(self) -> None:
        calls: list[tuple[str, tuple]] = []

        class FakeConn:
            async def fetchval(self, query: str, *args: object) -> object:
                calls.append((query, args))
                return 7

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_connect():
            yield FakeConn()

        self.db.connect = fake_connect  # type: ignore[assignment,method-assign]
        result = await self.db.fetchval("SELECT COUNT(*) FROM jobs")
        assert result == 7
        assert calls and calls[0][0] == "SELECT COUNT(*) FROM jobs"

    async def test_get_job_returns_none_when_missing(self) -> None:
        async def fake_fetchrow(query: str, *args: object) -> None:
            return None

        self.db.fetchrow = fake_fetchrow  # type: ignore[method-assign]
        assert await self.db.get_job("nope") is None

    async def test_get_job_returns_job_when_found(self) -> None:
        record = TestJobRecordConversion.make_record()

        async def fake_fetchrow(query: str, *args: object) -> object:
            return record

        self.db.fetchrow = fake_fetchrow  # type: ignore[method-assign]
        job = await self.db.get_job("abc")
        assert job is not None
        assert job.title == "AI Trainer"

    async def test_job_exists_true_and_false(self) -> None:
        async def make_fetchval(value: object):
            async def fake(query: str, *args: object) -> object:
                return value
            return fake

        self.db.fetchval = await make_fetchval(1)  # type: ignore[method-assign]
        assert await self.db.job_exists("x") is True

        self.db.fetchval = await make_fetchval(None)  # type: ignore[method-assign]
        assert await self.db.job_exists("x") is False
