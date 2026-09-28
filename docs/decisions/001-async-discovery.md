# ADR 001: Asynchronous Concurrency for Opportunity Scrapers

## Status
Accepted

## Context
DEDAN Remote continuously monitors 9 remote data annotation and AI evaluation career platforms. Running HTTP scrapers serially causes unacceptable discovery latency (> 120 seconds per cycle), while unrestricted unbounded threads risk overwhelming host system memory, tripping upstream Cloudflare rate limits, or triggering IP bans.

## Decision
We utilize Python's native `asyncio` event loop coupled with `aiohttp` and an asynchronous `asyncio.Semaphore` (bounded to 5 concurrent workers by default). Each scraper subclass implements an asynchronous `scrape()` coroutine returning normalized `Job` instances.

## Consequences
- **Positive**: Total discovery cycle execution drops from ~120 seconds to under 25 seconds across all 9 platforms.
- **Positive**: Upstream endpoints are respected with structured timeout limits (`REQUEST_TIMEOUT=30`) and randomized polite delays.
- **Negative**: Scrapers requiring dynamic JavaScript client rendering cannot rely solely on simple async HTTP fetches and require fallback selectors or headless browser hooks.
