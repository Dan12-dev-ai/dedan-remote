# 🏗️ DEDAN Remote Architecture

## Overview

DEDAN Remote is an automated remote-opportunity discovery system with a public
FastAPI REST layer. It consists of two co-running planes within a single Python
process:

1. **Product Plane** — Always-on discovery scheduler that scrapes, scores, and
   notifies about AI-related remote work opportunities
2. **AE-OS Plane** — Enterprise multi-agent backbone (optional, gated by
   `AEOS_ENABLED=true`) that provides tiered memory, RL policy learning, and
   Prometheus metrics

## High-Level Diagram

```
                         python main.py [--once | --aeos]
                                   |
               +-----------------------+-----------------------+
               |                       |                       |
    PRODUCT PLANE                    AE-OS PLANE (flag-gated)
               |                       |                       |
  +------------+-------------+    +--------+----------+--------+--+
  |                        |    |         |          |         |
APScheduler         Discovery     Rank-Notify   Email/Tele/Disc  PPO
Agent               Agent        0-100       Agent              Engine
(cron/interval)     (async)      scoring      (multi-channel)   (NumPy)
                       |           |          |          |
                       +-----------+----------+----------+----------+
                                    |              |          |
                                    v              v          v
                          +-----------------+  +----------+  +-------+
                          |  SQLite DB      |  | Redis   |  | Neo4j |
                          |  opportunities.|  | working |  | epistemic|
                          |  data/odb      |  | memory  |  | graph   |
                          +-----------------+  +----------+  +-------+
                                             |
                                             v
                                      +-----------+
                                      | Qdrant    |
                                      | vector DB |
                                      | (384-dim) |
                                      +-----------+
```

## Component Details

### Product Plane (Always On)

| Component | Tech | Description |
|-----------|------|-------------|
| `SchedulerAgent` | APScheduler | Owns the clock. Five-field cron (`CRON_SCHEDULE`) or `CHECK_INTERVAL` minutes (default 60). Fires once at startup. Overlap guard: `misfire_grace_time=300`. |
| `DiscoveryAgent` | asyncio + aiohost | One cycle: load scrapers → scrape (per-scraper timeout = REQUEST_TIMEOUT + 10s) → dedup (SHA-256 of source:url, first 16 hex chars) → rank (RankingAgent, 0-100) → store in SQLite → notify configured channels. |
| `RankingAgent` | numpy + weights | Eight dimensions (Remote 0.20, Worldwide 0.15, Salary 0.15, Beginner 0.10, AI-Related 0.15, English 0.05, Simplicity 0.10, Freshness 0.10). Weights must sum to 1.0. |
| Notifiers | stdlib smtplib, python-telegram-bot, discord-webhook | Email (SMTP/Gmail API), Telegram, Discord. Fires when score ≥ MIN_SCORE_FOR_NOTIFICATION (default 50). |

### AE-OS Plane (Optional, `--aeos` or `AEOS_ENABLED=true`)

| Component | Tech | Description |
|-----------|------|-------------|
| `AEOSOrchestrator` | — | Starts five pieces in order, then loops every 60s: |
| | 1. Prometheus HTTP server on port 9090 | Metrics exposition |
| | 2. Redis keyspace listener | `__keyevent@0__:expired` → handoff to longer memory |
| | 3. Neo4j sync worker | Counter at 1000 → consolidate graph |
| | 4. API Sentinel | Idempotency (HMAC), rate limit (token bucket), treasury (spend ceiling `AEOS_TREASURY_MAX` default $1000), SSRF guards |
| | 5. PPO engine | Actor-critic with 12-dim observation, 8 discrete actions, NumPy networks. Action `reset_circuits` logs intent; other actions no-op in current implementation. |
| API Sentinel | — | Guards four paid platforms (Shopify, TikTok Ads, Gumroad, Stripe). Three checks: idempotency (HMAC-SHA256, 300s replay window), rate limit (per-platform sliding window, cap 60, refill 1/sec), treasury (cost ceiling). |

### Data Stores

