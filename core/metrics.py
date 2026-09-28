"""
Prometheus Telemetry Gauges — Instrumented throughout AE-OS core components.

Exposes gauges for:
  - Context memory sizes (Tier 1 Working Memory)
  - Task processing latencies
  - Sentinel block triggers
  - Graph sync operations
  - PPO training metrics
"""

from __future__ import annotations

from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# ── Global Registry ──────────────────────────────────────────────────────────
_metrics_server_started: bool = False

# ── Context Memory Gauges ─────────────────────────────────────────────────────
CONTEXT_MEMORY_SIZE = Gauge(
    "aeos_context_memory_size",
    "Current size (bytes) of Tier 1 Working Memory context keys",
    ["tier", "pool"],
)

CONTEXT_KEY_COUNT = Gauge(
    "aeos_context_key_count",
    "Number of active keys in context memory",
    ["tier", "pool"],
)

# ── Task Processing Latencies ────────────────────────────────────────────────
TASK_PROCESSING_LATENCY = Histogram(
    "aeos_task_processing_latency_seconds",
    "Latency of task processing operations",
    ["component", "operation"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

TASK_PROCESSING_COUNT = Counter(
    "aeos_task_processing_total",
    "Total number of tasks processed",
    ["component", "operation", "status"],
)

# ── Sentinel Block Triggers ──────────────────────────────────────────────────
SENTINEL_BLOCK_TRIGGERS = Counter(
    "aeos_sentinel_block_triggers_total",
    "Total number of API sentinel blocks triggered",
    ["platform", "reason"],  # reason: idempotency, rate_limit, treasury
)

SENTINEL_DEAD_LETTER_WRITES = Counter(
    "aeos_sentinel_dead_letter_writes_total",
    "Total number of payloads routed to dead-letter queue",
    ["platform"],
)

# ── Redis Listener ────────────────────────────────────────────────────────────
REDIS_EXPIRED_EVENTS = Counter(
    "aeos_redis_expired_events_total",
    "Total number of Redis keyspace expired events captured",
    ["pool"],
)

REDIS_TOKEN_DISTILLATIONS = Counter(
    "aeos_redis_token_distillations_total",
    "Total token distillation operations completed",
    ["status"],  # success, failure
)

# ── Neo4j Graph Sync ─────────────────────────────────────────────────────────
NEO4J_NODE_CREATIONS = Counter(
    "aeos_neo4j_node_creations_total",
    "Total Neo4j nodes created",
    ["node_type"],  # Causal, Economic, Abstraction
)

NEO4J_EDGE_UPDATES = Counter(
    "aeos_neo4j_edge_updates_total",
    "Total Neo4j edges updated",
    ["edge_type"],  # CAUSES, ECONOMIC, ABSTRACTION
)

NEO4J_SYNC_LATENCY = Histogram(
    "aeos_neo4j_sync_latency_seconds",
    "Latency of Neo4j graph sync operations",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

# ── PPO Engine ────────────────────────────────────────────────────────────────
PPO_POLICY_UPDATES = Counter(
    "aeos_ppo_policy_updates_total",
    "Total number of PPO policy gradient updates",
    ["status"],  # success, failure
)

PPO_REWARD_SCORE = Gauge(
    "aeos_ppo_reward_score",
    "Current PPO reward score",
    ["metric"],  # net_profit, risk_index, token_overhead, total
)

PPO_TRAINING_EPOCH = Gauge(
    "aeos_ppo_training_epoch",
    "Current PPO training epoch number",
)

# ── Treasury ──────────────────────────────────────────────────────────────────
TREASURY_BALANCE = Gauge(
    "aeos_treasury_balance",
    "Current treasury balance in USD",
)

TREASURY_HARD_BRAKE_TRIGGERS = Counter(
    "aeos_treasury_hard_brake_triggers_total",
    "Total number of treasury hard-brake constraint triggers",
)


def start_metrics_server(port: int = 9090) -> None:
    """Start the Prometheus metrics HTTP server (idempotent)."""
    global _metrics_server_started
    if _metrics_server_started:
        return
    start_http_server(port)
    _metrics_server_started = True
    print(f"[Metrics] Prometheus HTTP server started on port {port}")


def format_labels(**kwargs: object) -> dict[str, str]:
    """Format label values as strings for Prometheus."""
    return {k: str(v) for k, v in kwargs.items()}