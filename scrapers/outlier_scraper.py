"""
Scraper for Outlier (https://outlier.ai) — AI training & evaluation platform.
"""

from __future__ import annotations

from typing import Optional

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class OutlierScraper(BaseScraper):
    """Scrapes Outlier for AI training/evaluation opportunities."""

    name = "Outlier"
    source = "outlier"
    base_url = "https://outlier.ai"

    # Outlier uses a headless-friendly job board
    JOBS_URL = "https://outlier.ai/jobs"

    async def scrape(self) -> list[Job]:
        """Scrape Outlier job listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            html = await client.fetch_html(self.JOBS_URL)
            selector = Selector(text=html)

            # Outlier job cards
            job_cards = selector.css("div.job-card, article.job, div[class*='job']")
            if not job_cards:
                # Fallback: try generic listing patterns
                job_cards = selector.css("a[href*='apply'], a[href*='career']")

            for card in job_cards[:50]:
                try:
                    title = card.css("h2::text, h3::text, [class*='title']::text").get("")
                    if not title:
                        continue

                    url_path = card.css("a::attr(href)").get("")
                    if not url_path:
                        continue

                    full_url = url_path if url_path.startswith("http") else f"{self.base_url}{url_path}"
                    company = card.css("[class*='company']::text, [class*='org']::text").get("Outlier")
                    salary = card.css("[class*='salary']::text, [class*='pay']::text").get("")
                    location = card.css("[class*='location']::text, [class*='loc']::text").get("")
                    description = card.css("p::text, [class*='desc']::text").get("")

                    tags = []
                    tag_els = card.css("[class*='tag']::text, [class*='badge']::text").getall()
                    tags = [t.strip() for t in tag_els if t.strip()]

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company=company.strip() if company else "Outlier",
                        url=full_url,
                        source=self.source,
                        salary=salary.strip() if salary else None,
                        country=None,  # Outlier is worldwide
                        remote=True,
                        description=description.strip() if description else None,
                        tags=tags or ["ai", "training", "evaluation"],
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing Outlier card: %s", exc)
                    continue

            logger.info("Outlier: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("Outlier scrape failed: %s", exc)

        return jobs