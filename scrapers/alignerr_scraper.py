"""
Scraper for Alignerr — AI training and data labeling platform.
"""

from __future__ import annotations

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class AlignerrScraper(BaseScraper):
    """Scrapes Alignerr for AI training opportunities."""

    name = "Alignerr"
    source = "alignerr"
    base_url = "https://alignerr.com"
    JOBS_URL = "https://alignerr.com/careers"

    async def scrape(self) -> list[Job]:
        """Scrape Alignerr job listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            listing_items = selector.css(
                "a[href*='career'], a[href*='job'], "
                "div[class*='position'], div[class*='role'], "
                "li[class*='job'], tr[class*='job']"
            )

            for item in listing_items[:30]:
                try:
                    title = item.css(
                        "h2::text, h3::text, h4::text, "
                        "[class*='title']::text, [class*='name']::text, "
                        "a::text"
                    ).get("")
                    if not title:
                        continue

                    url_path = (
                        item.css("a::attr(href)").get("") or
                        item.css("[class*='apply']::attr(href)").get("") or
                        ""
                    )
                    full_url = (
                        url_path if url_path.startswith("http")
                        else f"{self.base_url}{url_path}"
                    )

                    tags = ["ai", "training", "labeling", "evaluation"]

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="Alignerr",
                        url=full_url or self.base_url,
                        source=self.source,
                        remote=True,
                        tags=tags,
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing Alignerr item: %s", exc)
                    continue

            logger.info("Alignerr: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("Alignerr scrape failed: %s", exc)

        return jobs