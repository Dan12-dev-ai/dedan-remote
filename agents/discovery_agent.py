"""
Discovery Agent — orchestrates the scraping, deduplication, ranking,
and notification pipeline for a single execution cycle.
"""

from __future__ import annotations

import asyncio

from agents.ranking_agent import RankingAgent
from config.settings import get_settings
from database.database import Database
from models.job import Job
from notifications.notifier import Notifier
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_registry import get_registry
from utils.logger import get_logger

logger = get_logger(__name__)


class DiscoveryAgent:
    """
    Main orchestration agent.

    For each execution cycle:
      1. Loads all scrapers from the registry
      2. Runs them concurrently with per-scraper timeout
      3. Deduplicates against the database
      4. Scores each new job
      5. Stores in database
      6. Notifies on high-scoring jobs
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._db = Database()
        self._registry = get_registry()
        self._ranking = RankingAgent()
        self._notifier = Notifier()

    async def run_once(self) -> dict[str, int]:
        """
        Execute one full discovery cycle.

        Returns:
            Dict with execution stats.
        """
        execution_id = self._db.start_execution()
        logger.info("=" * 60)
        logger.info("Starting discovery cycle #%d", execution_id)
        logger.info("=" * 60)

        all_jobs: list[Job] = []
        errors: list[str] = []

        # ── 1. Discover scrapers ────────────────────────────────────────
        scrapers = self._registry.get_all()
        if not scrapers:
            logger.warning("No scrapers registered — nothing to do.")
            self._db.complete_execution(execution_id, 0, 0, 0, "No scrapers registered")
            return {"jobs_found": 0, "jobs_new": 0, "jobs_notified": 0}

        logger.info("Running %d scrapers concurrently...", len(scrapers))

        # ── 2. Run scrapers concurrently ────────────────────────────────
        async def scrape_with_timeout(scraper: BaseScraper) -> list[Job]:
            """Run a single scraper with timeout and error handling."""
            scraper_name = getattr(scraper, "name", str(scraper))
            source = getattr(scraper, "source", "unknown")
            try:
                # Check circuit breaker
                if self._db.is_circuit_open(source):
                    logger.warning("Circuit open for %s — skipping", scraper_name)
                    return []

                jobs = await asyncio.wait_for(
                    scraper.scrape(),
                    timeout=self._settings.REQUEST_TIMEOUT + 10,
                )
                self._db.update_website_status(source, success=True)
                logger.info("✓ %s: %d jobs found", scraper_name, len(jobs))
                return jobs
            except asyncio.TimeoutError:
                msg = f"{scraper_name} timed out after {self._settings.REQUEST_TIMEOUT + 10}s"
                logger.error(msg)
                errors.append(msg)
                self._db.update_website_status(source, success=False, error=msg)
                self._db.open_circuit(source, self._settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT)
                return []
            except Exception as exc:
                msg = f"{scraper_name} failed: {exc}"
                logger.error(msg)
                errors.append(msg)
                self._db.update_website_status(source, success=False, error=str(exc))
                # Open circuit after threshold failures
                conn = self._db.connect()
                row = conn.execute(
                    "SELECT consecutive_failures FROM website_status WHERE source = ?",
                    (source,),
                ).fetchone()
                if (
                    row
                    and row["consecutive_failures"]
                    >= self._settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD
                ):
                    self._db.open_circuit(source, self._settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT)
                    logger.warning(
                        "Circuit opened for %s after %d failures",
                        scraper_name,
                        row["consecutive_failures"],
                    )
                return []

        tasks = [scrape_with_timeout(s) for s in scrapers]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        for job_list in results:
            all_jobs.extend(job_list)

        logger.info("Total jobs found across all sources: %d", len(all_jobs))

        # ── 3. Deduplicate, score, store ────────────────────────────────
        new_jobs: list[Job] = []
        for job in all_jobs:
            if not self._db.job_exists(job.id):
                new_jobs.append(job)

        logger.info("New unique jobs: %d", len(new_jobs))

        # Score and store
        scored_jobs: list[tuple[Job, float]] = []
        for job in new_jobs:
            score = self._ranking.score(job)
            self._db.insert_job(job, score)
            scored_jobs.append((job, score))

        # ── 4. Notify on high-scoring jobs ──────────────────────────────
        min_score = self._settings.MIN_SCORE_FOR_NOTIFICATION
        notify_candidates = [(job, score) for job, score in scored_jobs if score >= min_score]

        notified_count = 0
        if notify_candidates:
            logger.info(
                "Notifying on %d jobs with score >= %d...",
                len(notify_candidates),
                min_score,
            )
            for job, score in notify_candidates:
                channels = self._notifier.send(job, score)
                if channels:
                    self._db.mark_notified(job.id, channel=",".join(channels))
                    notified_count += 1
        else:
            logger.info("No jobs above notification threshold (min_score=%d)", min_score)

        # ── 4b. Fire saved skill watches on the fresh listings ──────────
        # The cycle's job is to store and notify; a broken alert path must not
        # roll that back, so matching runs defensively and logs rather than raises.
        skill_matches = 0
        if scored_jobs:
            try:
                skill_matches = self._match_skill_watches(scored_jobs)
                if skill_matches:
                    logger.info("Skill watches matched %d listing(s)", skill_matches)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Skill-watch matching failed this cycle: %s", exc)

        # ── 5. Complete execution ───────────────────────────────────────
        error_str = "; ".join(errors[:10]) if errors else None
        self._db.complete_execution(
            execution_id=execution_id,
            jobs_found=len(all_jobs),
            jobs_new=len(new_jobs),
            jobs_notified=notified_count,
            errors=error_str,
        )

        stats = {
            "execution_id": execution_id,
            "jobs_found": len(all_jobs),
            "jobs_new": len(new_jobs),
            "jobs_notified": notified_count,
            "skill_watch_matches": skill_matches,
            "errors": len(errors),
        }
        logger.info("Cycle complete: %s", stats)
        return stats

    def _match_skill_watches(
        self, scored_jobs: list[tuple[Job, float]]
    ) -> int:
        """
        Run newly ingested listings against every user's saved skill watch.

        A skill watch only helps if it fires the moment a matching listing
        arrives, so the discovery cycle hands each fresh, scored job here after
        it is stored. Matching is best-effort per listing but the method does
        not swallow errors: a broken alert path must be visible, not silently
        skip the watch. Callers that want the cycle to survive a failure wrap
        the call themselves.

        Returns the total number of watch matches across all listings.
        """
        from interview.workers import match_and_alert

        matched_total = 0
        for job, _score in scored_jobs:
            row = self._job_to_row(job)
            summary = match_and_alert(row, send_email=False)
            matched_total += int(summary.get("matched") or 0)
        return matched_total

    @staticmethod
    def _job_to_row(job: Job) -> dict[str, object]:
        """Project a scraped Job into the flat dict the matcher reads."""
        return {
            "id": job.id,
            "slug": job.id,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "source": job.source,
            "description": job.description or "",
            "tags": list(job.tags or []),
            "salary": job.salary,
            "country": job.country,
        }

    def get_stats(self) -> dict[str, object]:
        """Get database stats."""
        return dict(self._db.get_stats())
