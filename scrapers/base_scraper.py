"""
Base scraper class that all website scrapers must extend.
Defines the interface and provides common utilities.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from models.job import Job
from utils.http_client import HttpClient


class BaseScraper(ABC):
    """
    Abstract base class for all job scrapers.

    Each scraper targets one website/platform and must implement
    ``async def scrape() -> list[Job]``.

    Subclasses should set:
      - ``name``: human-readable name (e.g. "Outlier")
      - ``source``: short identifier (e.g. "outlier")
      - ``base_url``: root URL of the platform
    """

    name: str = ""
    source: str = ""
    base_url: str = ""

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must set `name`")
        if not self.source:
            raise ValueError(f"{type(self).__name__} must set `source`")
        if not self.base_url:
            raise ValueError(f"{type(self).__name__} must set `base_url`")
        self._client: Optional[HttpClient] = None

    async def _get_client(self) -> HttpClient:
        """Lazy-load the shared HTTP client."""
        if self._client is None:
            from utils.http_client import get_http_client

            self._client = await get_http_client()
        return self._client

    @abstractmethod
    async def scrape(self) -> list[Job]:
        """
        Scrape the target website for job opportunities.

        Returns:
            A list of standardized Job objects.
            Return an empty list on failure — never raise.
        """
        ...

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} source={self.source!r}>"
