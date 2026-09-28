# Changelog

All notable changes to DEDAN Remote will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-09-28

### Added
- **FastAPI Public REST Layer**:
  - Full OpenAPI 3.1 schema and interactive documentation (`/api/docs`, `/api/openapi.json`).
  - Canonical routes (`/api/*`) and versioned aliases (`/api/v1/*`) for jobs, sources, categories, search, and health.
  - User authentication system with bcrypt password hashing and HS256 JWT sessions (`/api/auth/*`).
  - User state persistence: saved jobs (`/api/saved`), job application lifecycle tracking (`/api/applications`), and personalized profile preference store (`/api/profile`).
  - Structured error envelope handling (`detail`, `code`, `status`, `request_id`, `path`) and security middleware (Strict-Transport-Security, CSP, X-Frame-Options, X-Content-Type-Options).
- **Single Page Application (React 18 + Vite + TypeScript)**:
  - Responsive search interface, category browsing, multi-criteria filtering (remote, source, score thresholds), and pagination.
  - Job detail views, application status boards, and profile management.
  - Static distribution build integrated directly into FastAPI SPA static fallback routing.
- **Discovery Pipeline & Scrapers**:
  - Modular scraper registry supporting 9 remote data annotation and AI training platforms:
    - Outlier (`outlier`)
    - Alignerr (`alignerr`)
    - OneForma (`oneforma`)
    - TELUS Digital (`telus`)
    - Welocalize (`welocalize`)
    - Appen (`appen`)
    - DataAnnotation (`dataannotation`)
    - Clickworker (`clickworker`)
    - Toloka (`toloka`)
  - Resilient HTTP fetching using `aiohttp` with exponential backoff retries, user-agent rotation, per-source circuit breakers, and title/URL deduplication.
- **Intelligence & Scoring Subsystem**:
  - Deterministic multi-dimensional scoring pipeline (`intelligence/scorer.py`):
    - Geographic & payment eligibility evaluation (`intelligence/eligibility.py`)
    - Keyword & taxonomy skill matching (`intelligence/skill_matcher.py`)
    - Requirement complexity & difficulty estimator (`intelligence/difficulty_estimator.py`)
    - Success probability predictor (`intelligence/success_predictor.py`)
- **Experimental AE-OS Control Plane**:
  - In-memory Proximal Policy Optimization (PPO) reinforcement learning engine (`core/ppo_engine.py`) with discrete action allocations.
  - Multi-tier memory model: Redis working memory (`core/redis_listener.py`), PostgreSQL episodic store (`database/postgres_database.py`), Neo4j graph synchronization (`core/neo4j_sync.py`), and Qdrant semantic storage stubs.
  - API Sentinel (`core/api_sentinel.py`) enforcing HMAC idempotency, rate limiting, and parameter validation.
- **Comprehensive Test Infrastructure**:
  - 350 total tests covering unit tests, API contracts, scrapers, database roundtrips, notification dispatchers, property-based invariants (Hypothesis), and containerized integration mocks.
- **Containerization & CI**:
  - Multi-stage production `Dockerfile` building both the Node.js React frontend and the Python FastAPI runtime.
  - `docker-compose.yml` service orchestration for local development and self-hosted deployments.
  - GitHub Actions CI pipeline executing automated linting, type checks, unit test suites, and Docker build validations.

### Changed
- Standardized product identity to **DEDAN Remote** across all configuration files, environment definitions, schemas, and documentation.
- Decoupled normal product execution from optional AE-OS infrastructure (defaulting `AEOS_ENABLED=false`).
- Consolidated documentation to reflect implemented technical architecture truthfully.
