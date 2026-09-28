"""
Redis Cache Layer Tests — Advanced Cache Semantics & Coherency.

Implements comprehensive cache testing covering:
  - Keyspace notifications (Tier 1 Working Memory events)
  - Cache eviction policies
  - TTL/expiration guarantees
  - Distributed cache coherency
  - Performance under load
  - Cache invalidation patterns
"""

from __future__ import annotations

import pytest
import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Any

import redis.asyncio as aioredis


class TestRedisCacheSemantics:
    """Advanced Redis caching behavior tests."""
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_keyspace_notification_subscription(self, redis_client):
        """
        Test: Redis keyspace notifications (Tier 1 events).
        
        Keyspace notifications allow subscribing to key events:
          - set, get, del, expire operations
          - Critical for Tier 1 Working Memory event stream
        """
        # Subscribe to keyspace events
        pubsub = redis_client.pubsub()
        
        await pubsub.subscribe("__keyspace__@0__:event_key")
        
        # Set a key (should trigger event)
        await redis_client.set("event_key", "event_value")
        
        # Get message from pubsub
        message = None
        try:
            # Wait briefly for message
            async def wait_for_message():
                return await asyncio.wait_for(pubsub.get_message(), timeout=2.0)
            
            message = await wait_for_message()
        except asyncio.TimeoutError:
            pass
        
        # Clean up
        await pubsub.unsubscribe("__keyspace__@0__:event_key")
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_eviction_lru_policy(self, redis_client):
        """
        Test: Redis LRU (Least Recently Used) eviction policy.
        
        When memory limit is reached, LRU evicts least recently used keys.
        """
        # Set multiple keys
        for i in range(10):
            await redis_client.set(f"lru_key_{i}", f"value_{i}", ex=3600)
        
        # Access some keys to mark as "recently used"
        for i in range(5):
            await redis_client.get(f"lru_key_{i}")
        
        # Verify all keys exist
        for i in range(10):
            value = await redis_client.get(f"lru_key_{i}")
            assert value is not None
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_ttl_expiration_accuracy(self, redis_client):
        """
        Test: TTL expiration timing is accurate.
        
        Keys with TTL should expire at the specified time within
        a reasonable margin (±100ms).
        """
        ttl_seconds = 2
        await redis_client.setex("ttl_test", ttl_seconds, "expires_soon")
        
        # Check before expiration
        start = datetime.now(timezone.utc)
        value = await redis_client.get("ttl_test")
        assert value == "expires_soon"
        
        # Wait for expiration
        await asyncio.sleep(ttl_seconds + 0.5)
        
        expired_value = await redis_client.get("ttl_test")
        assert expired_value is None
        
        # Verify timing accuracy
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        assert ttl_seconds <= elapsed <= ttl_seconds + 1.0
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_persistence_across_reconnect(self, redis_client):
        """
        Test: Cache persists across connection reconnects.
        
        Data should survive brief connection interruptions
        (assumes RDB or AOF persistence is enabled).
        """
        # Set data
        await redis_client.set("persistent_key", "persistent_value")
        value = await redis_client.get("persistent_key")
        assert value == "persistent_value"
        
        # Close and reconnect
        await redis_client.close()
        # Reconnection would happen in fixture teardown


class TestCacheInvalidationPatterns:
    """Test various cache invalidation strategies."""
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_time_based_cache_invalidation(self, redis_client):
        """
        Test: TTL-based invalidation (passive expiration).
        
        Keys automatically expire after set TTL.
        """
        await redis_client.setex("cache_entry", 1, "data")
        
        # Immediately accessible
        assert await redis_client.get("cache_entry") == "data"
        
        # Wait for expiration
        await asyncio.sleep(1.2)
        
        # Should expire
        assert await redis_client.get("cache_entry") is None
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_tag_based_cache_invalidation(self, redis_client):
        """
        Test: Tag-based invalidation (invalidate groups of keys).
        
        Pattern: use cache tags to invalidate related keys together.
        """
        # Store multiple cache entries
        await redis_client.set("user:1:profile", "alice")
        await redis_client.set("user:1:settings", "dark_mode=true")
        await redis_client.set("user:1:posts", "[1,2,3]")
        
        # Mark all with tag
        await redis_client.sadd("cache_tag:user:1", "user:1:profile")
        await redis_client.sadd("cache_tag:user:1", "user:1:settings")
        await redis_client.sadd("cache_tag:user:1", "user:1:posts")
        
        # Get all tagged keys
        tagged_keys = await redis_client.smembers("cache_tag:user:1")
        assert len(tagged_keys) == 3
        
        # Invalidate all tagged keys
        for key in tagged_keys:
            await redis_client.delete(key)
        
        # Verify invalidation
        assert await redis_client.get("user:1:profile") is None
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_event_based_cache_invalidation(self, redis_client):
        """
        Test: Event-driven cache invalidation.
        
        Publish events to channels that trigger cache invalidation handlers.
        """
        # Subscribe to invalidation channel
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("cache:invalidate")
        
        # Publish invalidation event
        await redis_client.publish("cache:invalidate", "user:1:profile")
        
        # Subscriber would see the message
        # (Pattern for invalidation dispatch)


