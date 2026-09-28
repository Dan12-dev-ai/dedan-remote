# ADR 002: Per-Source Circuit Breakers for Scraping Resilience

## Status
Accepted

## Context
External career portals frequently undergo maintenance, change DOM structures, introduce anti-bot challenges, or suffer transient outages. In a naive loop, repetitive scraping of a failing target wastes compute cycles, pollutes application logs with stack traces, and delays healthy scraper execution.

## Decision
We implement a per-source circuit breaker tracked in the SQLite `website_status` table:
- If a platform scraper fails consecutively 3 times, its circuit trips to `open`.
- When `open`, subsequent discovery cycles skip that platform immediately without initiating outbound HTTP connections.
- A 30-minute cooldown window resets the state to `half-open`, allowing a single probe request to check if the upstream service has recovered.

## Consequences
- **Positive**: Complete discovery cycles continue uninterrupted even if 1 or 2 platforms are offline.
- **Positive**: Reduces wasteful network egress and prevents aggressive retries against dead servers.
- **Negative**: Temporary platform hiccups may cause listings posted during the 30-minute cooldown to be detected on the subsequent discovery cycle rather than immediately.
