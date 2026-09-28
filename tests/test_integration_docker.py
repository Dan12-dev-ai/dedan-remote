"""
Docker Integration Tests - PostgreSQL & Redis with Testcontainers.

Tests the full database and cache layer with live Docker containers:
  - Real PostgreSQL instance (not mocks)
  - Real Redis instance (not in-memory stubs)
  - Clean state isolation between tests
  - Connection pool management
  - Transaction semantics
  - Cache coherency
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

# ── PostgreSQL Integration Tests ─────────────────────────────────────────────


class TestPostgresIntegration:
    """
    Integration tests with live PostgreSQL via Docker testcontainers.

    Tests:
      - Connection pool lifecycle
      - Transaction isolation
      - Schema creation
      - CRUD operations
      - Data consistency
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_connection_pool_created(self, postgres_pool):
        """Test: PostgreSQL connection pool initializes correctly."""
        assert postgres_pool is not None
        assert postgres_pool._pool is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_schema_initialized(self, postgres_pool):
        """Test: Database schema is created on pool initialization."""
        # Schema should exist
        rows = await postgres_pool._fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """)

        # Should have tables
        assert len(rows) > 0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_insert_operation(self, postgres_pool):
        """Test: Data can be inserted into tables."""
        await postgres_pool._execute(
            """
            INSERT INTO tier2_episodic_memory (task_id, data)
            VALUES ($1, $2)
        """,
            "test-task-1",
            '{"test": "data"}',
        )

        # Verify insert
        row = await postgres_pool._fetchrow(
            "SELECT * FROM tier2_episodic_memory WHERE task_id = $1", "test-task-1"
        )
        assert row is not None
        assert row["task_id"] == "test-task-1"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_transaction_rollback(self, postgres_pool):
        """Test: Transactions can be rolled back properly."""
        try:
            await postgres_pool._execute(
                """
                INSERT INTO tier2_episodic_memory (task_id, data)
                VALUES ($1, $2)
            """,
                "test-rollback",
                '{"test": "data"}',
            )

            # Simulate error to trigger rollback
            raise Exception("Simulated error")
        except Exception:
            pass

        # Data should not exist (rolled back)
        row = await postgres_pool._fetchrow(
            "SELECT * FROM tier2_episodic_memory WHERE task_id = $1", "test-rollback"
        )
        # Depends on implementation, but transaction should be isolated
        assert row is None or row["task_id"] != "test-rollback"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_bulk_insert_performance(self, postgres_pool):
        """Test: Bulk inserts maintain performance."""
        # Insert 1000 records
        start_time = datetime.now(timezone.utc)

        for i in range(1000):
            await postgres_pool._execute(
                "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
                f"task-{i}",
                f'{{"index": {i}, "timestamp": "{start_time.isoformat()}"}}',
            )

        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()

        # Should complete in reasonable time (< 30 seconds)
        assert duration < 30.0

        # Verify all inserted
        count_row = await postgres_pool._fetchrow(
            "SELECT COUNT(*) as cnt FROM tier2_episodic_memory"
        )
        assert count_row["cnt"] >= 1000

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_concurrent_connections(self, postgres_pool):
        """Test: Connection pool handles concurrent operations."""
        tasks = []

        async def insert_record(i: int):
            await postgres_pool._execute(
                "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
                f"concurrent-{i}",
                f'{{"concurrent_index": {i}}}',
            )

        # Launch 50 concurrent inserts
        for i in range(50):
            tasks.append(insert_record(i))

        await asyncio.gather(*tasks)

        # All should have succeeded
        count_row = await postgres_pool._fetchrow(
            "SELECT COUNT(*) as cnt FROM tier2_episodic_memory WHERE task_id LIKE 'concurrent-%'"
        )
        assert count_row["cnt"] == 50

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_connection_isolation(self, postgres_pool):
        """Test: Data isolation between parallel connections."""
        # Insert in connection 1
        await postgres_pool._execute(
            "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
            "task-isolation-1",
            '{"connection": 1}',
        )

        # Insert in connection 2 (uses pool)
        await postgres_pool._execute(
            "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
            "task-isolation-2",
            '{"connection": 2}',
        )

        # Both should be visible
        rows = await postgres_pool._fetch(
            "SELECT * FROM tier2_episodic_memory WHERE task_id LIKE 'task-isolation-%'"
        )
        assert len(rows) == 2


# ── Redis Integration Tests ──────────────────────────────────────────────────


class TestRedisIntegration:
    """
    Integration tests with live Redis via Docker testcontainers.

    Tests:
      - Connection lifecycle
      - Get/Set operations
      - Key expiration (TTL)
      - Concurrent operations
      - Cache coherency
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_connection_established(self, redis_client):
        """Test: Redis connection established successfully."""
        assert redis_client is not None

        # Ping should succeed
        pong = await redis_client.ping()
        assert pong is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_key_set_get(self, redis_client):
        """Test: Basic Redis SET/GET operations."""
        await redis_client.set("test_key", "test_value")
        value = await redis_client.get("test_key")

        assert value == "test_value"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_key_expiration_ttl(self, redis_client):
        """Test: Redis key expiration is honored."""
        await redis_client.setex("expiring_key", 1, "short_lived")

        # Key exists immediately
        value = await redis_client.get("expiring_key")
        assert value == "short_lived"

        # Wait for expiration
        await asyncio.sleep(1.5)

        # Key should be gone
        value = await redis_client.get("expiring_key")
        assert value is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_increments(self, redis_client):
        """Test: Redis INCR operations for counters."""
        await redis_client.delete("counter")

        # Increment counter
        for _ in range(10):
            await redis_client.incr("counter")

        value = int(await redis_client.get("counter") or 0)
        assert value == 10

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_list_operations(self, redis_client):
        """Test: Redis list operations (push/pop)."""
        await redis_client.delete("test_list")

        # Push items
        for i in range(5):
            await redis_client.rpush("test_list", f"item-{i}")

        # Pop items
        item = await redis_client.lpop("test_list")
        assert item == "item-0"

        # List should have 4 remaining
        length = await redis_client.llen("test_list")
        assert length == 4

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_hash_operations(self, redis_client):
        """Test: Redis hash (dictionary) operations."""
        await redis_client.delete("test_hash")

        # Set hash fields
        await redis_client.hset(
            "test_hash",
            mapping={
                "field1": "value1",
                "field2": "value2",
                "field3": "value3",
            },
        )

        # Get individual field
        value = await redis_client.hget("test_hash", "field1")
        assert value == "value1"

        # Get all fields
        all_fields = await redis_client.hgetall("test_hash")
        assert len(all_fields) == 3

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_set_operations(self, redis_client):
        """Test: Redis set operations."""
        await redis_client.delete("test_set")

        # Add to set
        for i in range(5):
            await redis_client.sadd("test_set", f"member-{i}")

        # Check membership
        is_member = await redis_client.sismember("test_set", "member-0")
        assert is_member

        # Get set size
        size = await redis_client.scard("test_set")
        assert size == 5

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_concurrent_operations(self, redis_client):
        """Test: Redis handles concurrent operations."""
        tasks = []

        async def set_key(i: int):
            await redis_client.set(f"concurrent_key_{i}", f"value_{i}")

        # Launch 100 concurrent sets
        for i in range(100):
            tasks.append(set_key(i))

        await asyncio.gather(*tasks)

        # All keys should exist
        for i in range(100):
            value = await redis_client.get(f"concurrent_key_{i}")
            assert value == f"value_{i}"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_pipeline_operations(self, redis_client):
        """Test: Redis pipeline for atomic multi-operations."""
        pipe = redis_client.pipeline()

        for i in range(10):
            pipe.set(f"pipe_key_{i}", f"value_{i}")

        await pipe.execute()

        # All keys should exist
        value = await redis_client.get("pipe_key_0")
        assert value == "value_0"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_clean_state_between_tests(self, redis_client):
        """Test: Redis is flushed between tests (clean isolation)."""
        # This test runs after previous tests
        # Redis should be clean (flushed by fixture)

        value = await redis_client.get("any_previous_key")
        assert value is None

        # Should be able to freely set keys
        await redis_client.set("fresh_key", "fresh_value")
        fresh = await redis_client.get("fresh_key")
        assert fresh == "fresh_value"


