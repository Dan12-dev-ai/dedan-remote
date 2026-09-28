# ADR 004: Decoupling the AE-OS Control Plane from Core Discovery

## Status
Accepted

## Context
DEDAN Remote contains an advanced, experimental multi-agent backbone named AE-OS (Autonomous Environment Operating System), consisting of Redis listeners, PPO reinforcement learning engines, Neo4j graph synchronizers, and Qdrant vector memory. Requiring all five auxiliary services to be active causes high memory footprints (~2GB+ RAM) and prevents lightweight deployment.

## Decision
We decouple the AE-OS control plane into an optional architectural layer toggled via the environment flag `AEOS_ENABLED=false` (default). When `false`, the core discovery pipeline, SQLite ledger, FastAPI server, and React SPA operate completely autonomously without connecting to Redis, Neo4j, or Qdrant.

## Consequences
- **Positive**: Core system runs on low-cost virtual private servers (512MB–1GB RAM).
- **Positive**: Eliminates fragile multi-container boot dependencies for everyday job search usage.
- **Negative**: Advanced reinforcement learning concurrency tuning and semantic graph querying require explicitly turning on AE-OS and running the full Docker Compose stack.