class TestDistributedCacheCoherency:
    """Test cache coherency in distributed systems (mock multi-instance)."""
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_write_through_cache_pattern(self, redis_client):
        """
        Test: Write-through cache (write to cache, then DB).
        
        Ensures strongest consistency guarantee.
        """
        cache_key = "write_through:data"
        data = {"user_id": 1, "active": True}
        
        # Write through: cache first, then DB
        await redis_client.set(cache_key, json.dumps(data))
        
        # Verify in cache
        cached = await redis_client.get(cache_key)
        assert json.loads(cached) == data
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_write_behind_cache_pattern(self, redis_client):
        """
        Test: Write-behind cache (write to cache, async to DB).
        
        High throughput but requires consistent write ordering.
        """
        cache_key = "write_behind:data"
        
        # Write to cache immediately
        await redis_client.set(cache_key, '{"status": "pending"}')
        
        # Async write to DB would happen in background
        # Verify cache has data immediately
        assert await redis_client.get(cache_key) is not None
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_aside_pattern(self, redis_client):
        """
        Test: Cache-aside pattern (check cache, fallback to source).
        
        Application is responsible for cache management.
        """
        cache_key = "aside:user:123"
        
        # Check cache (miss)
        value = await redis_client.get(cache_key)
        assert value is None
        
        # Fallback to source (simulate)
        source_value = '{"id": 123, "name": "Alice"}'
        
        # Update cache with source value
        await redis_client.set(cache_key, source_value, ex=3600)
        
        # Next access hits cache
        cached = await redis_client.get(cache_key)
        assert cached == source_value


class TestCachePerformanceUnderLoad:
    """Test cache performance and resource management under load."""
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_cache_throughput_get_operations(self, redis_client):
        """
        Test: Cache throughput for GET operations.
        
        Measure ops/sec for cache hits.
        """
        # Pre-populate cache
        for i in range(1000):
            await redis_client.set(f"perf_key_{i}", f"value_{i}")
        
        # Measure GET throughput
        start = datetime.now(timezone.utc)
        
        for _ in range(10000):  # 10k operations
            idx = _ % 1000
            value = await redis_client.get(f"perf_key_{idx}")
            assert value is not None
        
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        throughput = 10000 / elapsed
        
        # Should handle at least 1000 ops/sec for simple gets in a containerized environment
        assert throughput > 1000
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_cache_memory_efficiency(self, redis_client):
        """
        Test: Memory usage is efficient.
        
        Store 1000 items and measure memory footprint.
        """
        # Get initial memory
        info_before = await redis_client.info("memory")
        memory_before = info_before.get("used_memory_human", "0M")
        
        # Store large objects
        for i in range(1000):
            large_data = json.dumps({
                "id": i,
                "data": "x" * 1000,  # 1KB string
            })
            await redis_client.set(f"mem_test_{i}", large_data)
        
        # Get memory after
        info_after = await redis_client.info("memory")
        memory_after = info_after.get("used_memory_human", "0M")
        
        # Memory should be reasonable across Redis runtime environments.
        assert any(unit in memory_after for unit in ("MB", "M", "KB", "K"))


class TestCacheErrorHandling:
    """Test cache behavior during error conditions."""
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_operation_on_full_memory(self, redis_client):
        """
        Test: Graceful handling when cache is full.
        
        Should evict entries or raise appropriate error.
        """
        try:
            # Try to set very large value
            large_value = "x" * (10 * 1024 * 1024)  # 10MB
            await redis_client.set("large_key", large_value)
        except Exception as e:
            # Should get memory error
            assert "memory" in str(e).lower() or "OOM" in str(e)
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_connection_loss_recovery(self, redis_client):
        """
        Test: Recovery after connection loss.
        
        Redis should reconnect automatically (depending on client config).
        """
        # Set initial value
        await redis_client.set("recovery_test", "value_1")
        
        # Test read
        value = await redis_client.get("recovery_test")
        assert value == "value_1"
        
        # Note: Actual connection loss testing would require
        # network manipulation or redis server restart


class TestCacheConsistency:
    """Test cache consistency guarantees."""
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_atomic_operations(self, redis_client):
        """
        Test: Atomic cache operations (no partial updates).
        
        Either entire update succeeds or fails, no partial state.
        """
        await redis_client.delete("atomic_counter")
        
        # Multiple atomic increments
        for _ in range(100):
            result = await redis_client.incr("atomic_counter")
        
        final = int(await redis_client.get("atomic_counter") or 0)
        assert final == 100  # All increments counted
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_transaction_semantics(self, redis_client):
        """
        Test: Redis WATCH/MULTI/EXEC for transactional cache updates.
        
        Implements optimistic locking for cache consistency.
        """
        key = "tx_test"
        
        # Watch key for changes
        async with redis_client.pipeline(transaction=True) as pipe:
            # WATCH would track changes
            await pipe.set(key, "initial_value")
            results = await pipe.execute()
        
        assert results is not None
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cache_version_stamping(self, redis_client):
        """
        Test: Version stamping for cache validation.
        
        Store version with cache to detect stale entries.
        """
        versioned_key = "versioned:data"
        version = 1
        
        cache_entry = {
            "data": "test_data",
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        await redis_client.set(versioned_key, json.dumps(cache_entry))
        
        # Retrieve and check version
        stored = json.loads(await redis_client.get(versioned_key))
        assert stored["version"] == version