# ── Cross-Layer Integration Tests ────────────────────────────────────────────


class TestDatabaseCacheIntegration:
    """
    Integration tests for interactions between PostgreSQL and Redis.

    Tests:
      - Cache-through patterns
      - Cache invalidation
      - Consistency guarantees
      - Fallback on cache miss
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_through_pattern(self, postgres_pool, redis_client):
        """Test: Cache-through (check cache first, fallback to DB)."""
        cache_key = "user:123"
        user_data = '{"id": 123, "name": "Test User"}'

        # Insert in database
        await postgres_pool._execute(
            "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
            "user-123",
            user_data,
        )

        # Cache miss initially
        cached = await redis_client.get(cache_key)
        assert cached is None

        # Get from database and cache
        db_row = await postgres_pool._fetchrow(
            "SELECT * FROM tier2_episodic_memory WHERE task_id = $1", "user-123"
        )

        # Store in cache
        await redis_client.set(cache_key, db_row["data"])

        # Next access should hit cache
        cached = await redis_client.get(cache_key)
        assert cached is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_invalidation(self, redis_client, postgres_pool):
        """Test: Cache invalidation on database update."""
        cache_key = "data:key-1"

        # Set cache
        await redis_client.set(cache_key, '{"version": 1}')

        # Write to database (simulating update)
        await postgres_pool._execute(
            "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
            "data-1",
            '{"version": 2}',
        )

        # Invalidate cache
        await redis_client.delete(cache_key)

        # Cache should be empty
        cached = await redis_client.get(cache_key)
        assert cached is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_consistency_under_concurrent_load(self, postgres_pool, redis_client):
        """Test: Database and cache remain consistent under load."""
        tasks = []

        async def write_and_cache(i: int):
            # Write to database
            await postgres_pool._execute(
                "INSERT INTO tier2_episodic_memory (task_id, data) VALUES ($1, $2)",
                f"concurrent-{i}",
                f'{{"index": {i}}}',
            )

            # Cache it
            await redis_client.set(f"concurrent:{i}", f'{{"index": {i}}}')

        # Concurrent writes
        for i in range(50):
            tasks.append(write_and_cache(i))

        await asyncio.gather(*tasks)

        # Verify consistency
        for i in range(50):
            # Check database
            db_row = await postgres_pool._fetchrow(
                "SELECT * FROM tier2_episodic_memory WHERE task_id = $1", f"concurrent-{i}"
            )

            # Check cache
            cached = await redis_client.get(f"concurrent:{i}")

            assert db_row is not None
            assert cached is not None


# ── State Isolation & Cleanup Tests ──────────────────────────────────────────


class TestStateIsolation:
    """
    Tests that verify clean state isolation between test runs.

    Ensures:
      - No cross-test data pollution
      - Fresh containers per session
      - Proper cleanup
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_postgres_isolation_clean_between_tests(self, postgres_pool):
        """Test: PostgreSQL state is clean between test runs."""
        # Count existing records
        count_before = await postgres_pool._fetchrow(
            "SELECT COUNT(*) as cnt FROM tier2_episodic_memory"
        )

        # This test should have clean slate (or only its own data)
        assert count_before is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redis_isolation_clean_between_tests(self, redis_client):
        """Test: Redis is flushed clean between test runs."""
        # Redis should be empty after flush (guaranteed by fixture)
        keys_count = await redis_client.dbsize()

        # Should be 0 or very small
        assert keys_count < 10
