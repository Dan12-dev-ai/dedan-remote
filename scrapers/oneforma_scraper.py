"""
Scraper for OneForma (https://oneforma.com) — AI data annotation platform.
"""

from __future__ import annotations

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class OneFormaScraper(BaseScraper):
    """Scrapes OneForma for AI annotation/translation opportunities."""

    name = "OneForma"
    source = "oneforma"
    base_url = "https://www.oneforma.com"
    JOBS_URL = "https://www.oneforma.com/jobs"

    async def scrape(self) -> list[Job]:
        """Scrape OneForma job listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            # OneForma job rows/cards
            job_rows = selector.css(
                "div[class*='job'], tr[class*='job'], a[href*='job'], div[class*='position']"
            )

            for row in job_rows[:40]:
                try:
                    title = row.css(
                        "h2::text, h3::text, [class*='title']::text, a::text, strong::text"
                    ).get("")
                    if not title:
                        continue

                    url_path = row.css("a::attr(href)").get("") or ""
                    full_url = (
                        url_path if url_path.startswith("http") else f"{self.base_url}{url_path}"
                    )

                    location = row.css(
                        "[class*='location']::text, "
                        "[class*='country']::text, "
                        "span:nth-child(2)::text"
                    ).get("")
                    category = row.css("[class*='category']::text, [class*='type']::text").get("")

                    tags = ["annotation", "ai", "data"]
                    if category:
                        tags.append(category.strip().lower())

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="OneForma",
                        url=full_url or self.base_url,
                        source=self.source,
                        country=location.strip() if location else None,
                        remote=True,
                        tags=tags,
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing OneForma row: %s", exc)
                    continue

            logger.info("OneForma: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("OneForma scrape failed: %s", exc)

        return jobs
