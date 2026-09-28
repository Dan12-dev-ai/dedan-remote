# AIJobFinder — System Design Brief

Compact architecture reference for the whole repository. Describes what is built, which technology each part uses, and how a run actually moves through the code.

**Product:** an autonomous scanner that finds remote AI-work listings, scores them, stores them, and alerts you.
**Runtime:** one Python 3.12 asyncio process (`main.py`), optionally sitting on a six-container data plane.

---

## 1. What the system is

AIJobFinder watches public career pages on AI-training and data-annotation platforms. On a schedule it:

1. Scrapes every registered platform at once.
2. Drops jobs it has already seen.
3. Scores each new job from 0 to 100.
4. Writes the job and the cycle stats to SQLite.
5. Sends Email, Telegram, and Discord alerts for jobs at or above the score floor (default 50).

A second plane, **AE-OS** (the enterprise multi-agent backbone), can run in the same process. It keeps a tiered memory of what the system did, guards outbound paid API calls, trains a small reinforcement-learning policy, and exports Prometheus metrics. AE-OS starts only when you pass `--aeos` or set `AEOS_ENABLED=true`.

The two planes share a process and a clock. The discovery cycle does not call AE-OS, and AE-OS does not drive the scrapers. Each plane has its own database.

---

## 2. System architecture

```
                        python main.py [--once | --aeos]
                                  |
                    +-------------+-------------+
                    |                           |
            PRODUCT PLANE                  AE-OS PLANE
            (always on)                    (flag-gated)
                    |                           |
            SchedulerAgent              AEOSOrchestrator
            APScheduler                       |
                    |              +----------+----------+----------+
            DiscoveryAgent         |          |          |          |
                    |           Redis      Neo4j      API       PPO
         +----------+------+   listener    sync     Sentinel   engine
         |          |      |      |          |          |          |
     Scrapers   Ranking  Notify  expire   every     HMAC +    12-dim
     x8 async   0-100    Email   -> PG    1000      bucket +  state
                         Tele    -> Qdrant tasks    $1000     -> 8 actions
                         Discord                     brake
         |          |      |
         +----+-----+------+
              |
         SQLite WAL
         data/opportunities.db

AE-OS stores elsewhere:
  Redis 7        working memory, key expiry events
  PostgreSQL 16  episodic memory, dead-letter queue, PPO ledger
  Qdrant         384-dim vectors, cosine, collection tier3_episodic_embeddings
  Neo4j 5        Causal / Economic / Abstraction graph
  Prometheus     scrape :9090 every 15s
```

### Product-plane cycle

`SchedulerAgent` owns the clock. A five-field cron expression wins (`CRON_SCHEDULE`, UTC). A bad cron falls back to `CHECK_INTERVAL` minutes (default 60). The scheduler also fires once at startup. A cycle already running is skipped (`misfire_grace_time` 300 seconds). Shutdown waits up to 30 seconds for the current cycle, then closes the HTTP pool.

`DiscoveryAgent.run_once()` is one cycle:

| Step | What happens |
|------|----------------|
| Open a row | `execution_history.status = running` |
| Load scrapers | `ScraperRegistry` imports every `BaseScraper` subclass under `scrapers/` |
| Skip sick sources | `website_status.circuit_open` skips that platform |
| Scrape | `asyncio.gather`, per-scraper timeout = `REQUEST_TIMEOUT + 10` (default 40s) |
| Health | success resets the source; timeout or exception increments `consecutive_failures`; at 5 failures the circuit stays open for 60s |
| Dedup | job id is SHA-256 of `source:url`, first 16 hex chars; existing ids are dropped |
| Score | `RankingAgent` writes a 0–100 float |
| Store | `jobs` row in SQLite |
| Notify | score ≥ `MIN_SCORE_FOR_NOTIFICATION` goes to every configured channel |
| Close the row | `jobs_found`, `jobs_new`, `jobs_notified`, first 10 error strings |

### AE-OS plane

`AEOSOrchestrator.start()` brings five pieces up in order, then loops every 60 seconds:

1. Prometheus HTTP server on port 9090.
2. Redis keyspace listener (`__keyevent@0__:expired`, Redis started with `notify-keyspace-events Ex`).
3. Neo4j sync worker. A counter increments every loop; at 1000 it consolidates.
4. API Sentinel (idempotency, rate limit, treasury).
5. PPO engine. An action is chosen every loop. A training step runs every 300 seconds once at least two rollouts exist.

Shutdown reverses that order and then closes the PostgreSQL pool.

---

## 3. Technology used

### Language and runtime

