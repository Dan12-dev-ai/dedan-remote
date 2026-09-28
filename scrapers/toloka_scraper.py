"""
Scraper for Toloka (https://toloka.ai) — crowdsourcing platform for data labeling and AI training tasks.
"""

from __future__ import annotations

from typing import Optional

from parsel import Selector

from models.job import Job, job_from_scraper_result
from scrapers.base_scraper import BaseScraper
from utils.logger import get_logger

logger = get_logger(__name__)


class TolokaScraper(BaseScraper):
    """Scrapes Toloka for data labeling and AI training opportunities."""

    name = "Toloka"
    source = "toloka"
    base_url = "https://toloka.ai"

    # Toloka has a public task marketplace
    TASKS_URL = "https://toloka.ai/tasks"

    # Public pages that list crowd-sourcing / partner opportunities
    CATALOG_URLS = [
        "https://toloka.ai/tasks",
        "https://toloka.ai/partners/",
    ]

    async def scrape(self) -> list[Job]:
        """Scrape Toloka task listings."""
        jobs: list[Job] = []
        try:
            client = await self._get_client()
            selector: Optional[Selector] = None

            # Fetch the first URL that returns parseable HTML
            for url in self.CATALOG_URLS:
                try:
                    html = await client.fetch_html(url)
                    selector = Selector(text=html)
                    break
                except Exception as exc:
                    logger.warning("Toloka: failed to fetch %s: %s", url, exc)
                    continue

            if selector is None:
                logger.error("Toloka scrape failed: no page could be fetched")
                return []

            # Toloka task cards — try structured selectors first,
            # then fall back to generic listing patterns.
            task_cards = selector.css(
                "div[class*='task-card'], article[class*='task'], "
                "div[class*='task'], li[class*='task']"
            )
            if not task_cards:
                task_cards = selector.css("a[href*='/tasks/'], a[href*='toloka.ai/']")

            for card in task_cards[:50]:
                try:
                    title = card.css(
                        "h2::text, h3::text, h4::text, "
                        "[class*='title']::text, [class*='name']::text"
                    ).get("")
                    if not title:
                        # Card may itself be an anchor with inline text
                        title = card.css("::text").get("").strip()
                    if not title:
                        continue

                    url_path = (
                        card.css("a::attr(href)").get("") or card.attrib.get("href", "") or ""
                    )
                    if not url_path:
                        continue

                    full_url = (
                        url_path if url_path.startswith("http") else f"{self.base_url}{url_path}"
                    )

                    reward = card.css(
                        "[class*='reward']::text, [class*='price']::text, "
                        "[class*='pay']::text, [class*='amount']::text"
                    ).get("")
                    description = card.css(
                        "p::text, [class*='desc']::text, [class*='hint']::text"
                    ).get("")
                    tag_els = card.css(
                        "[class*='tag']::text, [class*='badge']::text, [class*='skill']::text"
                    ).getall()
                    tags = [t.strip() for t in tag_els if t.strip()]

                    job = job_from_scraper_result(
                        title=title.strip(),
                        company="Toloka",
                        url=full_url,
                        source=self.source,
                        salary=reward.strip() if reward else None,
                        country=None,  # Toloka is worldwide
                        remote=True,
                        description=description.strip() if description else None,
                        tags=tags or ["ai", "data labeling", "crowdsourcing"],
                    )
                    jobs.append(job)
                except Exception as exc:
                    logger.warning("Error parsing Toloka card: %s", exc)
                    continue

            logger.info("Toloka: found %d jobs", len(jobs))
        except Exception as exc:
            logger.error("Toloka scrape failed: %s", exc)

        return jobs
