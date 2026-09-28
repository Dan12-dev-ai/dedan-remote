# Data Models & Storage Topology — DEDAN Remote

DEDAN Remote employs a hybrid persistence architecture tailored to separate product catalog workloads, user application state, and optional experimental agent memory.

---

## 1. Storage Layers Overview

| Store | Engine | Primary Responsibilities | Lifecycle / Mode |
|---|---|---|---|
| **Opportunities Ledger** | SQLite (`data/opportunities.db`) | Scraped jobs catalog, deduplication records, execution cycle history, per-source website status / circuit states. | Always active. Default product database. |
| **User & State DB** | SQLite (`data/dedan_users.db`) | User accounts (bcrypt hashes), auth sessions, saved job bookmarks, application tracker records, user profile preferences. | Always active. Dedicated user database. |
| **PostgreSQL** | PostgreSQL 16 (Async via `asyncpg`) | High-concurrency job storage, structured search queries, and episodic task execution records for AE-OS. | Optional / Production VM. Activated when configured. |
| **Redis** | Redis 7 | Working memory, Pub/Sub event distribution, rate-limit token buckets, API idempotency cache. | Optional (Core component of AE-OS). |
| **Qdrant** | Qdrant Vector DB | Vector embeddings for semantic job matching and skill similarity search. | Optional (AE-OS semantic tier). |
| **Neo4j** | Neo4j Graph DB | Knowledge graph of skills, platforms, candidate profiles, and market relationship graphs. | Optional (AE-OS relational graph tier). |

---

## 2. Core Entities & Schemas

### SQLite: Opportunities Ledger (`data/opportunities.db`)

#### Table: `jobs`
- `id` (TEXT, PRIMARY KEY): Unique composite key (e.g. `telus-70231` or SHA-256 hash of URL).
- `title` (TEXT, NOT NULL): Cleaned job title.
- `company` (TEXT, NOT NULL): Platform or hiring organization.
- `source` (TEXT, NOT NULL): Registry source key (`outlier`, `telus`, etc.).
- `url` (TEXT, NOT NULL UNIQUE): Canonical external application URL.
- `description` (TEXT): Full job description text.
- `salary` (TEXT): Parsed compensation string (e.g., `$25.00/hr`).
- `location` (TEXT): Geographic location specification.
- `is_remote` (BOOLEAN): Flag indicating remote eligibility.
- `score` (REAL): Composite ranking score (0.0 to 100.0).
- `tags` (TEXT): JSON-encoded list of categorized tags.
- `discovered_at` (TIMESTAMP): UTC timestamp of discovery.
- `notified` (BOOLEAN): Boolean flag indicating notification dispatch status.

#### Table: `website_status`
- `source` (TEXT, PRIMARY KEY): Scraper identifier.
- `last_checked` (TIMESTAMP): UTC timestamp of latest cycle run.
- `status` (TEXT): `active`, `degraded`, or `open` (tripped circuit breaker).
- `consecutive_failures` (INTEGER): Failure count trigger for circuit breakers.
- `jobs_found` (INTEGER): Lifetime discovered jobs counter.

#### Table: `executions`
- `id` (INTEGER, PRIMARY KEY AUTOINCREMENT)
- `started_at` (TIMESTAMP)
- `completed_at` (TIMESTAMP)
- `jobs_found` (INTEGER)
- `jobs_new` (INTEGER)
- `duration_seconds` (REAL)
- `status` (TEXT): `success`, `partial`, or `failed`.
- `error_message` (TEXT)

---

## 3. SQLite: User & Session Database (`data/dedan_users.db`)

#### Table: `users`
- `id` (INTEGER, PRIMARY KEY AUTOINCREMENT)
- `email` (TEXT, UNIQUE NOT NULL)
- `hashed_password` (TEXT, NOT NULL): Bcrypt-hashed password string.
- `full_name` (TEXT)
- `created_at` (TIMESTAMP)

#### Table: `saved_jobs`
- `user_id` (INTEGER, FOREIGN KEY -> users.id)
- `job_id` (TEXT, NOT NULL)
- `saved_at` (TIMESTAMP)
- PRIMARY KEY (`user_id`, `job_id`)

#### Table: `applications`
- `id` (TEXT, PRIMARY KEY): UUID tracker key.
- `user_id` (INTEGER, FOREIGN KEY -> users.id)
- `job_id` (TEXT, NOT NULL)
- `status` (TEXT): State enum: `saved`, `applied`, `interviewing`, `offered`, `rejected`.
- `notes` (TEXT)
- `applied_date` (TIMESTAMP)
- `updated_at` (TIMESTAMP)

#### Table: `user_profiles`
- `user_id` (INTEGER, PRIMARY KEY, FOREIGN KEY -> users.id)
- `target_skills` (TEXT): JSON array of skill strings.
- `experience_level` (TEXT): `entry`, `mid`, `senior`.
- `min_hourly_rate` (REAL)
- `preferred_sources` (TEXT): JSON array of preferred source IDs.