| Piece | Choice | Why it is here |
|-------|--------|----------------|
| Language | Python 3.11+ (image is 3.12-slim) | One async process for I/O-bound scraping |
| Config | pydantic-settings 2, `python-dotenv` | Typed settings from `.env`, secrets stay out of code |
| CLI | `argparse` in `main.py` | `--once`, `--stats`, `--setup`, `--env`, `--aeos` |
| Scheduler | APScheduler 3, `AsyncIOScheduler` | Cron or interval, overlap guard |
| HTTP | aiohttp 3 + tenacity | Pooled sessions, exponential backoff, 3 attempts |
| HTML parse | BeautifulSoup 4, lxml, parsel | Per-platform scrapers |
| Numerics | NumPy | PPO actor and critic, no deep-learning framework |
| Logging | stdlib `RotatingFileHandler` + JSON formatter | `logs/ai_opportunity_finder.log` |
| Terminal output | Rich | Operator-facing display |

### Data stores

| Store | Engine | Role |
|-------|--------|------|
| Job ledger | SQLite, WAL, foreign keys | Jobs, notifications, cycle history, per-site circuit state. File: `data/opportunities.db` |
| Working memory | Redis 7 Alpine, AOF | Short-lived context keys. Expiry is the handoff into longer memory |
| Episodic memory | PostgreSQL 16, asyncpg pool 4–20 | Milestones, dead-letter queue, PPO reward ledger, high-performing sequences |
| Vector memory | Qdrant (REST on aiohttp, cosine, 384 dims) | Embedding of distilled tokens. Collection `tier3_episodic_embeddings` |
| Epistemic graph | Neo4j 5 Community + APOC, Bolt | Causal, economic, and abstraction nodes |
| Metrics | prometheus-client + Prometheus | Gauges, counters, histograms. App `:9090`, Prometheus UI `:9091` |

### Notifications

| Channel | Library | When it sends |
|---------|---------|----------------|
| Email | stdlib `smtplib` (Gmail SMTP `smtp.gmail.com:587`) | Address and app password are set |
| Telegram | `python-telegram-bot` 20 | `ENABLE_TELEGRAM=true` plus bot token and chat id |
| Discord | `discord-webhook` | `ENABLE_DISCORD=true` plus webhook URL |

### Build, quality, test

| Concern | Tool |
|---------|------|
| Image | Multi-stage Dockerfile. Builder installs with gcc; runtime image keeps `ca-certificates` and the user site-packages |
| Compose | Docker Compose 3.9. App depends on Redis, healthy Postgres, Neo4j, Qdrant |
| Unit / async tests | pytest 8, pytest-asyncio, pytest-cov, pytest-mock, pytest-xdist (`-n auto`), pytest-timeout (300s) |
| Property tests | Hypothesis (max 1000 examples) + Faker |
| Live dependencies | testcontainers for PostgreSQL and Redis |
| HTTP fakes | aioresponses |
| Style | black, isort, flake8, mypy |
| Security tests | pycryptodome, requests, hand-rolled OWASP payloads |

---

## 4. Module map

```
main.py                 CLI and process lifetime
config/settings.py      Settings model, ranking weights, AE-OS DSNs
models/job.py           Frozen Job dataclass, deterministic id
scrapers/               BaseScraper + registry + 8 platforms
agents/                 Scheduler, Discovery, Ranking
intelligence/           Second scorer, Ethiopia-focused (library, see §6)
database/database.py    Synchronous SQLite
notifications/          Fan-out to email, Telegram, Discord
utils/http_client.py    Shared aiohttp session, semaphore, retries
utils/logger.py         JSON rotating logs
core/                   AE-OS only
  orchestrator.py       Lifecycle and 60s loop
  redis_listener.py     Expiry → distill → Postgres → Qdrant
  database_async.py     asyncpg schema and writes
  neo4j_sync.py         Sequence → Cypher MERGE
  api_sentinel.py       Idempotency, token bucket, treasury, SSRF guards
  ppo_engine.py         Actor, critic, reward, clipped update
  metrics.py            Prometheus instruments
```

A new platform is a new file in `scrapers/` whose class subclasses `BaseScraper` and sets `name`, `source`, and `base_url`. `ScraperRegistry` imports the package with `pkgutil` and registers every concrete subclass. No central list to edit.

Platforms shipped: Outlier, Alignerr, OneForma, TELUS Digital, Welocalize, Appen, DataAnnotation, Clickworker.

---

## 5. Data model

### SQLite — product ledger

