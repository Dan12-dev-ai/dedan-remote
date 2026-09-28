# ADR 003: SQLite as the Default Canonical Job Ledger

## Status
Accepted

## Context
Running and self-hosting an opportunity discovery engine should not mandate provisioning and maintaining heavy relational database clusters (e.g., PostgreSQL / MySQL) for simple local setups or lightweight VPS instances. However, data persistence, deduplication, and atomic write transactions are mandatory.

## Decision
We select SQLite (configured in Write-Ahead Logging mode `PRAGMA journal_mode=WAL;`) as the primary out-of-the-box storage engine for the job opportunity ledger (`data/opportunities.db`) and user data (`data/dedan_users.db`). An asynchronous PostgreSQL storage module is provided as an optional high-concurrency tier.

## Consequences
- **Positive**: Zero operational overhead on first start; `python main.py` runs without requiring external database services or credentials.
- **Positive**: Full ACID transactions and native SQL query capabilities for the FastAPI backend.
- **Negative**: Concurrency is limited under massive parallel writes across multiple OS processes, which is mitigated by our architectural separation of discovery cycles and WAL mode.
