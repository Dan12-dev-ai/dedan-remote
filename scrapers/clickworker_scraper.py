"""
Scraper for Clickworker (https://www.clickworker.com) — microtask platform.
"""

from __future__ import annotations

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class ClickworkerScraper(BaseScraper):
    """Scrapes Clickworker for microtask/AI opportunities."""

    name = "Clickworker"
    source = "clickworker"
    base_url = "https://www.clickworker.com"
    JOBS_URL = "https://www.clickworker.com/jobs"

    async def scrape(self) -> list[Job]:
        """Scrape Clickworker job listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            items = selector.css(
                "article[class*='job'], div[class*='job'], "
                "div[class*='post'], li[class*='job'], "
                "a[href*='job'], a[href*='task']"
            )

            for item in items[:30]:
                try:
                    title = item.css(
                        "h2::text, h3::text, h4::text, "
                        "[class*='title']::text, a::text"
                    ).get("")
                    if not title:
                        continue

                    href = item.css("a::attr(href)").get("") or ""
                    full_url = (
                        href if href.startswith("http")
                        else f"{self.base_url}{href}"
                    )

                    tags = ["microtask", "annotation"]
                    if any(kw in title.lower() for kw in ["ai", "artificial intelligence"]):
                        tags.append("ai")
                    if any(kw in title.lower() for kw in ["translat", "language"]):
                        tags.append("translation")

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="Clickworker",
                        url=full_url or self.base_url,
                        source=self.source,
                        remote=True,
                        tags=tags,
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing Clickworker item: %s", exc)
                    continue

            logger.info("Clickworker: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("Clickworker scrape failed: %s", exc)

        return jobs