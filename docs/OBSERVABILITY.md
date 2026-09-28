# Observability & System Telemetry — DEDAN Remote

DEDAN Remote includes comprehensive operational observability across system metrics, structured JSON logging, health probes, and Prometheus telemetry.

---

## 1. Metrics & Prometheus Exporter

Prometheus metrics are exposed by the AE-OS metrics listener (default port: `9090`, started by `core/orchestrator.py` when `AEOS_ENABLED=true`).

### Available Prometheus Metrics (`core/metrics.py`)

All currently exported metrics belong to the experimental AE-OS control plane:

| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `aeos_context_memory_size` | Gauge | `tier`, `pool` | Current size (bytes) of Tier 1 working-memory context keys. |
| `aeos_context_key_count` | Gauge | `tier`, `pool` | Number of active keys in context memory. |
| `aeos_task_processing_total` | Counter | `component`, `operation`, `status` | Total number of AE-OS tasks processed. |
| `aeos_task_processing_latency_seconds` | Histogram | `component`, `operation` | Latency of AE-OS task processing operations. |
| `aeos_sentinel_block_triggers_total` | Counter | `platform`, `reason` | API Sentinel blocks (idempotency, rate limit, treasury). |
| `aeos_sentinel_dead_letter_writes_total` | Counter | `platform` | Dead-letter writes produced by API Sentinel. |
| `aeos_redis_expired_events_total` | Counter | `pool` | Redis keyspace-expired events observed. |
| `aeos_redis_token_distillations_total` | Counter | `status` | Redis token distillations recorded. |
| `aeos_neo4j_node_creations_total` | Counter | `node_type` | Nodes created by the Neo4j sync worker. |
| `aeos_neo4j_edge_updates_total` | Counter | `edge_type` | Edges updated by the Neo4j sync worker. |
| `aeos_neo4j_sync_latency_seconds` | Histogram | — | Neo4j sync cycle latency. |
| `aeos_ppo_policy_updates_total` | Counter | `status` | PPO policy update cycles executed. |
| `aeos_ppo_reward_score` | Gauge | `metric` | Reward score recorded by the experimental PPO engine. |
| `aeos_ppo_training_epoch` | Gauge | — | Current PPO training epoch number. |
| `aeos_treasury_balance` | Gauge | — | Current treasury balance in USD. |
| `aeos_treasury_hard_brake_triggers_total` | Counter | — | Treasury hard-brake constraint triggers. |

> **Note:** Discovery-pipeline counters (jobs discovered, scraper durations, notifications sent) are not exported as Prometheus metrics yet. Product-level statistics are available from `GET /api/stats` and the `website_status` table instead.

---

## 2. Structured JSON Logging

Logs are formatted uniformly using standard UTC ISO timestamps, process IDs, and structured key-value contexts via `utils/logger.py`.

Example log line from file `logs/dedan_remote.log`:
```json
{
  "timestamp": "2026-09-28T10:14:22.102Z",
  "level": "INFO",
  "logger": "scrapers.telus",
  "message": "Discovered 14 listings from TELUS Digital",
  "source": "telus",
  "jobs_count": 14,
  "elapsed_seconds": 1.42
}
```

### Log Targets
- **Standard Output (Console)**: Colored formatted logs for terminal interactivity.
- **File Rotation**: Size-based rotation (10 MB per file, 5 backups) written to `logs/dedan_remote.log` via `RotatingFileHandler`.

---

## 3. Health & Readiness Probes

Designed for Kubernetes, Docker Swarm, and cloud VM load balancers:

1. **Liveness Probe**: `GET /api/health`
   - Returns `200 OK` if the Python FastAPI event loop is responsive.
2. **Readiness Probe**: `GET /api/ready`
   - Verifies the discovery SQLite job ledger is readable by the running process.
   - Returns `503 Service Unavailable` if database files are locked or unreachable.
