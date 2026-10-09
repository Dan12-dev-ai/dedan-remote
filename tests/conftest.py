"""
Pytest configuration and shared fixtures.

This module provides:
  1. Docker TestContainer fixtures (PostgreSQL, Redis) for true integration testing
  2. Async database and cache fixtures with proper lifecycle management
  3. Stateful memory isolation between test runs
  4. Factory fixtures for generating realistic test data (via Faker)
  5. Contextual fixtures for property-based testing
"""

from __future__ import annotations

# In-progress, not-yet-committed interview/provider subsystems. Their test
# files are untracked WIP and are excluded from the committed baseline so
# `pytest` / `hermes verify` reflect what is actually in version control.
# Remove entries here as each subsystem lands.
collect_ignore = [
    "test_interview_room.py",
    "test_mock_interview.py",
    "test_interview_api.py",
    "test_interview_report_pdf.py",
    "test_interview_client_contract.py",
    "test_provider_retry.py",
    "test_version_endpoint.py",
]

import asyncio
import os
import tempfile
from datetime import timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Generator

import pytest
import redis.asyncio as aioredis
from faker import Faker

try:
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer

    TESTCONTAINERS_AVAILABLE = True
except ImportError:
    TESTCONTAINERS_AVAILABLE = False
    PostgresContainer = None  # type: ignore
    RedisContainer = None  # type: ignore

from core.api_sentinel import APISentinel, IdempotencyKeyManager
from core.database_async import AsyncPostgresDB
from models.job import Job, job_from_scraper_result

# ── Hermetic discovery database ──────────────────────────────────────────────
#
# The API layer reads the engine's SQLite database through
# ``settings.DATABASE_PATH`` (default ``data/opportunities.db``). Point it at a
# throwaway file created with the engine's own schema so the suite never depends
# on a developer machine's ``data/`` directory — CI has no such directory, which
# produced "unable to open database file" and an HTTP 500 on every /api/jobs
# call.
#
# This must run before the first ``get_settings()`` call, i.e. before the API
# modules are imported during test collection.
# Each xdist worker gets a private file. A single shared database made every
# worker seed the same file at the same moment, so concurrent ``create_tables``
# and ``insert_job`` transactions collided and pytest reported
# "sqlite3.OperationalError: database is locked". Separate files remove the
# contention entirely; the serial (no-xdist) run keeps the original name.
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
TEST_JOBS_DB = Path(tempfile.gettempdir()) / (f"dedan_test_opportunities{_XDIST_WORKER or ''}.db")
os.environ["DATABASE_PATH"] = str(TEST_JOBS_DB)

# Every process now owns its file outright, so a stale database from a previous
# run can be dropped at import time without pulling it from under a peer.
TEST_JOBS_DB.unlink(missing_ok=True)

# Representative discovery rows. API tests assert on real records — card fields,
# detail lookup, save/apply by job id — so the throwaway DB must contain jobs.
TEST_JOBS: list[tuple[Job, float]] = [
    (
        Job(
            title="AI Data Annotator — Amharic",
            company="OneForma",
            url="https://www.oneforma.com/jobs/ai-data-annotator-amharic",
            source="oneforma",
            salary="$18/hr",
            country="Ethiopia",
            remote=True,
            posted_date="2026-09-01",
            description="Annotate Amharic text and audio for AI training datasets.",
            tags=["ai", "annotation", "amharic", "remote"],
        ),
        78.0,
    ),
    (
        Job(
            title="LLM Evaluation Specialist",
            company="Outlier",
            url="https://outlier.ai/expert-jobs/llm-evaluation-specialist",
            source="outlier",
            salary=None,
            country="Ethiopia",
            remote=True,
            posted_date="2026-09-10",
            description="Review and rank model responses for instruction-following quality.",
            tags=["ai", "llm", "evaluation"],
        ),
        71.5,
    ),
    (
        Job(
            title="AI Trainer — Amharic (Part-Time)",
            company="TELUS Digital",
            url="https://jobs.telusdigital.com/ai-trainer-amharic",
            source="telus",
            salary="$12/hr",
            country="Ethiopia",
            remote=False,
            posted_date="2026-09-15",
            description="Contribute Amharic language data for model improvement.",
            tags=["ai", "training", "amharic"],
        ),
        66.0,
    ),
]

# ── Scope & Markers ──────────────────────────────────────────────────────────


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring Docker containers (PostgreSQL/Redis)",
    )
    config.addinivalue_line(
        "markers",
        "security: mark test as security/penetration test",
    )
    config.addinivalue_line(
        "markers",
        "property_based: mark test as property-based (hypothesis)",
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow-running",
    )


# ── Environment Setup ─────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def setup_test_session() -> Generator[None, None, None]:
    """
    Session-scoped setup for entire test suite.

    Initializes:
      - Faker seed for reproducibility
      - Test environment variables
      - The throwaway discovery SQLite schema used by the read-only API layer
    """
    # Seed Faker for reproducible test data
    Faker.seed(42)

    # Create the engine's schema and seed representative jobs in this worker's
    # throwaway DB. Each worker owns its own file, so no two processes write
    # the same database at once.
    from database.database import Database

    db = Database(str(TEST_JOBS_DB))
    db.create_tables()
    for job, score in TEST_JOBS:
        db.insert_job(job, score)
    db.close()

    yield

    # No cleanup needed at session end


