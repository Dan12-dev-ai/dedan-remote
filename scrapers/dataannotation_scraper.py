"""
Scraper for DataAnnotation (https://dataannotation.tech) — AI training platform.
"""

from __future__ import annotations

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class DataAnnotationScraper(BaseScraper):
    """Scrapes DataAnnotation for AI training/evaluation opportunities."""

    name = "DataAnnotation"
    source = "dataannotation"
    base_url = "https://www.dataannotation.tech"
    JOBS_URL = "https://www.dataannotation.tech/opportunities"

    async def scrape(self) -> list[Job]:
        """Scrape DataAnnotation opportunities."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            items = selector.css(
                "div[class*='opportunity'], div[class*='job'], "
                "a[href*='project'], div[class*='task'], "
                "div[class*='listing']"
            )

            for item in items[:30]:
                try:
                    title = item.css(
                        "h2::text, h3::text, [class*='title']::text, "
                        "strong::text, a::text"
                    ).get("")
                    if not title:
                        continue

                    href = item.css("a::attr(href)").get("") or ""
                    full_url = (
                        href if href.startswith("http")
                        else f"{self.base_url}{href}"
                    )
                    pay = item.css(
                        "[class*='pay']::text, [class*='rate']::text, "
                        "[class*='salary']::text, [class*='price']::text"
                    ).get("")

                    tags = ["ai", "training", "annotation", "rhlf"]
                    if any(kw in title.lower() for kw in ["coding", "code", "programming"]):
                        tags.append("coding")
                    if any(kw in title.lower() for kw in ["writing", "writer", "creative"]):
                        tags.append("writing")

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="DataAnnotation",
                        url=full_url or self.base_url,
                        source=self.source,
                        salary=pay.strip() if pay else None,
                        remote=True,
                        tags=tags,
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing DataAnnotation item: %s", exc)
                    continue

            logger.info("DataAnnotation: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("DataAnnotation scrape failed: %s", exc)

        return jobs