"""
Tests for the Redis keyspace listener (Tier 1 working memory).

redis-py hands back raw bytes. The listener must decode them before the
distiller and the memory-size gauge touch the payload, otherwise every
expired-key event raises TypeError/AttributeError at runtime.
"""

from __future__ import annotations

import pytest

from core.redis_listener import RedisKeyspaceListener


class FakeRedis:
    """Returns raw bytes, exactly like the real redis-py client."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.deleted: list[str] = []

    async def get(self, key: str) -> object:
        return self.value

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 1


class FakePostgres:
    """Records episodic milestones instead of writing to PostgreSQL."""

    def __init__(self) -> None:
        self.milestones: list[dict[str, object]] = []
        self.embedding_updates: list[tuple[str, str]] = []

    async def insert_episodic_milestone(self, **kwargs: object) -> str:
        self.milestones.append(dict(kwargs))
        return "milestone-1"

    async def update_embeddings_id(self, milestone_id: str, token_id: str) -> None:
        self.embedding_updates.append((milestone_id, token_id))


class FakeQdrant:
    """Accepts embeddings without contacting a vector store."""

    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []

    async def upsert_embedding(self, **kwargs: object) -> bool:
        self.upserts.append(dict(kwargs))
        return True


def make_listener(
    redis_value: object,
) -> tuple[RedisKeyspaceListener, FakeRedis, FakePostgres, FakeQdrant]:
    """Listener wired to in-memory fakes (no Redis/Postgres/Qdrant required)."""
    postgres = FakePostgres()
    qdrant = FakeQdrant()
    redis = FakeRedis(redis_value)

    listener = RedisKeyspaceListener(postgres_db=postgres)  # type: ignore[arg-type]
    listener._redis = redis  # type: ignore[assignment]
    listener._qdrant = qdrant  # type: ignore[assignment]
    return listener, redis, postgres, qdrant


class TestRedisKeyspaceListener:
    """Expired-key processing against a bytes-returning Redis client."""

    @pytest.mark.asyncio
    async def test_bytes_payload_is_decoded_and_distilled(self) -> None:
        """A bytes payload must be decoded, distilled and persisted."""
        listener, redis, postgres, qdrant = make_listener(
            b"system milestone achieved|blocked by rate limit"
        )

        await listener._process_expired_key("task:99")

        assert redis.deleted == ["_shadow:task:99"]
        assert postgres.milestones, "tokens distilled from the bytes payload were not stored"
        assert all(m["task_id"] == "task:99" for m in postgres.milestones)
        # Proof the bytes payload was decoded: the distilled text is real text.
        assert [m["tokens"][0] for m in postgres.milestones] == [
            "system milestone achieved",
            "blocked by rate limit",
        ]
        assert qdrant.upserts, "embeddings were not synced"
        assert [upsert["point_id"] for upsert in qdrant.upserts] == [
            token_id for _, token_id in postgres.embedding_updates
        ]

    @pytest.mark.asyncio
    async def test_str_payload_still_supported(self) -> None:
        """Clients configured to decode responses keep working."""
        listener, _, postgres, _ = make_listener("plain text payload")

        await listener._process_expired_key("task:100")

        assert postgres.milestones
        assert postgres.milestones[0]["tokens"] == ["plain text payload"]

    @pytest.mark.asyncio
    async def test_missing_shadow_value_is_skipped(self) -> None:
        """An expired key without a shadow value does not distil anything."""
        listener, _, postgres, _ = make_listener(None)

        await listener._process_expired_key("task:101")

        assert postgres.milestones == []