@pytest.fixture(autouse=True)
def set_test_env() -> Generator[None, None, None]:
    """
    Set up test environment variables (function-scoped, autouse).

    Isolates test environment from system environment.
    """
    old_env = dict(os.environ)
    os.environ["EMAIL"] = "test@example.com"
    os.environ["EMAIL_PASSWORD"] = "test_password"
    os.environ["SMTP_SERVER"] = "smtp.gmail.com"
    os.environ["SMTP_PORT"] = "587"
    os.environ["LOG_LEVEL"] = "DEBUG"
    os.environ["AEOS_TREASURY_LIMIT"] = "1000.00"
    os.environ["AEOS_RATE_LIMIT_WINDOW"] = "60"
    os.environ["AEOS_RATE_LIMIT_TOKENS"] = "100"

    yield

    # Restore environment
    os.environ.clear()
    os.environ.update(old_env)


# ── Docker Testcontainers (PostgreSQL & Redis) ──────────────────────────────


@pytest.fixture(scope="session")
def postgres_container() -> Generator[Any, None, None]:
    """
    Session-scoped PostgreSQL container fixture.

    Provides a live PostgreSQL database for all integration tests.
    Automatically started/stopped with container lifecycle.
    Skips if testcontainers/Docker not available.
    """
    if not TESTCONTAINERS_AVAILABLE:
        pytest.skip("testcontainers not installed")
    try:
        container = PostgresContainer(
            image="postgres:16-alpine",
            driver_kwargs={"user": "postgres", "password": "postgres"},
        )
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker not available: {exc}")
        return

    yield container

    try:
        container.stop()
    except Exception:
        pass


@pytest.fixture(scope="session")
def redis_container() -> Generator[Any, None, None]:
    """
    Session-scoped Redis container fixture.

    Provides a live Redis instance for cache layer integration tests.
    Skips if testcontainers/Docker not available.
    """
    if not TESTCONTAINERS_AVAILABLE:
        pytest.skip("testcontainers not installed")
    try:
        container = RedisContainer(image="redis:7-alpine")
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker not available: {exc}")
        return

    yield container

    try:
        container.stop()
    except Exception:
        pass


# ── Async Database Fixtures ──────────────────────────────────────────────────


@pytest.fixture
async def postgres_pool(
    postgres_container: PostgresContainer,
) -> AsyncGenerator[AsyncPostgresDB, None]:
    """
    Function-scoped PostgreSQL connection pool fixture.

    Returns AsyncPostgresDB instance connected to the test container.
    Creates schema and ensures clean state for each test.
    """
    # Extract connection details from container
    url = postgres_container.get_connection_url().replace("psycopg2://", "postgresql://")

    db = AsyncPostgresDB(dsn=url)
    await db.connect()

    # Initialize schema
    await db.create_schema()

    yield db

    # Cleanup
    await db.close()


@pytest.fixture
async def redis_client(redis_container: RedisContainer) -> AsyncGenerator[aioredis.Redis, None]:
    """
    Function-scoped Redis async client fixture.

    Provides clean Redis connection with automatic flush before/after test.
    """
    # Connect to container
    redis = await aioredis.from_url(
        f"redis://{redis_container.get_container_host_ip()}:{redis_container.get_exposed_port(6379)}",
        decode_responses=True,
    )

    # Flush to ensure clean state
    await redis.flushall()

    yield redis

    # Cleanup
    await redis.flushall()
    await redis.close()


# ── Legacy SQLite Database Fixtures (for backward compatibility) ─────────────


