# Engineering Roadmap — DEDAN Remote

Development priorities for DEDAN Remote are organized across verified milestones, current operational focus, and future technical objectives.

---

## 1. Completed Milestones (v1.0.0)

- [x] **Asynchronous Discovery Engine**: Scraper framework utilizing `aiohttp` and `asyncio.Semaphore` bounded concurrency.
- [x] **Platform Scraper Registry**: Parsers for 9 data annotation and AI training job boards (`outlier`, `alignerr`, `oneforma`, `telus`, `welocalize`, `appen`, `dataannotation`, `clickworker`, `toloka`).
- [x] **Circuit Breaker Fault Tolerance**: Per-source consecutive failure counters with cooldown recovery.
- [x] **Multi-Criteria Deterministic Scoring**: Weighted 0–100 scoring based on geographic eligibility, skill matching, difficulty, and success probability.
- [x] **Multi-Channel Notification Dispatcher**: Parallel alerting via SMTP Email (HTML digest), Telegram bot API, and Discord webhooks.
- [x] **FastAPI REST API**: OpenAPI 3.1 contract, canonical (`/api/*`) and versioned (`/api/v1/*`) route aliases, structured error envelopes, and correlation request IDs.
- [x] **User Management & Session State**: Bcrypt credential hashing, bearer session tokens, saved bookmarks, and job application tracking.
- [x] **React 18 Single Page Application**: Production Vite bundle integrated directly into FastAPI SPA fallback routing.
- [x] **Test Infrastructure**: 350-test automated suite covering unit tests, contract mocks, and Hypothesis property-based invariants.
- [x] **Packaging**: Multi-stage production `Dockerfile` and `docker-compose.yml`.

---

## 2. Current Engineering Focus (Next)

- [ ] **Headless Browser Execution for Anti-Bot Portals**:
  - Integrate Playwright/Puppeteer support for JavaScript-rendered and Cloudflare-protected career listings.
- [ ] **Outbound Webhook Delivery Subsystem**:
  - Allow users to configure generic HTTP webhook URLs to receive job alerts in JSON format.
- [ ] **Slack Webhook Integration**:
  - Native Slack incoming webhook payload adapter alongside existing Discord and Telegram dispatchers.
- [ ] **Frontend Cypress / Playwright E2E Tests**:
  - Automate browser testing for candidate filtering, job card interactions, and application tracking workflows.

---

## 3. Future Technical Objectives (Planned)

- [ ] **Dynamic PPO Runtime Actuation**:
  - Fully couple the experimental PPO agent policy outputs with live asynchronous worker pool scaling and scrape interval backoff.
- [ ] **Semantic Vector Search (Qdrant)**:
  - Generate text embeddings for job listings and candidate profiles to enable fuzzy semantic search and vector similarity ranking.
- [ ] **Knowledge Graph Queries (Neo4j)**:
  - Model relationship graphs linking platform reliability, skill frequency, and payout distributions.
- [ ] **Distributed Multi-Node Scheduling**:
  - Migrate scheduler state and task locking from local APScheduler to Redis/Celery for horizontal multi-worker cluster deployments.

