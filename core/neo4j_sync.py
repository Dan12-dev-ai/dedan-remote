"""
Neo4j Epistemic Graph Sync Worker — Tiers 4, 5, 6, 7.

Runs every 1000 system tasks as a background consolidation worker.
Scans the PostgreSQL Episodic table, isolates high-performing
operational sequences, and executes atomic Cypher mutations to
generate or update Causal, Economic, and Abstraction nodes.

Edges are formatted with explicit properties:
  [:CAUSES {weight: float, historical_win_rate: float, updated_at: timestamp}]
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from core.database_async import AsyncPostgresDB
from core.metrics import (
    NEO4J_EDGE_UPDATES,
    NEO4J_NODE_CREATIONS,
    NEO4J_SYNC_LATENCY,
    TASK_PROCESSING_COUNT,
    TASK_PROCESSING_LATENCY,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Neo4j Async Driver Wrapper ────────────────────────────────────────────────


class Neo4jDriver:
    """Async Neo4j driver wrapper with connection pooling."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "aeos_neo4j",
        max_connection_pool_size: int = 10,
    ) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._max_pool = max_connection_pool_size
        self._driver: Optional[Any] = None

    async def connect(self) -> None:
        """Initialize the Neo4j driver."""
        if self._driver is not None:
            return
        try:
            from neo4j import AsyncGraphDatabase

            self._driver = AsyncGraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
                max_connection_pool_size=self._max_pool,
            )
            # Verify connectivity
            async with self._driver.session() as session:
                await session.run("RETURN 1")
            logger.info("Neo4j connected to %s", self._uri)
        except ImportError:
            logger.warning("neo4j package not installed — using mock driver")
            self._driver = _MockNeo4jDriver()

    async def close(self) -> None:
        """Close the Neo4j driver."""
        if self._driver and not isinstance(self._driver, _MockNeo4jDriver):
            await self._driver.close()
            self._driver = None
            logger.info("Neo4j driver closed")

    async def run(self, query: str, **params: Any) -> Any:
        """Execute a Cypher query."""
        if not self._driver:
            raise RuntimeError("Neo4j driver not initialized")
        async with self._driver.session() as session:  # type: ignore[union-attr]
            result = await session.run(query, **params)
            return await result.data()

    async def run_in_transaction(self, queries: list[tuple[str, dict[str, Any]]]) -> list[Any]:
        """
        Execute multiple Cypher queries atomically within a single transaction.

        Args:
            queries: List of (query, params) tuples.

        Returns:
            List of results from each query.
        """
        if not self._driver:
            raise RuntimeError("Neo4j driver not initialized")

        results: list[Any] = []
        async with self._driver.session() as session:  # type: ignore[union-attr]
            async with session.begin_transaction() as tx:
                try:
                    for query, params in queries:
                        result = await tx.run(query, **params)
                        records = await result.data()
                        results.append(records)
                    await tx.commit()
                except Exception:
                    await tx.rollback()
                    raise
        return results


class _MockNeo4jDriver:
    """Mock driver for environments where neo4j is not installed."""

    class MockSession:
        async def __aenter__(self) -> "_MockNeo4jDriver":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def run(self, query: str, **params: Any) -> "_MockResult":
            logger.debug("[Mock Neo4j] %s | params=%s", query[:80], params)
            return _MockResult()

        async def begin_transaction(self) -> "_MockNeo4jDriver.MockTransaction":
            return _MockNeo4jDriver.MockTransaction()

    class MockTransaction:
        async def __aenter__(self) -> "_MockNeo4jDriver.MockTransaction":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def run(self, query: str, **params: Any) -> "_MockResult":
            logger.debug("[Mock Neo4j TX] %s | params=%s", query[:80], params)
            return _MockResult()

        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    def session(self) -> "MockSession":
        return self.MockSession()

    async def close(self) -> None:
        pass


class _MockResult:
    async def data(self) -> list[dict[str, Any]]:
        return [{"mock": True}]


# ── Data Model ────────────────────────────────────────────────────────────────

SYNC_TASK_THRESHOLD = 1000  # Sync every N system tasks


# ── Neo4j Graph Sync Worker ───────────────────────────────────────────────────


