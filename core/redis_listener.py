"""
Redis Keyspace Notification Listener — Tier 1 Working Memory Expiry Daemon.

Listens for `__keyevent@0__:expired` events from Redis. When a Tier 1
Working Memory context key expires, it:
  1. Captures the string payload from the expired key
  2. Passes it through a native token distillation pipe
  3. Logs extracted business milestones into PostgreSQL Tier 2 Episodic DB
  4. Auto-syncs text embeddings into Qdrant Tier 3 collection

All operations are asynchronous and non-blocking.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import redis.asyncio as aioredis

from core.database_async import AsyncPostgresDB
from core.metrics import (
    CONTEXT_KEY_COUNT,
    CONTEXT_MEMORY_SIZE,
    REDIS_EXPIRED_EVENTS,
    REDIS_TOKEN_DISTILLATIONS,
    TASK_PROCESSING_COUNT,
    TASK_PROCESSING_LATENCY,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class DistilledToken:
    """A single distilled token from the pipeline."""

    token_id: str
    text: str
    entropy: float  # information density score 0.0–1.0
    milestone_type: str  # e.g. "job_discovery", "application", "revenue_event"
    business_value: float = 0.0


@dataclass
class ExpiredContextPayload:
    """Parsed payload from an expired Tier 1 Working Memory key."""

    key: str
    raw_value: str
    pool: str = "default"
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tokens: list[DistilledToken] = field(default_factory=list)


# ── Token Distillation Pipeline ───────────────────────────────────────────────


class TokenDistillationPipe:
    """
    Native token distillation pipeline.

    Extracts structured business milestones from raw string payloads
    using heuristic pattern matching and entropy-based filtering.
    """

    # Milestone pattern signatures (regex-free, heuristic)
    MILESTONE_PATTERNS: dict[str, list[str]] = {
        "job_discovery": [
            "job",
            "opportunity",
            "position",
            "role",
            "hiring",
            "remote",
            "freelance",
            "contract",
            "gig",
        ],
        "application": [
            "applied",
            "submitted",
            "application",
            "resume",
            "cover letter",
            "candidate",
        ],
        "revenue_event": [
            "payment",
            "revenue",
            "sale",
            "invoice",
            "payout",
            "commission",
            "bonus",
            "earned",
        ],
        "system_event": [
            "error",
            "timeout",
            "failure",
            "circuit",
            "retry",
            "rate_limit",
            "blocked",
        ],
        "milestone": [
            "completed",
            "achieved",
            "milestone",
            "target",
            "goal",
            "threshold",
            "record",
        ],
    }

    def distill(self, raw_value: str) -> list[DistilledToken]:
        """
        Distill a raw string into structured tokens.

        Args:
            raw_value: The raw string payload from the expired key.

        Returns:
            List of DistilledToken objects.
        """
        tokens: list[DistilledToken] = []
        seen_texts: set[str] = set()

        # Split on common delimiters
        segments = raw_value.replace("\n", " ").replace("\r", " ").split("|")
        segments = [s.strip() for s in segments if s.strip()]

        for segment in segments:
            # Skip duplicates
            if segment.lower() in seen_texts:
                continue
            seen_texts.add(segment.lower())

            # Classify milestone type
            milestone_type = self._classify(segment)

            # Compute entropy score (normalized length-based information density)
            entropy = self._compute_entropy(segment)

            # Estimate business value
            business_value = self._estimate_business_value(segment, milestone_type)

            token_id = hashlib.sha256(segment.encode("utf-8")).hexdigest()[:16]

            tokens.append(
                DistilledToken(
                    token_id=token_id,
                    text=segment,
                    entropy=entropy,
                    milestone_type=milestone_type,
                    business_value=business_value,
                )
            )

        return tokens

    def _classify(self, text: str) -> str:
        """Classify a text segment into a milestone type."""
        text_lower = text.lower()
        best_type = "general"
        best_score = 0

        for mtype, patterns in self.MILESTONE_PATTERNS.items():
            score = sum(1 for p in patterns if p in text_lower)
            if score > best_score:
                best_score = score
                best_type = mtype

        return best_type

    def _compute_entropy(self, text: str) -> float:
        """
        Compute a normalized information density score (0.0–1.0).

        Uses character-level entropy normalized by max possible entropy
        for the given text length.
        """
        if not text:
            return 0.0

        from math import log2

        length = len(text)
        freq: dict[str, int] = {}
        for ch in text:
            freq[ch] = freq.get(ch, 0) + 1

        entropy = 0.0
        for count in freq.values():
            p = count / length
            entropy -= p * log2(p)

        # Normalize: max entropy for length is log2(length) for unique chars
        max_entropy = log2(max(len(freq), 2))
        normalized = entropy / max_entropy if max_entropy > 0 else 0.0
        return min(max(normalized, 0.0), 1.0)

    def _estimate_business_value(self, text: str, milestone_type: str) -> float:
        """
        Estimate business value of a token (0.0–1.0).

        Revenue events and milestones get higher base values.
        """
        base_values = {
            "revenue_event": 0.8,
            "milestone": 0.7,
            "application": 0.5,
            "job_discovery": 0.4,
            "system_event": 0.1,
            "general": 0.2,
        }
        return base_values.get(milestone_type, 0.2)


# ── Qdrant Sync Client ────────────────────────────────────────────────────────


class QdrantSyncClient:
    """
    Minimal async Qdrant client for syncing text embeddings.

    Connects to Qdrant Tier 3 vector store and upserts embeddings
    derived from distilled tokens.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection: str = "tier3_episodic_embeddings",
        vector_size: int = 384,
    ) -> None:
        self._host = host
        self._port = port
        self._collection = collection
        self._vector_size = vector_size
        self._base_url = f"http://{host}:{port}"
        self._session: Optional[Any] = None

    async def _ensure_session(self) -> Any:
        """Get or create an aiohttp session."""
        if self._session is None:
            import aiohttp

            self._session = aiohttp.ClientSession(
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def ensure_collection(self) -> None:
        """Ensure the Qdrant collection exists."""
        session = await self._ensure_session()
        # Check if collection exists
        async with session.get(
            f"{self._base_url}/collections/{self._collection}",
        ) as resp:
            if resp.status == 200:
                return

        # Create collection
        async with session.put(
            f"{self._base_url}/collections/{self._collection}",
            json={
                "name": self._collection,
                "vectors": {
                    "size": self._vector_size,
                    "distance": "Cosine",
                },
            },
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                logger.warning("Qdrant collection creation: %s", text)

    async def upsert_embedding(
        self,
        point_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> bool:
        """
        Upsert a single embedding point into Qdrant.

        Args:
            point_id: Unique point ID (string).
            vector: Embedding vector (list of floats).
            payload: Metadata payload.

        Returns:
            True if successful.
        """
        session = await self._ensure_session()
        async with session.put(
            f"{self._base_url}/collections/{self._collection}/points",
            json={
                "points": [
                    {
                        "id": point_id,
                        "vector": vector,
                        "payload": payload,
                    }
                ],
            },
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                logger.error("Qdrant upsert failed: %s", text)
                return False
            return True

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None


# ── Embedding Generator (lightweight) ─────────────────────────────────────────


class EmbeddingGenerator:
    """
    Generates text embeddings using a lightweight hash-based approach.

    In production, replace with a real embedding model (e.g. sentence-transformers).
    This implementation uses a deterministic hash-based vector for demonstration.
    """

    def __init__(self, vector_size: int = 384) -> None:
        self._vector_size = vector_size

    def generate(self, text: str) -> list[float]:
        """
        Generate a deterministic embedding vector from text.

        Uses a seeded hash approach to produce a fixed-size vector.
        """
        vector = [0.0] * self._vector_size
        for i, ch in enumerate(text):
            idx = i % self._vector_size
            vector[idx] += ord(ch) / 255.0

        # Normalize
        magnitude = sum(v * v for v in vector) ** 0.5
        if magnitude > 0:
            vector = [v / magnitude for v in vector]

        return vector


# ── Main Redis Listener ───────────────────────────────────────────────────────


class RedisKeyspaceListener:
    """
    Asynchronous, non-blocking background daemon that listens for
    Redis keyspace expired events.

    Architecture:
      - Subscribes to `__keyevent@0__:expired` via Redis Pub/Sub
      - On expiry, captures the key name and retrieves its value
      - Passes through TokenDistillationPipe for structured extraction
      - Logs milestones to PostgreSQL Tier 2
      - Syncs embeddings to Qdrant Tier 3
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        postgres_db: Optional[AsyncPostgresDB] = None,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        pool_name: str = "default",
        poll_interval: float = 1.0,
    ) -> None:
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._postgres = postgres_db or AsyncPostgresDB()
        self._qdrant = QdrantSyncClient(host=qdrant_host, port=qdrant_port)
        self._distiller = TokenDistillationPipe()
        self._embedder = EmbeddingGenerator()
        self._pool_name = pool_name
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """Start the Redis keyspace listener daemon."""
        if self._running:
            logger.warning("RedisKeyspaceListener already running")
            return

        # Connect to Redis
        self._redis = await aioredis.from_url(
            self._redis_url,
            decode_responses=True,
        )

        # Configure Redis for keyspace events (needs CONFIG SET permission)
        try:
            await self._redis.config_set("notify-keyspace-events", "Ex")
            logger.info("Redis keyspace events configured: Ex")
        except Exception as exc:
            logger.warning(
                "Could not set notify-keyspace-events (may need redis.conf): %s",
                exc,
            )

        # Subscribe to expired events
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe("__keyevent@0__:expired")
        logger.info(
            "Subscribed to __keyevent@0__:expired on %s",
            self._redis_url,
        )

        # Connect PostgreSQL
        await self._postgres.connect()
        await self._postgres.create_schema()

        # Ensure Qdrant collection
        await self._qdrant.ensure_collection()

        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("RedisKeyspaceListener started (pool=%s)", self._pool_name)

    async def _listen_loop(self) -> None:
        """Main async event loop for listening to expired keys."""
        while self._running:
            try:
                message = await self._pubsub.get_message(  # type: ignore[union-attr]
                    ignore_subscribe_messages=True,
                    timeout=self._poll_interval,
                )
                if message is None:
                    continue

                key = message.get("data", "")
                if not key or not isinstance(key, str):
                    continue

                await self._process_expired_key(key)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Redis listener error: %s", exc, exc_info=True)
                await asyncio.sleep(1.0)

    async def _process_expired_key(self, key: str) -> None:
        """
        Process a single expired key event.

        Steps:
          1. Retrieve the key's value (before expiry, from a shadow copy)
          2. Distill tokens from the value
          3. Log milestones to PostgreSQL
          4. Generate embeddings and sync to Qdrant
        """
        start_time = time.monotonic()
        logger.debug("Processing expired key: %s", key)

        # Track metrics
        REDIS_EXPIRED_EVENTS.labels(pool=self._pool_name).inc()

        # Try to get the value from a shadow key (stored with TTL metadata)
        shadow_key = f"_shadow:{key}"
        raw_value = ""
        if self._redis:
            try:
                stored = await self._redis.get(shadow_key)
                # Redis hands back raw bytes; the distiller works on text, so the
                # payload is decoded at this boundary (bytes would otherwise
                # raise TypeError inside distill()).
                if isinstance(stored, bytes):
                    raw_value = stored.decode("utf-8", errors="replace")
                elif stored:
                    raw_value = stored
                # Clean up shadow key
                await self._redis.delete(shadow_key)
            except Exception:
                raw_value = ""

        if not raw_value:
            logger.debug("No shadow value for expired key: %s", key)
            TASK_PROCESSING_COUNT.labels(
                component="redis_listener",
                operation="process_expired",
                status="skipped",
            ).inc()
            return

        # ── Step 1: Distill tokens ──────────────────────────────────────
        tokens = self._distiller.distill(raw_value)
        REDIS_TOKEN_DISTILLATIONS.labels(
            status="success" if tokens else "failure",
        ).inc()

        if not tokens:
            logger.debug("No tokens distilled from key: %s", key)
            return

        # ── Step 2: Log milestones to PostgreSQL ────────────────────────
        for token in tokens:
            try:
                milestone_id = await self._postgres.insert_episodic_milestone(
                    task_id=key,
                    milestone_type=token.milestone_type,
                    payload={
                        "token_id": token.token_id,
                        "text": token.text,
                        "entropy": token.entropy,
                        "source_key": key,
                        "pool": self._pool_name,
                    },
                    tokens=[token.text],
                    business_value=token.business_value,
                    agent_id="redis_listener",
                )

                # ── Step 3: Generate embedding & sync to Qdrant ─────────
                vector = self._embedder.generate(token.text)
                qdrant_success = await self._qdrant.upsert_embedding(
                    point_id=token.token_id,
                    vector=vector,
                    payload={
                        "milestone_id": milestone_id,
                        "milestone_type": token.milestone_type,
                        "text": token.text,
                        "business_value": token.business_value,
                        "source_key": key,
                        "pool": self._pool_name,
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                if qdrant_success:
                    await self._postgres.update_embeddings_id(
                        milestone_id,
                        token.token_id,
                    )

            except Exception as exc:
                logger.error(
                    "Failed to process token %s: %s",
                    token.token_id,
                    exc,
                )

        # Update metrics
        elapsed = time.monotonic() - start_time
        TASK_PROCESSING_LATENCY.labels(
            component="redis_listener",
            operation="process_expired",
        ).observe(elapsed)

        TASK_PROCESSING_COUNT.labels(
            component="redis_listener",
            operation="process_expired",
            status="success",
        ).inc()

        # Update context memory gauges
        CONTEXT_KEY_COUNT.labels(tier="1", pool=self._pool_name).inc()
        CONTEXT_MEMORY_SIZE.labels(
            tier="1",
            pool=self._pool_name,
        ).set(len(raw_value.encode("utf-8")))

        logger.info(
            "Processed expired key '%s': %d tokens distilled in %.3fs",
            key,
            len(tokens),
            elapsed,
        )

    async def stop(self) -> None:
        """Gracefully stop the Redis listener daemon."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._pubsub:
            await self._pubsub.unsubscribe("__keyevent@0__:expired")
            await self._pubsub.close()

        if self._redis:
            await self._redis.close()

        await self._qdrant.close()
        await self._postgres.close()

        logger.info("RedisKeyspaceListener stopped")