@pytest.fixture
def temp_db() -> Generator:
    """
    Function-scoped temporary SQLite database (legacy support).

    DEPRECATED: Prefer postgres_pool for new tests.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    from database.database import Database

    db = Database(db_path)
    db.create_tables()

    yield db

    db.close()
    Path(db_path).unlink(missing_ok=True)


# ── Data Factory Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def faker_instance() -> Faker:
    """
    Faker instance for generating realistic test data.

    Uses fixed seed for reproducibility.
    """
    fake = Faker()
    Faker.seed(42)
    return fake


@pytest.fixture
def sample_job() -> Job:
    """Create a single sample job for testing."""
    return job_from_scraper_result(
        title="AI Training Specialist",
        company="TestCompany",
        url="https://example.com/job/123",
        source="test_source",
        salary="$50-80/hr",
        country="Worldwide",
        remote=True,
        posted_date="2026-06-29",
        description="Great AI training opportunity for beginners.",
        tags=["ai", "training", "remote", "beginner"],
    )


@pytest.fixture
def sample_jobs() -> list[Job]:
    """Create multiple sample jobs for testing."""
    return [
        job_from_scraper_result(
            title=f"AI Job {i}",
            company="TestCorp",
            url=f"https://example.com/job/{i}",
            source="test_source",
            remote=True,
            tags=["ai"],
        )
        for i in range(5)
    ]


@pytest.fixture
def fake_jobs(faker_instance: Faker) -> list[Job]:
    """
    Generate realistic fake jobs using Faker.

    Returns 10 diverse job entries with randomized properties.
    """
    jobs = []
    for i in range(10):
        jobs.append(
            job_from_scraper_result(
                title=faker_instance.job(),
                company=faker_instance.company(),
                url=f"https://{faker_instance.domain_name()}/job/{i}",
                source=faker_instance.random_element(
                    ["test_source", "appen", "clickworker", "outlier"]
                ),
                salary=f"${faker_instance.random_int(min=20, max=150)}/hr"
                if faker_instance.boolean()
                else None,
                country=faker_instance.country(),
                remote=faker_instance.boolean(),
                posted_date=faker_instance.date_time(tzinfo=timezone.utc).isoformat(),
                description=faker_instance.text(max_nb_chars=200),
                tags=[
                    faker_instance.random_element(
                        ["ai", "training", "beginner", "advanced", "remote"]
                    )
                ],
            )
        )
    return jobs


# ── API Sentinel Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def idempotency_manager() -> IdempotencyKeyManager:
    """
    IdempotencyKeyManager instance for security testing.

    Uses a fixed test secret.
    """
    return IdempotencyKeyManager(secret="test-secret-key-for-testing")


@pytest.fixture
def api_sentinel(postgres_pool) -> APISentinel:
    """
    APISentinel instance for API gateway hardening tests.

    Configured with test database connection.
    """
    return APISentinel(
        postgres_db=postgres_pool,
        idempotency_secret="test-secret-key-for-testing",
    )


# ── Async Helper Utilities ───────────────────────────────────────────────────


@pytest.fixture
def event_loop() -> Generator:
    """
    Provide event loop for async tests.

    Ensures proper lifecycle management of async fixtures.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ── Property-Based Testing Context ──────────────────────────────────────────


@pytest.fixture
def hypothesis_settings():
    """
    Configure hypothesis for property-based testing.

    Yields explicit settings that balance thoroughness and speed.
    """
    from hypothesis import HealthCheck, settings

    return settings(
        max_examples=1000,  # Increased from default 100
        deadline=5000,  # 5-second timeout per test
        suppress_health_check=[HealthCheck.too_slow],
    )


# ── Financial Ledger Testing Fixtures ───────────────────────────────────────


@pytest.fixture
def mock_financial_state() -> dict:
    """
    Create initial mock financial ledger state.

    Used for stateful property-based tests and financial invariant checking.
    """
    return {
        "verified_revenue": 0.0,
        "verified_costs": 0.0,
        "verified_net_profit": 0.0,
        "pending_transactions": [],
        "completed_transactions": [],
        "failed_transactions": [],
        "treasury_balance": 1000.0,
        "execution_count": 0,
    }


# ── Performance Benchmarking Fixtures ────────────────────────────────────────


@pytest.fixture
def benchmark_context():
    """
    Provide timing context for performance assertions.

    Useful for benchmarking security checks and query times.
    """

    class BenchmarkContext:
        def __init__(self):
            self.measurements = {}

        def time_operation(self, name: str, operation_time: float):
            """Record an operation's execution time."""
            if name not in self.measurements:
                self.measurements[name] = []
            self.measurements[name].append(operation_time)

        def get_stats(self, name: str) -> dict:
            """Get stats for a named operation."""
            if name not in self.measurements:
                return {}
            times = self.measurements[name]
            return {
                "count": len(times),
                "min": min(times),
                "max": max(times),
                "avg": sum(times) / len(times),
            }

    return BenchmarkContext()


# ── Security Testing Utilities ───────────────────────────────────────────────


@pytest.fixture
def malicious_payloads() -> dict[str, str]:
    """
    Curated collection of malicious payloads for security penetration testing.

    Covers OWASP top 10 attack vectors.
    """
    return {
        # SQL Injection
        "sql_basic": "'; DROP TABLE users; --",
        "sql_union": "' UNION SELECT * FROM users --",
        "sql_time_based": "'; WAITFOR DELAY '00:00:05'; --",
        # SSRF/Loopback
        "ssrf_localhost": "http://localhost:5432/admin",
        "ssrf_internal": "http://169.254.169.254/latest/meta-data/",
        "ssrf_file": "file:///etc/passwd",
        # XSS Payloads
        "xss_script": "<script>alert('xss')</script>",
        "xss_img": "<img src=x onerror=alert('xss')>",
        "xss_event": "<body onload=alert('xss')>",
        # Command Injection
        "cmd_basic": "; rm -rf /",
        "cmd_backtick": "test`whoami`",
        "cmd_dollar": "test$(id)",
        # HMAC/Signature Bypass
        "hmac_empty": "",
        "hmac_invalid": "invalid_signature_string",
        "hmac_replay": "same_key_used_twice",
    }