| Store | Engine | Role |
|-------|--------|------|
| Job ledger | SQLite, WAL, foreign keys | Jobs, notifications, execution history, per-site circuit state. File: `data/opportunities.db` |
| Working memory | Redis 7 Alpine, AOF | Short-lived context keys. Expiry is the handoff into longer memory. |
| Episodic memory | PostgreSQL 16, asyncpg pool | Milestones, dead-letter queue, PPO reward ledger, high-performing sequences. DB: `aeos_episodic`, user: `aeos`. |
| Vector memory | Qdrant (REST, aiohttp) | 384-dim vectors, cosine distance. Collection: `tier3_episodic_embeddings`. Vectors are hash embeddings (deterministic, no model server required). |
| Epistemic graph | Neo4j 5 Community + APOC, Bolt | Causal, economic, and abstraction nodes with CAUSES edges. Degrades to logging mock when driver is absent. |
| Metrics | prometheus-client + Prometheus | Gauges, counters, histograms. App exposes on `:9090`, Prometheus UI on `:9091`. |

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Language | Python 3.11+ (runtime image: 3.12-slim) | Async I/O-bound scraping workflow |
| Config | pydantic-settings 2, python-dotenv | Typed settings from `.env`, secrets stay out of code |
| CLI | argparse in `main.py` | `--once`, `--stats`, `--setup`, `--env`, `--aeos` |
| Scheduler | APScheduler 3, AsyncIOScheduler | Cron or interval, overlap guard |
| HTTP | aiohttp 3 + tenacity | Pooled sessions, exponential backoff, 3 attempts |
| HTML parse | BeautifulSoup 4, lxml, parsel | Per-platform scrapers |
| Numerics | NumPy | PPO actor/critic, ranking score computation |
| Logging | stdlib RotatingFileHandler + JSON formatter | `logs/dedan_remote.log`, compatible with log aggregation |
| Terminal output | Rich | Operator-facing display |
| Style | Ruff, mypy | Linting/formatting and type checking (CI-enforced) |
| Testing | pytest 8, pytest-asyncio, hypothesis | Unit, integration, property-based, security tests |
| Container | Multi-stage Dockerfile | Builder (node:22-slim + python:3.12-slim) → minimal runtime |
| Orchestration | Docker Compose 3.9 | App + Redis + PostgreSQL + Neo4j + Qdrant + Prometheus |
| API | FastAPI + uvicorn | Public REST layer ("DEDAN Remote") with auth, filtering, pagination |
| Notifications | smtplib, python-telegram-bot, discord-webhook | Email, Telegram, Discord multi-channel alerts |

### Discovery / Data Pipeline

1. **Scheduler fires** `_execute_cycle` on cron/interval
2. **Discovery loads scrapers** via `ScraperRegistry` (pkgutil auto-discovery under `scrapers/`)
3. **Per-scraper scrape** with `aiohttp` session (semaphore 5, connector 20, timeout 30s, 3 retries via tenacity)
4. **Dedup**: job id = `sha256(source + ":" + url)[:16]`. Existing IDs filtered from SQLite.
5. **Ranking**: `RankingAgent.score` applies eight weighted dimensions → 0-100 score
6. **Store**: `insert_job` writes row to SQLite with title, company, URL, source, salary, country, remote, posted_date, description, tags, discovered_at, score, notified
7. **Notify**: If score ≥ MIN_SCORE_FOR_NOTIFICATION (default 50), notifier tries Email → Telegram → Discord. `mark_notified` records which channels succeeded.
8. **Close cycle**: `complete_execution` stamps duration and counts (jobs_found, jobs_new, jobs_notified, first 10 error strings)

### Frontend / Backend Structure

| Layer | Path | Description |
|-------|------|-------------|
| **Backend** | `api/` | FastAPI application (`api.main:app`) with routers for jobs, users, auth, preferences, dashboard. Serves REST API and SPA from `/app/frontend/dist`. |
| **API Routers** | `api/routers/` | `jobs`, `users`, `auth`, `preferences`, `dashboard` (token-protected) |
| **API Schemas** | `api/schemas.py` | Pydantic models for request/response validation |
| **API Security** | `api/security.py` | bcrypt password hashing, `require_system_token` dependency for `/api/system/*` |
| **Frontend** | `frontend/` | React SPA (Vite + React 18). Built inside Docker image at `frontend/dist`. Served at `/` route. |
| **Discovery** | `scrapers/` | 9 platform-specific scrapers (`BaseScraper` subclass + auto-registration via `ScraperRegistry`) |
| **Agents** | `agents/` | `SchedulerAgent` (APScheduler), `DiscoveryAgent` (orchestrate cycle), `RankingAgent` (0-100 scoring) |
| **Config** | `config/` | Pydantic-settings `Settings` model, ranking weights, AE-OS DSNs, DEDAN Remote settings |
| **Models** | `models/` | `Job` dataclass, deterministic ID (`sha256(source+url)[:16]`) |
| **Database** | `database/` | Synchronous SQLite (`database/database.py`), async PostgreSQL (`database_async.py`) |
| **Notifications** | `notifications/` | Email (`email_notifier.py`), Telegram (`telegram_notifier.py`), Discord (`discord_notifier.py`) |
| **Utils** | `utils/` | Async HTTP client (`http_client.py`), structured logger (`logger.py`) |
| **AE-OS** | `core/` | Orchestrator, sentinel, Neo4j sync, PPO engine, Redis listener, metrics |

### Local Setup