class Neo4jEpistemicSyncWorker:
    """
    Background consolidation worker that executes every 1000 system tasks.

    Pipeline:
      1. Scan PostgreSQL Episodic table for high-performing sequences
      2. Generate Causal, Economic, and Abstraction nodes in Neo4j
      3. Update edges with [:CAUSES {weight, historical_win_rate, updated_at}]
    """

    # Cypher templates for node creation (merge to avoid duplicates)
    MERGE_CAUSAL_NODE = """
        MERGE (c:CausalNode {id: $node_id})
        ON CREATE SET
            c.name = $name,
            c.description = $description,
            c.trigger_pattern = $trigger_pattern,
            c.effect_pattern = $effect_pattern,
            c.confidence = $confidence,
            c.created_at = $created_at,
            c.updated_at = $updated_at
        ON MATCH SET
            c.confidence = CASE
                WHEN $confidence > c.confidence THEN $confidence
                ELSE c.confidence
            END,
            c.updated_at = $updated_at
        RETURN c.id AS node_id
    """

    MERGE_ECONOMIC_NODE = """
        MERGE (e:EconomicNode {id: $node_id})
        ON CREATE SET
            e.name = $name,
            e.description = $description,
            e.revenue_impact = $revenue_impact,
            e.cost_impact = $cost_impact,
            e.roi_estimate = $roi_estimate,
            e.created_at = $created_at,
            e.updated_at = $updated_at
        ON MATCH SET
            e.roi_estimate = CASE
                WHEN $roi_estimate > e.roi_estimate THEN $roi_estimate
                ELSE e.roi_estimate
            END,
            e.updated_at = $updated_at
        RETURN e.id AS node_id
    """

    MERGE_ABSTRACTION_NODE = """
        MERGE (a:AbstractionNode {id: $node_id})
        ON CREATE SET
            a.name = $name,
            a.description = $description,
            a.abstraction_level = $abstraction_level,
            a.generalization_score = $generalization_score,
            a.created_at = $created_at,
            a.updated_at = $updated_at
        ON MATCH SET
            a.generalization_score = CASE
                WHEN $generalization_score > a.generalization_score
                    THEN $generalization_score
                ELSE a.generalization_score
            END,
            a.updated_at = $updated_at
        RETURN a.id AS node_id
    """

    MERGE_CAUSES_EDGE = """
        MERGE (source)-[r:CAUSES]->(target)
        ON CREATE SET
            r.weight = $weight,
            r.historical_win_rate = $historical_win_rate,
            r.updated_at = $updated_at,
            r.edge_type = $edge_type,
            r.description = $description
        ON MATCH SET
            r.weight = CASE
                WHEN $weight > r.weight THEN $weight
                ELSE r.weight
            END,
            r.historical_win_rate = CASE
                WHEN $historical_win_rate > r.historical_win_rate
                    THEN $historical_win_rate
                ELSE r.historical_win_rate
            END,
            r.updated_at = $updated_at
        RETURN id(r) AS edge_id
    """

    def __init__(
        self,
        postgres_db: Optional[AsyncPostgresDB] = None,
        neo4j_driver: Optional[Neo4jDriver] = None,
        min_win_rate: float = 0.7,
        min_executions: int = 5,
    ) -> None:
        self._postgres = postgres_db or AsyncPostgresDB()
        self._neo4j = neo4j_driver or Neo4jDriver()
        self._min_win_rate = min_win_rate
        self._min_executions = min_executions
        self._running = False
        self._task_count: int = 0

    async def start(self) -> None:
        """Initialize connections and start tracking tasks."""
        await self._postgres.connect()
        await self._neo4j.connect()
        self._running = True
        logger.info("Neo4jEpistemicSyncWorker started (threshold=%d)", SYNC_TASK_THRESHOLD)

    async def stop(self) -> None:
        """Shutdown connections."""
        self._running = False
        await self._neo4j.close()
        await self._postgres.close()
        logger.info("Neo4jEpistemicSyncWorker stopped")

    async def increment_task_count(self) -> None:
        """Increment the internal task counter and trigger sync at threshold."""
        self._task_count += 1
        if self._task_count >= SYNC_TASK_THRESHOLD:
            logger.info(
                "Task threshold reached (%d) — triggering graph sync",
                SYNC_TASK_THRESHOLD,
            )
            await self.run_sync()
            self._task_count = 0

    async def run_sync(self) -> dict[str, int]:
        """
        Execute a full graph sync cycle.

        Returns:
            Dict with counts of nodes created and edges updated.
        """
        start_time = time.monotonic()
        logger.info("=" * 60)
        logger.info("Neo4j Epistemic Graph Sync — starting cycle")
        logger.info("=" * 60)

        stats: dict[str, int] = {
            "causal_nodes": 0,
            "economic_nodes": 0,
            "abstraction_nodes": 0,
            "edges_created": 0,
        }

        try:
            # ── Phase 1: Fetch high-performing sequences ──────────────
            sequences = await self._postgres.fetch_high_performing_sequences(
                min_win_rate=self._min_win_rate,
                min_executions=self._min_executions,
                limit=50,
            )
            logger.info("Phase 1: %d high-performing sequences fetched", len(sequences))

            if not sequences:
                logger.info("No sequences to sync — skipping")
                return stats

            now_iso = datetime.now(timezone.utc).isoformat()

            # ── Phase 2: Build Cypher mutations ──────────────────────
            cypher_batch: list[tuple[str, dict[str, Any]]] = []

            for seq in sequences:
                seq_data = seq.get("sequence_data", {})
                if isinstance(seq_data, str):
                    seq_data = json.loads(seq_data)

                win_rate = float(seq.get("win_rate", 0.0))
                avg_reward = float(seq.get("avg_reward", 0.0))
                sequence_hash = str(seq.get("sequence_hash", ""))

                if not sequence_hash:
                    continue

                # Generate deterministic node IDs from sequence hash
                causal_id = f"causal:{sequence_hash}"
                economic_id = f"economic:{sequence_hash}"
                abstraction_id = f"abstraction:{sequence_hash}"

                # Extract patterns from sequence data
                trigger = seq_data.get("trigger", "unknown")
                effect = seq_data.get("effect", "unknown")
                action_type = seq_data.get("action_type", "general")

                # ── Create Causal Node ────────────────────────────────
                cypher_batch.append(
                    (
                        self.MERGE_CAUSAL_NODE,
                        {
                            "node_id": causal_id,
                            "name": f"Causal: {action_type}",
                            "description": f"Trigger: {trigger} → Effect: {effect}",
                            "trigger_pattern": trigger,
                            "effect_pattern": effect,
                            "confidence": avg_reward,
                            "created_at": now_iso,
                            "updated_at": now_iso,
                        },
                    )
                )
                stats["causal_nodes"] += 1

                # ── Create Economic Node ──────────────────────────────
                revenue_impact = seq_data.get("revenue_impact", 0.0)
                cost_impact = seq_data.get("cost_impact", 0.0)
                roi = (revenue_impact - cost_impact) / max(cost_impact, 0.01)

                cypher_batch.append(
                    (
                        self.MERGE_ECONOMIC_NODE,
                        {
                            "node_id": economic_id,
                            "name": f"Economic: {action_type}",
                            "description": f"Rev=${revenue_impact:.2f} Cost=${cost_impact:.2f} ROI={roi:.2f}x",
                            "revenue_impact": revenue_impact,
                            "cost_impact": cost_impact,
                            "roi_estimate": roi,
                            "created_at": now_iso,
                            "updated_at": now_iso,
                        },
                    )
                )
                stats["economic_nodes"] += 1

                # ── Create Abstraction Node ───────────────────────────
                abstraction_level = seq_data.get("abstraction_level", 1)
                generalization = min(avg_reward * win_rate * 1.5, 1.0)

                cypher_batch.append(
                    (
                        self.MERGE_ABSTRACTION_NODE,
                        {
                            "node_id": abstraction_id,
                            "name": f"Abstraction: {action_type}",
                            "description": f"Level {abstraction_level} generalization",
                            "abstraction_level": abstraction_level,
                            "generalization_score": generalization,
                            "created_at": now_iso,
                            "updated_at": now_iso,
                        },
                    )
                )
                stats["abstraction_nodes"] += 1

                # ── Create CAUSES Edges ───────────────────────────────
                # Causal → Economic
                edge_weight = avg_reward
                cypher_batch.append(
                    (
                        self.MERGE_CAUSES_EDGE,
                        {
                            "source_label": "CausalNode",
                            "source_id": causal_id,
                            "target_label": "EconomicNode",
                            "target_id": economic_id,
                            "weight": edge_weight,
                            "historical_win_rate": win_rate,
                            "updated_at": now_iso,
                            "edge_type": "causal_to_economic",
                            "description": f"Causal sequence drives economic outcome (win_rate={win_rate:.3f})",
                        },
                    )
                )
                stats["edges_created"] += 1

                # Economic → Abstraction
                cypher_batch.append(
                    (
                        self.MERGE_CAUSES_EDGE,
                        {
                            "source_label": "EconomicNode",
                            "source_id": economic_id,
                            "target_label": "AbstractionNode",
                            "target_id": abstraction_id,
                            "weight": edge_weight * 0.8,
                            "historical_win_rate": win_rate,
                            "updated_at": now_iso,
                            "edge_type": "economic_to_abstraction",
                            "description": f"Economic outcome informs abstraction (weight={edge_weight * 0.8:.3f})",
                        },
                    )
                )
                stats["edges_created"] += 1

                # Abstraction → Causal (feedback loop)
                cypher_batch.append(
                    (
                        self.MERGE_CAUSES_EDGE,
                        {
                            "source_label": "AbstractionNode",
                            "source_id": abstraction_id,
                            "target_label": "CausalNode",
                            "target_id": causal_id,
                            "weight": generalization,
                            "historical_win_rate": win_rate,
                            "updated_at": now_iso,
                            "edge_type": "abstraction_to_causal",
                            "description": f"Abstraction feeds back into causal model (gen={generalization:.3f})",
                        },
                    )
                )
                stats["edges_created"] += 1

            # ── Phase 3: Execute atomic batch ─────────────────────────
            if cypher_batch:
                # Fix edge MERGE to use dynamic labels
                edge_queries: list[tuple[str, dict[str, Any]]] = []
                for q, params in cypher_batch:
                    if q == self.MERGE_CAUSES_EDGE:
                        # Build dynamic MERGE with labels
                        source_label = params.pop("source_label", "CausalNode")
                        target_label = params.pop("target_label", "EconomicNode")
                        edge_q = f"""
                            MATCH (source:{source_label} {{id: $source_id}})
                            MATCH (target:{target_label} {{id: $target_id}})
                            MERGE (source)-[r:CAUSES]->(target)
                            ON CREATE SET
                                r.weight = $weight,
                                r.historical_win_rate = $historical_win_rate,
                                r.updated_at = $updated_at,
                                r.edge_type = $edge_type,
                                r.description = $description
                            ON MATCH SET
                                r.weight = CASE
                                    WHEN $weight > r.weight THEN $weight
                                    ELSE r.weight
                                END,
                                r.historical_win_rate = CASE
                                    WHEN $historical_win_rate > r.historical_win_rate
                                        THEN $historical_win_rate
                                    ELSE r.historical_win_rate
                                END,
                                r.updated_at = $updated_at
                            RETURN id(r) AS edge_id
                        """
                        edge_queries.append((edge_q, params))
                    else:
                        edge_queries.append((q, params))

                await self._neo4j.run_in_transaction(edge_queries)
                logger.info(
                    "Phase 3: %d Cypher mutations executed in atomic transaction",
                    len(edge_queries),
                )

                # Update Prometheus metrics
                NEO4J_NODE_CREATIONS.labels(node_type="Causal").inc(stats["causal_nodes"])
                NEO4J_NODE_CREATIONS.labels(node_type="Economic").inc(stats["economic_nodes"])
                NEO4J_NODE_CREATIONS.labels(node_type="Abstraction").inc(stats["abstraction_nodes"])
                NEO4J_EDGE_UPDATES.labels(edge_type="CAUSES").inc(stats["edges_created"])

                # Mark episodic records as synced
                for seq in sequences:
                    seq_id = seq.get("id")
                    if seq_id:
                        try:
                            await self._postgres.mark_synced(
                                int(seq_id),
                                sync_target="neo4j",
                            )
                        except (ValueError, TypeError):
                            pass

        except Exception as exc:
            logger.error("Graph sync failed: %s", exc, exc_info=True)
            TASK_PROCESSING_COUNT.labels(
                component="neo4j_sync",
                operation="run_sync",
                status="failure",
            ).inc()
        finally:
            elapsed = time.monotonic() - start_time
            NEO4J_SYNC_LATENCY.observe(elapsed)
            TASK_PROCESSING_LATENCY.labels(
                component="neo4j_sync",
                operation="run_sync",
            ).observe(elapsed)
            TASK_PROCESSING_COUNT.labels(
                component="neo4j_sync",
                operation="run_sync",
                status="success",
            ).inc()

        logger.info(
            "Graph sync complete: %d nodes, %d edges in %.3fs",
            stats["causal_nodes"] + stats["economic_nodes"] + stats["abstraction_nodes"],
            stats["edges_created"],
            time.monotonic() - start_time,
        )

        return stats
