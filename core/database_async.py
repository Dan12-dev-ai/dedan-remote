"""
Async PostgreSQL Database Layer — Tier 2 Episodic Memory Store.

Provides connection pooling and async DML for:
  - Episodic memory (Tier 2 — business milestones, task outcomes)
  - Dead-letter queue (failed sentinel payloads)
  - PPO reward ledger
  - Execution task history
"""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg
from asyncpg import Pool, Record

from utils.logger import get_logger

logger = get_logger(__name__)


class AsyncPostgresDB:
    """Async PostgreSQL connection pool manager for Tier 2+ storage."""

    def __init__(
        self,
        dsn: str = "",
        host: str = "localhost",
        port: int = 5432,
        user: str = "aeos",
        password: str = "",
        database: str = "aeos_episodic",
        min_size: int = 4,
        max_size: int = 20,
    ) -> None:
        self._dsn = dsn or (f"postgresql://{user}:{password}@{host}:{port}/{database}")
        self._pool: Optional[Pool] = None
        self._min_size = min_size
        self._max_size = max_size

    async def connect(self) -> None:
        """Initialize the connection pool."""
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=30,
        )
        logger.info(
            "AsyncPostgresDB pool created (min=%d, max=%d)",
            self._min_size,
            self._max_size,
        )

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("AsyncPostgresDB pool closed")

    async def _execute(self, query: str, *args: Any) -> str:
        """Execute a query and return status."""
        if not self._pool:
            raise RuntimeError("Database pool not initialized — call connect() first")
        async with self._pool.acquire() as conn:
            result = await conn.execute(query, *args)
            return result

    async def _fetch(self, query: str, *args: Any) -> list[Record]:
        """Execute a query and return rows."""
        if not self._pool:
            raise RuntimeError("Database pool not initialized — call connect() first")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return rows

    async def _fetchrow(self, query: str, *args: Any) -> Optional[Record]:
        """Execute a query and return first row."""
        if not self._pool:
            raise RuntimeError("Database pool not initialized — call connect() first")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return row

    # ── Schema Initialization ────────────────────────────────────────────────

    async def create_schema(self) -> None:
        """Create all Tier 2+ tables if they don't exist."""
        await self._execute("""
            CREATE TABLE IF NOT EXISTS tier2_episodic_memory (
                id              BIGSERIAL PRIMARY KEY,
                task_id         TEXT NOT NULL,
                agent_id        TEXT NOT NULL DEFAULT 'discovery',
                milestone_type  TEXT NOT NULL,
                payload         JSONB NOT NULL,
                tokens          TEXT[] DEFAULT '{}',
                embeddings_id   TEXT,
                business_value  REAL DEFAULT 0.0,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                synced_to_qdrant BOOLEAN DEFAULT FALSE,
                synced_to_neo4j BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS tier2_dead_letter_queue (
                id              BIGSERIAL PRIMARY KEY,
                task_id         TEXT NOT NULL,
                platform        TEXT NOT NULL,
                payload         JSONB NOT NULL,
                failure_reason  TEXT NOT NULL,
                failure_detail  JSONB,
                idempotency_key TEXT,
                retry_count     INTEGER DEFAULT 0,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at     TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS tier2_ppo_reward_ledger (
                id              BIGSERIAL PRIMARY KEY,
                epoch           INTEGER NOT NULL,
                state_vector    JSONB NOT NULL,
                action_taken    INTEGER NOT NULL,
                reward_total    REAL NOT NULL,
                net_profit      REAL NOT NULL DEFAULT 0.0,
                risk_index      REAL NOT NULL DEFAULT 0.0,
                token_overhead  REAL NOT NULL DEFAULT 0.0,
                policy_loss     REAL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS tier2_high_performing_sequences (
                id              BIGSERIAL PRIMARY KEY,
                sequence_hash   TEXT NOT NULL UNIQUE,
                sequence_data   JSONB NOT NULL,
                avg_reward      REAL NOT NULL DEFAULT 0.0,
                win_rate        REAL NOT NULL DEFAULT 0.0,
                execution_count INTEGER NOT NULL DEFAULT 0,
                first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_episodic_task
                ON tier2_episodic_memory(task_id);
            CREATE INDEX IF NOT EXISTS idx_episodic_milestone
                ON tier2_episodic_memory(milestone_type);
            CREATE INDEX IF NOT EXISTS idx_episodic_synced_neo4j
                ON tier2_episodic_memory(synced_to_neo4j)
                WHERE synced_to_neo4j = FALSE;
            CREATE INDEX IF NOT EXISTS idx_dlq_platform
                ON tier2_dead_letter_queue(platform);
            CREATE INDEX IF NOT EXISTS idx_ppo_epoch
                ON tier2_ppo_reward_ledger(epoch DESC);
            CREATE INDEX IF NOT EXISTS idx_high_perf_win_rate
                ON tier2_high_performing_sequences(win_rate DESC);
        """)
        logger.info("AsyncPostgresDB schema initialized")

    # ── Tier 2 Episodic Memory ───────────────────────────────────────────────

    async def insert_episodic_milestone(
        self,
        task_id: str,
        milestone_type: str,
        payload: dict[str, Any],
        tokens: list[str],
        business_value: float = 0.0,
        agent_id: str = "discovery",
    ) -> int:
        """Insert a business milestone into Tier 2 episodic memory."""
        row = await self._fetchrow(
            """INSERT INTO tier2_episodic_memory
               (task_id, agent_id, milestone_type, payload, tokens, business_value)
               VALUES ($1, $2, $3, $4::jsonb, $5::text[], $6)
               RETURNING id""",
            task_id,
            agent_id,
            milestone_type,
            json.dumps(payload),
            tokens,
            business_value,
        )
        inserted_id = row["id"] if row else 0
        logger.debug(
            "Episodic milestone #%d inserted (type=%s, task=%s)",
            inserted_id,
            milestone_type,
            task_id,
        )
        return inserted_id  # type: ignore[return-value]

    async def fetch_unsynced_episodic(
        self,
        limit: int = 100,
        sync_target: str = "neo4j",
    ) -> list[Record]:
        """Fetch episodic records not yet synced to the given target."""
        col = "synced_to_neo4j" if sync_target == "neo4j" else "synced_to_qdrant"
        rows = await self._fetch(
            f"""SELECT * FROM tier2_episodic_memory
                WHERE {col} = FALSE
                ORDER BY id ASC
                LIMIT $1""",
            limit,
        )
        return rows

    async def mark_synced(
        self,
        record_id: int,
        sync_target: str = "neo4j",
    ) -> None:
        """Mark an episodic record as synced."""
        col = "synced_to_neo4j" if sync_target == "neo4j" else "synced_to_qdrant"
        await self._execute(
            f"UPDATE tier2_episodic_memory SET {col} = TRUE WHERE id = $1",
            record_id,
        )

    async def update_embeddings_id(self, record_id: int, embeddings_id: str) -> None:
        """Update the Qdrant embeddings ID for a record."""
        await self._execute(
            "UPDATE tier2_episodic_memory SET embeddings_id = $1 WHERE id = $2",
            embeddings_id,
            record_id,
        )

    # ── Dead-Letter Queue ────────────────────────────────────────────────────

    async def insert_dead_letter(
        self,
        task_id: str,
        platform: str,
        payload: dict[str, Any],
        failure_reason: str,
        failure_detail: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> int:
        """Insert a failed payload into the dead-letter queue."""
        row = await self._fetchrow(
            """INSERT INTO tier2_dead_letter_queue
               (task_id, platform, payload, failure_reason, failure_detail, idempotency_key)
               VALUES ($1, $2, $3::jsonb, $4, $5::jsonb, $6)
               RETURNING id""",
            task_id,
            platform,
            json.dumps(payload),
            failure_reason,
            json.dumps(failure_detail) if failure_detail else None,
            idempotency_key,
        )
        dlq_id = row["id"] if row else 0
        logger.warning(
            "Dead-letter entry #%d created (platform=%s, reason=%s)",
            dlq_id,
            platform,
            failure_reason,
        )
        return dlq_id  # type: ignore[return-value]

    async def get_dead_letter_queue(self, limit: int = 100) -> list[Record]:
        """Return recent dead-letter queue entries for investigation and tests."""
        rows = await self._fetch(
            """SELECT * FROM tier2_dead_letter_queue
               ORDER BY created_at DESC
               LIMIT $1""",
            limit,
        )
        return rows

    # ── PPO Reward Ledger ─────────────────────────────────────────────────────

    async def insert_ppo_reward(
        self,
        epoch: int,
        state_vector: dict[str, Any],
        action_taken: int,
        reward_total: float,
        net_profit: float = 0.0,
        risk_index: float = 0.0,
        token_overhead: float = 0.0,
        policy_loss: Optional[float] = None,
    ) -> int:
        """Log a PPO reward entry."""
        row = await self._fetchrow(
            """INSERT INTO tier2_ppo_reward_ledger
               (epoch, state_vector, action_taken, reward_total,
                net_profit, risk_index, token_overhead, policy_loss)
               VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, $8)
               RETURNING id""",
            epoch,
            json.dumps(state_vector),
            action_taken,
            reward_total,
            net_profit,
            risk_index,
            token_overhead,
            policy_loss,
        )
        return row["id"] if row else 0  # type: ignore[return-value]

    async def get_latest_ppo_epoch(self) -> int:
        """Get the latest PPO epoch number."""
        row = await self._fetchrow(
            "SELECT COALESCE(MAX(epoch), 0) AS max_epoch FROM tier2_ppo_reward_ledger"
        )
        return row["max_epoch"] if row else 0  # type: ignore[return-value]

    # ── High-Performing Sequences ────────────────────────────────────────────

    async def upsert_high_performing_sequence(
        self,
        sequence_hash: str,
        sequence_data: dict[str, Any],
        reward: float,
        win_rate: float,
        execution_count: int = 1,
    ) -> None:
        """Upsert a high-performing operational sequence."""
        await self._execute(
            """INSERT INTO tier2_high_performing_sequences
               (sequence_hash, sequence_data, avg_reward, win_rate, execution_count,
                first_seen_at, last_seen_at)
               VALUES ($1, $2::jsonb, $3, $4, $5, NOW(), NOW())
               ON CONFLICT (sequence_hash) DO UPDATE SET
                   avg_reward = ((t2_high_performing_sequences.avg_reward *
                                  t2_high_performing_sequences.execution_count) + $3) /
                                 (t2_high_performing_sequences.execution_count + 1),
                   win_rate = CASE
                       WHEN $4 > t2_high_performing_sequences.win_rate
                           THEN $4
                       ELSE t2_high_performing_sequences.win_rate
                   END,
                   execution_count = t2_high_performing_sequences.execution_count + 1,
                   last_seen_at = NOW()""",
            sequence_hash,
            json.dumps(sequence_data),
            reward,
            win_rate,
            execution_count,
        )

    async def fetch_high_performing_sequences(
        self,
        min_win_rate: float = 0.7,
        min_executions: int = 5,
        limit: int = 50,
    ) -> list[Record]:
        """Fetch high-performing operational sequences for Neo4j sync."""
        rows = await self._fetch(
            """SELECT * FROM tier2_high_performing_sequences
               WHERE win_rate >= $1 AND execution_count >= $2
               ORDER BY win_rate DESC, avg_reward DESC
               LIMIT $3""",
            min_win_rate,
            min_executions,
            limit,
        )
        return rows

    # ── Health Check ─────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Check database connectivity."""
        try:
            row = await self._fetchrow("SELECT 1 AS ok")
            return row is not None and row["ok"] == 1
        except Exception as exc:
            logger.error("AsyncPostgresDB ping failed: %s", exc)
            return False
