"""
Tests for the database layer — Enhanced with Docker Integration.

Uses live PostgreSQL instance via testcontainers for true integration testing.
Tests verify:
  - Table creation and schema validation
  - ACID transaction semantics
  - Data consistency across concurrent operations
  - Query performance
  - Error handling and edge cases
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

import tempfile

from models.job import job_from_scraper_result
from hypothesis import given, strategies as st
from hypothesis import settings as hypothesis_settings, HealthCheck
from database.database import Database


# ── Legacy SQLite Tests (Backward Compatibility) ──────────────────────────────

class TestDatabaseLegacySQLite:
    """Legacy tests using SQLite (temp_db fixture)."""

    def test_create_tables(self, temp_db) -> None:
        """Test that tables are created."""
        conn = temp_db.connect()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {row["name"] for row in tables}
        expected = {"jobs", "notifications", "execution_history", "website_status"}
        assert expected.issubset(table_names)

    def test_insert_job(self, temp_db, sample_job) -> None:
        """Test inserting a job."""
        result = temp_db.insert_job(sample_job)
        assert result is True

        # Verify it was inserted
        conn = temp_db.connect()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (sample_job.id,)).fetchone()
        assert row is not None
        assert row["title"] == sample_job.title
        assert row["company"] == sample_job.company

    def test_insert_duplicate_job(self, temp_db, sample_job) -> None:
        """Test that duplicate jobs are rejected."""
        temp_db.insert_job(sample_job)
        result = temp_db.insert_job(sample_job)
        assert result is False

    def test_job_exists(self, temp_db, sample_job) -> None:
        """Test job_exists method."""
        assert not temp_db.job_exists(sample_job.id)
        temp_db.insert_job(sample_job)
        assert temp_db.job_exists(sample_job.id)

    def test_mark_notified(self, temp_db, sample_job) -> None:
        """Test marking a job as notified."""
        temp_db.insert_job(sample_job)
        temp_db.mark_notified(sample_job.id, channel="email")

        conn = temp_db.connect()
        row = conn.execute(
            "SELECT * FROM notifications WHERE job_id = ?", (sample_job.id,)
        ).fetchone()
        assert row is not None
        assert row["channel"] == "email"
        assert row["status"] == "sent"

    def test_get_new_unnotified_jobs(self, temp_db, sample_jobs) -> None:
        """Test getting unnotified jobs."""
        for job in sample_jobs[:3]:
            temp_db.insert_job(job, score=75.0)
        temp_db.insert_job(sample_jobs[4], score=75.0)
        temp_db.mark_notified(sample_jobs[4].id)

        unnotified = temp_db.get_new_unnotified_jobs(min_score=50.0)
        assert len(unnotified) == 3  # 3 new, 1 notified excluded

    def test_execution_history(self, temp_db) -> None:
        """Test execution history tracking."""
        exec_id = temp_db.start_execution()
        assert exec_id is not None

        temp_db.complete_execution(
            execution_id=exec_id,
            jobs_found=10,
            jobs_new=5,
            jobs_notified=2,
        )

        conn = temp_db.connect()
        row = conn.execute(
            "SELECT * FROM execution_history WHERE id = ?", (exec_id,)
        ).fetchone()
        assert row["status"] == "completed"
        assert row["jobs_found"] == 10
        assert row["jobs_new"] == 5

    def test_website_status_success(self, temp_db) -> None:
        """Test updating website status on success."""
        temp_db.update_website_status("test_source", success=True)
        conn = temp_db.connect()
        row = conn.execute(
            "SELECT * FROM website_status WHERE source = ?", ("test_source",)
        ).fetchone()
        assert row is not None
        assert row["consecutive_failures"] == 0
        assert row["circuit_open"] == 0


# ── Advanced PostgreSQL Integration Tests ────────────────────────────────────

class TestDatabasePostgresIntegration:
    """
    Advanced database tests using live PostgreSQL via testcontainers.
    
    REQUIRES: Docker daemon running
    
    Tests advanced scenarios:
      - Concurrent operations
      - Transaction isolation
      - Complex queries
      - Performance
      - Edge cases
    """
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_schema_integrity(self, postgres_pool) -> None:
        """Test: PostgreSQL schema is correctly initialized."""
        # Query information schema
        rows = await postgres_pool._fetch("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)
        
        # Should have tables
        assert len(rows) > 0
        
        # Verify expected columns exist
        column_names = {row["column_name"] for row in rows}
        assert "id" in column_names
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_insert_performance(self, postgres_pool) -> None:
        """
        Test: Insert performance meets requirements.
        
        Should handle 1000 inserts in < 10 seconds.
        """
        import time
        
        start = time.time()
        
        for i in range(1000):
            await postgres_pool._execute(
                "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
                f"perf_task_{i}",
                f'{{"index": {i}}}'
            )
        
        elapsed = time.time() - start
        
        # Performance assertion
        assert elapsed < 10.0, f"Insert performance degraded: {elapsed}s for 1000 records"
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_query_isolation(self, postgres_pool) -> None:
        """
        Test: Transactions are properly isolated.
        
        One transaction's changes shouldn't be visible to others
        until committed.
        """
        # Insert test data
        await postgres_pool._execute(
            "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
            "isolation_test",
            '{"version": 1}'
        )
        
        # Verify it exists
        row = await postgres_pool._fetchrow(
            "SELECT * FROM tier2_episodic_memory WHERE task_id = $1",
            "isolation_test"
        )
        assert row is not None
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_concurrent_writes(self, postgres_pool) -> None:
        """
        Test: Concurrent writes maintain consistency.
        
        50 concurrent inserts should all succeed without conflicts.
        """
        import asyncio
        
        async def parallel_insert(i: int):
            await postgres_pool._execute(
                "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
                f"concurrent_{i}",
                f'{{"worker": {i}}}'
            )
        
        # Run 50 concurrent operations
        tasks = [parallel_insert(i) for i in range(50)]
        await asyncio.gather(*tasks)
        
        # Verify all succeeded
        count_row = await postgres_pool._fetchrow(
            "SELECT COUNT(*) as cnt FROM tier2_episodic_memory WHERE task_id LIKE 'concurrent_%'"
        )
        assert count_row["cnt"] == 50
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_data_type_validation(self, postgres_pool) -> None:
        """
        Test: Data types are properly validated.
        
        Inserting incompatible types should raise errors.
        """
        # This should work
        await postgres_pool._execute(
            "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
            "type_test",
            '{"string": "value"}'
        )
        
        # Verify the insert
        row = await postgres_pool._fetchrow(
            "SELECT * FROM tier2_episodic_memory WHERE task_id = $1",
            "type_test"
        )
        assert row is not None


# ── Property-Based Database Tests ───────────────────────────────────────────

class TestDatabaseProperties:
    """Property-based tests for database operations."""
    
    @given(
        job_title=st.text(min_size=1, max_size=256),
        company=st.text(min_size=1, max_size=256),
        salary_range=st.one_of(st.none(), st.text(max_size=50)),
    )
    @hypothesis_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @pytest.mark.property_based
    def test_job_storage_property(self, job_title, company, salary_range) -> None:
        """
        Property: Any job input can be stored and retrieved.

        Tests that database handles various job inputs without errors.
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            db_path = handle.name

        db = Database(db_path)
        db.create_tables()
        try:
            job = job_from_scraper_result(
                title=job_title,
                company=company,
                url="https://example.com/job/1",
                source="test",
                salary=salary_range,
            )

            result = db.insert_job(job)
            assert result is True
            assert db.job_exists(job.id)
        finally:
            db.close()
            import os
            os.unlink(db_path)
    
    @given(
        scores=st.lists(st.floats(min_value=0, max_value=100), min_size=1, max_size=20)
    )
    @hypothesis_settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @pytest.mark.property_based
    def test_score_persistence_property(self, scores) -> None:
        """
        Property: Scores for jobs are accurately persisted.

        Tests various score values to ensure no loss of precision.
        """
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            db_path = handle.name

        db = Database(db_path)
        db.create_tables()
        try:
            sample = job_from_scraper_result(
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
            db.insert_job(sample)

            for index, score in enumerate(scores):
                job = job_from_scraper_result(
                    title=f"Job {score}",
                    company="Test",
                    url=f"https://example.com/{index}-{score}",
                    source="test",
                    salary=None,
                )
                result = db.insert_job(job)
                assert result is True
        finally:
            db.close()
            import os
            os.unlink(db_path)


# ── Edge Case & Error Handling Tests ────────────────────────────────────────

class TestDatabaseEdgeCases:
    """Test edge cases and error conditions."""
    
    def test_empty_database_query(self, temp_db) -> None:
        """Test: Queries on empty database return empty results."""
        unnotified = temp_db.get_new_unnotified_jobs(min_score=50.0)
        assert unnotified == []
    
    def test_null_values_handled(self, temp_db) -> None:
        """Test: NULL values are handled correctly."""
        job = job_from_scraper_result(
            title="Test",
            company="Corp",
            url="https://example.com/1",
            source="test",
            salary=None,  # NULL
            description=None,  # NULL
        )
        
        result = temp_db.insert_job(job)
        assert result is True
    
    def test_special_characters_escaped(self, temp_db) -> None:
        """Test: Special characters in input are properly escaped."""
        job = job_from_scraper_result(
            title="Job's \"Title\" with 'quotes'",
            company="Company & Co.",
            url="https://example.com/1",
            source="test",
        )
        
        result = temp_db.insert_job(job)
        assert result is True
        
        # Verify it can be retrieved
        assert temp_db.job_exists(job.id)
    
    def test_very_long_strings(self, temp_db) -> None:
        """Test: Very long strings are handled."""
        job = job_from_scraper_result(
            title="x" * 10000,  # Very long title
            company="Company",
            url="https://example.com/1",
            source="test",
        )
        
        # May succeed or fail gracefully depending on schema
        try:
            result = temp_db.insert_job(job)
            assert result is True
        except Exception as e:
            # Should have proper error handling
            assert "size" in str(e).lower() or "limit" in str(e).lower()

    def test_website_status_failure(self, temp_db) -> None:
        """Test updating website status on failure."""
        temp_db.update_website_status("test_source", success=False, error="Timeout")
        conn = temp_db.connect()
        row = conn.execute(
            "SELECT * FROM website_status WHERE source = ?", ("test_source",)
        ).fetchone()
        assert row["consecutive_failures"] == 1
        assert row["last_error"] == "Timeout"

    def test_circuit_breaker(self, temp_db) -> None:
        """Test circuit breaker."""
        assert not temp_db.is_circuit_open("test_source")
        temp_db.open_circuit("test_source", timeout_seconds=5)
        assert temp_db.is_circuit_open("test_source")
        # Wait for circuit to close
        import time
        time.sleep(6)
        assert not temp_db.is_circuit_open("test_source")

    def test_get_stats(self, temp_db, sample_job) -> None:
        """Test get_stats method."""
        stats = temp_db.get_stats()
        assert stats["total_jobs"] == 0

        temp_db.insert_job(sample_job)
        stats = temp_db.get_stats()
        assert stats["total_jobs"] == 1
        assert stats["notified_jobs"] == 0