```bash
# 1. Clone
git clone https://github.com/Dan12-dev-ai/dedan-remote.git
cd dedan-remote

# 2. Create venv
python -m venv venv
source venv/bin/activate

# 3. Install deps
pip install -r requirements.txt

# 4. Configure env
cp .env.example .env
# Edit .env with your credentials (email, telegram, discord if desired)

# 5. Init database
python main.py --setup

# 6. Run a test cycle
python main.py --once

# 7. Start scheduler
python main.py
```

### Docker Setup

```bash
# Build and start full stack (AE-OS + all datastores)
docker compose up --build -d

# View logs
docker compose logs -f

# Run a single cycle
docker compose run --rm dedan-remote python main.py --once

# Or run without AE-OS
AEOS_ENABLED=false docker compose up --build -d
```

### Health Checks

```bash
# API health
curl -fsS http://127.0.0.1:8000/api/health

# API readiness
curl -fsS http://127.0.0.1:8000/api/ready

# SPA root (must return 200)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/

# Prometheus metrics
curl -fsS http://127.0.0.1:9090/metrics | head -5
```

### Testing

```bash
# Run all tests (excluding integration which needs Docker)
pytest tests/ -m "not integration" -v

# With coverage
pytest tests/ --cov=. --cov-report=term-missing

# Security tests
pytest tests/ -m security -v

# Property-based tests
pytest tests/ -m property_based -v
```

### Deployment

#### VPS/Docker Deployment (Recommended)

```bash
# 1. Clone on server
git clone https://github.com/Dan12-dev-ai/dedan-remote.git
cd dedan-remote

# 2. Configure secrets
cp .env.example .env
# MUST change all defaults:
#   DEDAN_SYSTEM_TOKEN=<openssl rand -hex 32>
#   AEOS_IDEMPOTENCY_SECRET=<openssl rand -hex 32>
#   AEOS_POSTGRES_PASSWORD=<strong-password>
#   DEDAN_CORS_ORIGINS=https://your-domain.com
#   DEDAN_ENV=production
#   DEDAN_ENABLE_HSTS=true
#   DEDAN_TRUST_PROXY=true

# 3. Deploy
docker compose up --build -d

# 4. Bind app to loopback only (edit docker-compose.yml)
#    ports: "127.0.0.1:8000:8000"

# 5. Install Caddy reverse proxy with TLS
#    See DEPLOYMENT.md for full Caddyfile configuration

# 6. Verify
curl -fsS https://your-domain.com/api/health
```

#### Manual Deployment without Docker

1. Install system dependencies: Python 3.11+, Redis, PostgreSQL, Neo4j, Qdrant
2. Configure `.env` with all credentials (see `.env.example`)
3. Initialize SQLite: `python main.py --setup`
4. Start the scheduler: `python main.py`
5. Set up Caddy/TLS for the public API
6. Configure Prometheus metrics scraping

### Known Limitations

- **No horizontal scaling** — single-process architecture (AE-OS runs in same process)
- **AE-OS PPO policy is synthetic** — observes heartbeat counter, only `reset_circuits` action has a handler body; other actions are no-ops
- **Qdrant vectors are hash embeddings** — deterministic 384-dim, not a sentence-transformer model. Production swap required for real embeddings
- **Neo4j degrades to logging mock** when the `neo4j` driver package is absent — sync loop still completes but no graph features available
- **Job scraping is not wrapped by API Sentinel** — sentinel guards only the four enumerated commerce platforms (Shopify, TikTok Ads, Gumroad, Stripe)
- **Email requires Gmail App Password** or SMTP credentials — no OAuth2 flow implemented
- **Telegram and Discord are optional** — at least one must be enabled for notifications, or the scheduler runs silently

### Roadmap

| ID | Feature | Status |
|----|---------|--------|
| 1 | Core scraping framework with auto-discovery | ✅ Complete |
| 2 | 9 platform scrapers | ✅ Complete |
| 3 | Smart scoring and ranking (8 dimensions) | ✅ Complete |
| 4 | Multi-channel notifications | ✅ Complete |
| 5 | Circuit breaker error handling | ✅ Complete |
| 6 | Docker deployment | ✅ Complete |
| 7 | GitHub Actions CI | ✅ Complete |
| 8 | Web dashboard (React SPA served by FastAPI) | ✅ Complete |
| 9 | RSS feed output | ⏳ Planned |
| 10 | Slack webhook support | ⏳ Planned |
| 11 | More scrapers (Invisible Technologies, etc.) | ⏳ Planned |
| 12 | Machine learning-based job matching | ⏳ Planned |
| 13 | Public API endpoint for querying jobs | ✅ Complete |
| 14 | Eth-specific scoring (ComprehensiveScorer) | ✅ Complete |

*Roadmap driven by community demand and maintainer availability. Contributions welcome!*