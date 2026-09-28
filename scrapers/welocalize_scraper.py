"""
Scraper for Welocalize — AI data annotation and translation platform.
"""

from __future__ import annotations

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class WelocalizeScraper(BaseScraper):
    """Scrapes Welocalize careers for AI opportunities."""

    name = "Welocalize"
    source = "welocalize"
    base_url = "https://www.welocalize.com"
    JOBS_URL = "https://www.welocalize.com/careers"

    async def scrape(self) -> list[Job]:
        """Scrape Welocalize job listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            job_items = selector.css(
                "div[class*='job'], a[href*='job'], div[class*='position'], li[class*='job']"
            )

            for item in job_items[:30]:
                try:
                    title = item.css(
                        "h2::text, h3::text, h4::text, [class*='title']::text, a::text"
                    ).get("")
                    if not title:
                        continue

                    href = item.css("a::attr(href)").get("") or ""
                    full_url = href if href.startswith("http") else f"{self.base_url}{href}"

                    tags = ["ai", "annotation", "translation"]
                    if any(kw in title.lower() for kw in ["linguist", "localization", "translat"]):
                        tags.append("linguist")

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="Welocalize",
                        url=full_url or self.base_url,
                        source=self.source,
                        remote=True,
                        tags=tags,
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing Welocalize item: %s", exc)
                    continue

            logger.info("Welocalize: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("Welocalize scrape failed: %s", exc)

        return jobs