| Table | Holds |
|-------|--------|
| `jobs` | id (PK), title, company, url, source, salary, country, remote, posted_date, description, tags, discovered_at, score, notified |
| `notifications` | job_id → jobs, channel, status, sent_at, error |
| `execution_history` | one row per cycle: found / new / notified / errors / duration |
| `website_status` | one row per source: last success, consecutive failures, circuit open and until-when |

Indexes: `jobs(source)`, `jobs(score DESC)`, `jobs(notified)`, `jobs(discovered_at DESC)`.

### PostgreSQL — AE-OS episodic store (`aeos_episodic`)

| Table | Holds |
|-------|--------|
| `tier2_episodic_memory` | task, agent, milestone type, JSON payload, token list, business value, Qdrant id, sync flags |
| `tier2_dead_letter_queue` | blocked sentinel payloads: platform, reason, idempotency key, retry count |
| `tier2_ppo_reward_ledger` | epoch, state vector, action, reward, profit, risk, token overhead, policy loss |
| `tier2_high_performing_sequences` | sequence hash, JSON, average reward, win rate, execution count |

Partial index on episodic rows still waiting for Neo4j. Win-rate index supports the graph sync query (win rate ≥ 0.7 and at least 5 executions, limit 50).

### Neo4j — epistemic graph

For each qualifying sequence the worker MERGEs three nodes and `CAUSES` edges:

| Label | Identity | Notable properties |
|-------|----------|--------------------|
| `CausalNode` | `causal:<hash>` | trigger pattern, effect pattern, confidence |
| `EconomicNode` | `economic:<hash>` | revenue impact, cost impact, ROI |
| `AbstractionNode` | `abstraction:<hash>` | abstraction level, generalization score |

Edge type `CAUSES` carries `weight`, `historical_win_rate`, `updated_at`. MERGE keeps the higher confidence, ROI, weight, and win rate. The driver uses the official async Neo4j client with a pool of 10. If that package cannot load, a mock driver logs the Cypher and continues.

### Qdrant point

Id plus a 384-float vector plus a payload (token text, milestone type, business value). Distance is cosine. The vector today is a deterministic hash embedding (`EmbeddingGenerator`), built so the pipe runs without a model server. A production swap is a real embedding model of the same width.

### Redis key

Tier 1 context is a string key. On expiry the listener:

1. Splits the payload on `|`.
2. Classifies each segment: `job_discovery`, `application`, `revenue_event`, `system_event`, `milestone`, or `general`.
3. Scores character-level entropy and a heuristic business value.
4. Inserts a Postgres milestone.
5. Upserts the embedding.

---

## 6. Decision systems

### RankingAgent — this is what notifications use

Eight dimensions, each 0–100, combined with weights that must sum to 1.0:

| Dimension | Weight | Rule of thumb |
|-----------|--------|----------------|
| Remote | 0.20 | Remote = 100, otherwise 0 |
| Worldwide | 0.15 | No country, or worldwide/global/remote = 100, else 30 |
| Salary | 0.15 | High keywords (about $50+/50k+) = 100, hourly/mid = 60, any salary = 40, missing = 0 |
| Beginner | 0.10 | Entry-level language = 100, else 20 |
| AI related | 0.15 | AI/ML/RLHF/annotation language = 100, labeling/transcription = 70, else 30 |
| English | 0.05 | Non-English script markers = 30, else 80 |
| Simplicity | 0.10 | Low-barrier language = 90, PhD/senior = 30, else 60 |
| Freshness | 0.10 | Newer posts score higher; unknown date = 70 |

Final score is clamped to 0–100 and rounded to one decimal.

### Intelligence package — a second, richer scorer

`intelligence/` evaluates a job for an applicant based in Ethiopia. `ComprehensiveScorer.evaluate()` returns a `ScorerResult` with a verdict.

| Module | Question it answers | Weight in the overall score |
|--------|---------------------|-----------------------------|
| `EligibilityEngine` | Can someone in Ethiopia reach this role and get paid (PayPal, Payoneer, Wise, crypto, Telebirr; sanctions and "US only" language)? | 0.30 |
| `SkillMatcher` | Beginner / entry / specialized fit | 0.25 |
| `SuccessPredictor` | Chance of actually getting it, given skill and difficulty | 0.25 |
| `DifficultyEstimator` | Very Easy → Very Hard, inverted into an ease score | 0.20 |

Verdict: **RECOMMENDED** at 75 and above, **CONSIDER** at 45 and above, otherwise reject. The discovery cycle does not call this package. `RankingAgent` is the live gate. Wire `ComprehensiveScorer` in beside it when the Ethiopia-specific verdict should decide alerts.

### PPO — in-process policy, NumPy only

Observation is 12 floats:

