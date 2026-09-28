"""
Scraper for TELUS Digital (formerly TELUS International) — AI data annotation platform.
"""

from __future__ import annotations

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class TelusScraper(BaseScraper):
    """Scrapes TELUS Digital AI careers."""

    name = "TELUS Digital"
    source = "telus"
    base_url = "https://www.telusdigital.com"
    JOBS_URL = "https://www.telusdigital.com/careers"

    async def scrape(self) -> list[Job]:
        """Scrape TELUS Digital job listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            job_links = selector.css(
                "a[href*='career'], a[href*='job'], a[href*='position'], "
                "div[class*='job-card'] a, li[class*='job'] a"
            )

            seen_titles: set[str] = set()
            for link in job_links[:40]:
                try:
                    title = link.css("::text").get("")
                    if not title or title.strip() in seen_titles:
                        continue
                    seen_titles.add(title.strip())

                    href = link.css("::attr(href)").get("")
                    full_url = (
                        href if href and href.startswith("http")
                        else f"{self.base_url}{href}" if href
                        else self.base_url
                    )

                    tags = ["ai", "annotation", "data", "telus"]
                    if any(kw in title.lower() for kw in ["translation", "linguist", "language"]):
                        tags.append("translation")

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="TELUS Digital",
                        url=full_url,
                        source=self.source,
                        remote=True,
                        tags=tags,
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing TELUS link: %s", exc)
                    continue

            logger.info("TELUS Digital: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("TELUS Digital scrape failed: %s", exc)

        return jobs