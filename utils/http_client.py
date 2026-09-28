"""
Async HTTP client with connection pooling, retries, rate limiting, and timeouts.
Uses aiohttp for high-performance concurrent requests.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiohttp import ClientSession, ClientTimeout, TCPConnector
from tenacity import (
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)


class HttpClient:
    """
    Reusable async HTTP client with connection pooling,
    retry logic, and configurable timeouts.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._timeout = ClientTimeout(total=settings.REQUEST_TIMEOUT)
        self._max_retries = settings.MAX_RETRIES
        self._rate_limit_delay = settings.RATE_LIMIT_DELAY
        self._session: Optional[ClientSession] = None
        self._semaphore = asyncio.Semaphore(settings.CONCURRENT_SCRAPERS)

    async def __aenter__(self) -> HttpClient:
        await self._get_session()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def _get_session(self) -> ClientSession:
        """Get or create the shared aiohttp session with connection pooling."""
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=20,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                force_close=False,
            )
            self._session = ClientSession(
                connector=connector,
                timeout=self._timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/json,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying session."""
        if self._session and not self._session.closed:
            await self._session.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_html(self, url: str, **kwargs: object) -> str:
        """
        Fetch HTML content from a URL with retry logic.

        Args:
            url: Target URL.
            **kwargs: Additional params passed to session.get().

        Returns:
            HTML content as string.

        Raises:
            aiohttp.ClientError: On HTTP or connection failure.
        """
        async with self._semaphore:
            session = await self._get_session()
            async with session.get(url, ssl=False, **kwargs) as response:  # type: ignore[arg-type]
                response.raise_for_status()
                text = await response.text(encoding="utf-8", errors="replace")
                logger.debug(
                    "Fetched %s — status %s, %d bytes",
                    url,
                    response.status,
                    len(text),
                )
                await asyncio.sleep(self._rate_limit_delay)
                return text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def fetch_json(
        self,
        url: str,
        **kwargs: object,
    ) -> dict[str, object] | list[dict[str, object]]:
        """
        Fetch JSON content from a URL with retry logic.

        Args:
            url: Target URL.
            **kwargs: Additional params passed to session.get().

        Returns:
            Parsed JSON data.
        """
        async with self._semaphore:
            session = await self._get_session()
            async with session.get(url, ssl=False, **kwargs) as response:  # type: ignore[arg-type]
                response.raise_for_status()
                data = await response.json()
                logger.debug(
                    "Fetched JSON %s — status %s",
                    url,
                    response.status,
                )
                await asyncio.sleep(self._rate_limit_delay)
                return data  # type: ignore[return-value]


# Module-level singleton for reuse
_http_client: Optional[HttpClient] = None


async def get_http_client() -> HttpClient:
    """Get or create the global HTTP client singleton."""
    global _http_client
    if _http_client is None:
        _http_client = HttpClient()
    return _http_client


async def close_http_client() -> None:
    """Close the global HTTP client."""
    global _http_client
    if _http_client:
        await _http_client.close()
        _http_client = None