`jobs_found_rate, jobs_new_rate, notification_rate, avg_score, error_rate, circuit_breaker_count, execution_duration, concurrent_scrapers, rate_limit_hits, treasury_remaining, memory_utilization, time_of_day`

Eight discrete actions: raise or lower concurrency, raise or lower the rate-limit delay, raise or lower the minimum notify score, reset circuits, or no-op.

Networks, both Xavier-initialized, both `Linear → ReLU → Linear → ReLU → Linear`:

- Actor: 12 → 64 → 64 → 8, softmax over actions.
- Critic: 12 → 64 → 64 → 1.

Reward:

```
R = net_profit_change − risk_index − token_overhead
```

When the caller passes zeros, profit is derived from the change in jobs-found rate (×0.3) and average score (×0.2). Risk mixes error rate (×0.6) and open circuits (×0.4). Token overhead mixes execution duration and concurrency.

Training hyperparameters: learning rate 3e-4, γ 0.99, GAE λ 0.95, clip ε 0.2, entropy coefficient 0.01, value coefficient 0.5, max grad norm 0.5, 4 epochs, batch 64, target KL 0.02.

The orchestrator currently builds the 12-vector from the heartbeat counter, and the only action with a handler body is `reset_circuits` (it logs the intent). The policy, the ledger write, and the metrics are live. Closing the loop means feeding real cycle stats into `PPOState` and applying the chosen action to `Settings` / circuit state.

---

## 7. API Sentinel

Every paid outbound connector is meant to pass `APISentinel` before the socket opens. Platforms enumerated: Shopify, TikTok Ads, Gumroad, Stripe. Three checks, in order:

1. **Idempotency.** Key format `v1:<unix>:<task_id>:<platform>:<nonce>:<hmac16>`. HMAC-SHA256 of the prefix, truncated to 16 hex chars, compared with `hmac.compare_digest`. Rejected if older than 300 seconds or already seen. Payload bodies can be signed the same way over canonical JSON.
2. **Rate limit.** Per-platform sliding-window token bucket. Default capacity 60, refill 1 token/second.
3. **Treasury.** In-memory spend ceiling `AEOS_TREASURY_MAX` (default $1000). A call whose estimated cost would cross the ceiling is refused and counted.

A failed check writes `tier2_dead_letter_queue` and increments `aeos_sentinel_block_triggers_total{platform,reason}`. The sentinel also carries SSRF and loopback checks used by the security suite.

Job-page scraping uses `utils/http_client.py`, not the sentinel. The sentinel is the guard for paid platform APIs.

---

## 8. Security controls

| Control | Where |
|---------|--------|
| Secrets only in `.env` (mounted read-only in Compose) | `config/settings.py` |
| Parameterized SQL (`?` in SQLite, `$1` in asyncpg) | both database layers |
| SQLite WAL + foreign keys | `database/database.py` |
| Circuit breaker per source (5 failures, 60s open) | discovery + `website_status` |
| Retry with exponential backoff (1s–10s, 3 tries) | `HttpClient` |
| Concurrency cap | `asyncio.Semaphore(CONCURRENT_SCRAPERS)`, TCP connector limit 20 |
| HMAC idempotency and 5-minute replay window | `IdempotencyKeyManager` |
| Treasury hard brake | `TreasuryHardBrake` |
| Dead-letter capture of rejected calls | Postgres DLQ |
| TLS for outbound HTTP | aiohttp default + image `ca-certificates` |

Test suite mirrors this with markers: `security` (injection, XSS, SSRF, command injection, HMAC, rate limit, treasury, DLQ, telemetry), `property_based` (ledger invariant `net_profit = revenue − costs` under Hypothesis), `integration` (real Postgres and Redis via testcontainers). Fast run: `pytest tests/ -m "not integration"`.

---

## 9. Deployment and operations

### Process modes

```
python main.py --setup     # create SQLite tables
python main.py --once      # one discovery cycle, exit 1 if any scraper error
python main.py --stats     # job / notified / pending / cycle counts
python main.py             # scheduler forever
python main.py --aeos      # scheduler + AE-OS backbone
python main.py --env x.env # alternate env file
```

### Containers

`docker compose up --build -d` starts:

| Service | Image | Host ports |
|---------|-------|------------|
| ai-opportunity-finder | local Dockerfile | 9090 metrics |
| redis | redis:7-alpine | 6379 |
| postgres | postgres:16-alpine | 5432 (`aeos` / `aeos_password` / `aeos_episodic`) |
| neo4j | neo4j:5-community | 7474 HTTP, 7687 Bolt (`neo4j` / `aeos_neo4j`) |
| qdrant | qdrant/qdrant | 6333 REST, 6334 gRPC |
| prometheus | prom/prometheus | 9091 → container 9090 |

