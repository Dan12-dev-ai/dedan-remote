# Testing Guide & Verification Architecture — DEDAN Remote

This document details the complete testing strategy, test layers, and verification commands for the DEDAN Remote system.

---

## 1. Test Suite Summary

The repository contains **357 tests** organized across four primary verification layers:

```
tests/
├── test_api.py                           # 83 tests: FastAPI REST endpoints, auth, jobs, saved/applications
├── test_cache_redis_layer.py             # 17 tests: Redis cache layer behaviour (container-gated)
├── test_database.py                      # 22 tests: SQLite ledger, deduplication, property invariants
├── test_email.py                         # 7 tests: SMTP email formatting & error handling
├── test_financial_ledger_property_based.py # 5 tests: Hypothesis ledger conservation laws
├── test_integration_docker.py            # 22 tests: Docker container integration (PostgreSQL, Redis)
├── test_intelligence.py                  # 45 tests: Eligibility, skill matching, difficulty, predictor
├── test_models.py                        # 8 tests: Job dataclass creation and behaviour
├── test_notifications.py                 # 22 tests: Telegram, Discord, multi-channel dispatch
├── test_postgres_database.py             # 30 tests: Async PostgreSQL methods & model conversion
├── test_ranking.py                       # 8 tests: Deterministic scoring rules & threshold weights
├── test_redis_keyspace_listener.py       # 3 tests: Tier-1 key expiry → distillation, vector sync
├── test_scheduler.py                     # 4 tests: Discovery cycle scheduler & event loop
├── test_scrapers.py                      # 43 tests: 9 platform parsers, contracts, fallback URLs
└── test_security_penetration.py          # 38 tests: Sentinel HMAC idempotency, DLQ, input checks
```

Counts are collected-test counts (`pytest --collect-only`, 2026-09-28); parametrised
tests expand, so a file's count exceeds its number of `def test_` functions.

---

## 2. Test Execution Commands

### Fast Unit & Contract Tests (No Docker Required)
Executes all functional tests, API route validation, scraper parsers, and deterministic intelligence rules:
```bash
python3 -m pytest tests/ -v -m "not integration and not property_based"
```
*Current status: 276 passed, 27 skipped (container-gated integration tests), 54 deselected in ~21 seconds.*

**The discovery database is provisioned by the suite.** `tests/conftest.py` points
`DATABASE_PATH` at a throwaway SQLite file (`$TMPDIR/dedan_test_opportunities.db`),
creates the engine's schema through the engine's own `Database.create_tables()`, and
seeds three representative jobs before the first test runs. The API tests therefore
need no developer `data/opportunities.db`; without this provisioning their absence
in CI made every `/api/jobs` call return HTTP 500.

### Property-Based Invariant Tests (Hypothesis)
Validates financial ledger conservation laws and database state invariants across 1,000+ pseudo-random permutations:
```bash
python3 -m pytest tests/ -v -m "property_based"
```
*Current status: 7 passed in ~42 seconds.*

### Run Specific Test Modules
```bash
# Verify API routes and authentication
python3 -m pytest tests/test_api.py -v

# Verify Scraper parsers
python3 -m pytest tests/test_scrapers.py -v

# Verify Intelligence and scoring logic
python3 -m pytest tests/test_intelligence.py tests/test_ranking.py -v
```

### Full Integration Tests with Docker Containers
Integration tests require a running Docker daemon to spin up ephemeral Postgres and Redis containers via `testcontainers`:
```bash
python3 -m pytest tests/ -v -m "integration"
```

---

## 3. Frontend Build & Type Validation

The React 18 single-page application is verified using TypeScript compiler checks and Vite bundling:
```bash
cd frontend
npm ci
npm run build
```
Build output produces bundled production assets in `frontend/dist/`.

---

## 4. Continuous Integration Checks

Every pull request and push to `master` runs GitHub Actions CI (`.github/workflows/ci.yml`), validating:
1. Python 3.11 & 3.12 compatibility
2. Code formatting and linting (`ruff check` + `ruff format --check`; rules pinned in `ruff.toml`)
3. Type checking with `mypy` (blocking; zero-error policy)
4. Automated pytest test execution with coverage reporting
5. Frontend TypeScript compile, Vite asset generation, and Vitest unit tests
6. Docker Compose syntax and multi-stage container build

### Reproducing the quality gates locally

Both gates are pinned by config files so a local run matches CI byte for byte:

```bash
# Lint + format (ruff.toml pins the rule set and line length)
ruff check . --exclude frontend,venv,.venv
ruff format --check . --exclude frontend,venv,.venv

# Types (mypy.ini pins the scope and flags)
mypy
```

`mypy` with no arguments reads `mypy.ini`, which covers all application code:
`api`, `agents`, `config`, `core`, `database`, `intelligence`, `main.py`,
`models`, `notifications`, `scrapers`, `utils` (62 source files are checked when
dependencies are installed). `tests/` is deliberately outside the type-checked
scope and is validated by pytest instead.

> **Important:** mypy only detects real errors when the project dependencies are
> installed (`pip install -r requirements.txt`). In a bare environment
> `fastapi`/`pydantic`/`python-telegram-bot` resolve to `Any`, third-party call
> signatures are unchecked, and errors such as an un-awaited coroutine or a
> `dict` passed where a model is declared are silently missed — the run reports
> "Success" even though CI fails. Always type-check inside the project
> environment.
