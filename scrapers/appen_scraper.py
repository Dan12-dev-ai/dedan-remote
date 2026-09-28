"""
Scraper for Appen (https://appen.com) — AI data annotation platform.
"""

from __future__ import annotations

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class AppenScraper(BaseScraper):
    """Scrapes Appen for AI annotation/RLHF opportunities."""

    name = "Appen"
    source = "appen"
    base_url = "https://www.appen.com"
    JOBS_URL = "https://www.appen.com/jobs"

    async def scrape(self) -> list[Job]:
        """Scrape Appen job listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            job_cards = selector.css(
                "div[class*='job'], a[class*='job'], "
                "div[class*='card'], li[class*='job']"
            )

            for card in job_cards[:30]:
                try:
                    title = card.css(
                        "h2::text, h3::text, h4::text, "
                        "[class*='title']::text, a::text"
                    ).get("")
                    if not title:
                        continue

                    href = card.css("a::attr(href)").get("") or ""
                    full_url = (
                        href if href.startswith("http")
                        else f"{self.base_url}{href}"
                    )

                    tags = ["ai", "annotation", "labeling"]
                    if any(kw in title.lower() for kw in ["search", "evaluator", "rater"]):
                        tags.append("search evaluation")
                    if any(kw in title.lower() for kw in ["linguist", "translat"]):
                        tags.append("linguist")

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="Appen",
                        url=full_url or self.base_url,
                        source=self.source,
                        remote=True,
                        tags=tags,
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing Appen card: %s", exc)
                    continue

            logger.info("Appen: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("Appen scrape failed: %s", exc)

        return jobs