Volumes: `./data`, `./logs`, `./.env` (read-only), plus named volumes for each data service. App healthcheck opens the SQLite file. A single cycle inside Compose: `docker compose run --rm ai-opportunity-finder python main.py --once`.

Compose injects `AEOS_ENABLED=true` and the in-network DSNs (`redis`, `postgres`, `neo4j`, `qdrant`).

### Signals

Both the scheduler and the orchestrator register `SIGINT` and `SIGTERM`, shut their own tasks down, and exit. On Windows the signal registration is skipped when the loop refuses it.

### Metrics worth graphing

`aeos_task_processing_latency_seconds`, `aeos_task_processing_total`, `aeos_context_memory_size`, `aeos_context_key_count`, `aeos_redis_expired_events_total`, `aeos_neo4j_node_creations_total`, `aeos_neo4j_sync_latency_seconds`, `aeos_sentinel_block_triggers_total`, `aeos_treasury_balance`, `aeos_ppo_reward_score`, `aeos_ppo_policy_updates_total`.

---

## 10. Configuration surface

Everything is an environment variable, read once by `get_settings()`.

| Group | Keys | Default |
|-------|------|---------|
| Mail | `EMAIL`, `EMAIL_PASSWORD`, `SMTP_SERVER`, `SMTP_PORT` | Gmail, 587 |
| Clock | `CHECK_INTERVAL`, `CRON_SCHEDULE` | 60 min, `*/60 * * * *` |
| Product DB | `DATABASE_PATH` | `data/opportunities.db` |
| Logs | `LOG_LEVEL`, `LOG_FILE` | INFO, `logs/ai_opportunity_finder.log` |
| Channels | `ENABLE_TELEGRAM`, `TELEGRAM_*`, `ENABLE_DISCORD`, `DISCORD_WEBHOOK_URL` | off |
| HTTP | `REQUEST_TIMEOUT`, `MAX_RETRIES`, `CONCURRENT_SCRAPERS`, `RATE_LIMIT_DELAY`, `CACHE_TTL` | 30, 3, 5, 1.0, 300 |
| Rank | `RANKING_WEIGHT_*` | see §6, must sum to 1 |
| Alert floor | `MIN_SCORE_FOR_NOTIFICATION` | 50 |
| Circuits | `CIRCUIT_BREAKER_FAILURE_THRESHOLD`, `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | 5, 60 |
| AE-OS | `AEOS_ENABLED`, `AEOS_REDIS_URL`, `AEOS_POSTGRES_*`, `AEOS_NEO4J_*`, `AEOS_QDRANT_*`, `AEOS_METRICS_PORT`, `AEOS_IDEMPOTENCY_SECRET`, `AEOS_TREASURY_MAX` | off, localhost defaults, port 9090, max $1000 |

---

## 11. Request path, end to end

A listing that becomes an email:

1. APScheduler fires `_execute_cycle`.
2. Discovery asks the registry for eight scraper instances.
3. Each scraper borrows the shared `HttpClient` (semaphore 5, connector 20, timeout 30s, three retries).
4. HTML becomes `Job` objects. Id = `sha256(source + ":" + url)[:16]`.
5. SQLite `job_exists` filters repeats.
6. `RankingAgent.score` applies the eight weights.
7. `insert_job` writes the row with that score.
8. If score ≥ 50, `Notifier.send` tries email, then Telegram, then Discord, and `mark_notified` records the channels that succeeded.
9. `complete_execution` stamps duration and counts.
10. In parallel, if AE-OS is on, the 60-second loop ticks the Neo4j counter, samples a PPO action, and every five minutes writes a reward row. Redis expiries, when keys exist, land in Postgres and Qdrant on their own task.

---

## 12. Known shape of the build

These are facts about how the modules connect today, useful before you extend them.

- Discovery and AE-OS are siblings. Turning AE-OS on does not change which jobs are stored or mailed.
- `intelligence.ComprehensiveScorer` is complete and unused by the cycle. Ranking is the only live score.
- PPO observes a synthetic state derived from the heartbeat counter. Action `reset_circuits` logs; the other seven actions return without mutating scraper settings.
- Qdrant vectors are hash embeddings of width 384, not a sentence-transformer model.
- Neo4j degrades to a logging mock when the driver package is absent, so the sync loop still completes.
- Job scraping is not wrapped by the API Sentinel. The sentinel guards the four enumerated commerce platforms.
- There is no web UI and no public HTTP API for jobs. The only HTTP servers are Prometheus on 9090 and the data services in Compose.
