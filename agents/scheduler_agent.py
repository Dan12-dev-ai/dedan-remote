"""
Scheduler Agent — runs the discovery cycle at configurable intervals.
Uses APScheduler for robust cron-based scheduling with overlap prevention.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.discovery_agent import DiscoveryAgent
from config.settings import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)


class SchedulerAgent:
    """
    Manages the execution schedule for the discovery agent.

    Features:
      - Configurable interval (minutes) or cron expression
      - Prevents overlapping executions
      - Graceful shutdown on SIGINT/SIGTERM
      - One-shot mode for testing
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._discovery = DiscoveryAgent()
        self._scheduler = AsyncIOScheduler()
        self._running = False
        self._current_task: Optional[asyncio.Task[object]] = None

    async def _execute_cycle(self) -> None:
        """Execute one discovery cycle, preventing overlap."""
        if self._current_task is not None and not self._current_task.done():
            logger.warning("Previous cycle still running — skipping this execution")
            return

        task = asyncio.create_task(self._discovery.run_once())
        self._current_task = task
        try:
            await task
        except Exception as exc:
            logger.error("Discovery cycle crashed: %s", exc)
        finally:
            self._current_task = None

    def start(self) -> None:
        """Start the scheduler with the configured schedule."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        # Register signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.shutdown(s)))
            except (NotImplementedError, ValueError):
                # Windows may not support add_signal_handler
                pass

        # Set up the schedule
        cron = self._settings.CRON_SCHEDULE
        interval = self._settings.CHECK_INTERVAL

        if cron and cron.strip():
            try:
                parts = cron.strip().split()
                if len(parts) == 5:
                    trigger = CronTrigger(
                        minute=parts[0],
                        hour=parts[1],
                        day=parts[2],
                        month=parts[3],
                        day_of_week=parts[4],
                        timezone="UTC",
                    )
                    logger.info("Using cron schedule: %s", cron)
                else:
                    raise ValueError(f"Invalid cron expression: {cron}")
            except Exception as exc:
                logger.warning(
                    "Invalid cron '%s', falling back to interval: %s",
                    cron, exc,
                )
                trigger = IntervalTrigger(minutes=interval)
        else:
            trigger = IntervalTrigger(minutes=interval)
            logger.info("Using interval schedule: every %d minutes", interval)

        self._scheduler.add_job(
            self._execute_cycle,
            trigger=trigger,
            id="discovery_cycle",
            name="AI Opportunity Discovery Cycle",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Run immediately on start
        self._scheduler.add_job(
            self._execute_cycle,
            trigger="date",
            id="initial_run",
            name="Initial Discovery Cycle",
        )

        self._scheduler.start()
        self._running = True
        logger.info("Scheduler started — next runs will follow schedule")

    async def run_once(self) -> dict[str, object]:
        """Run a single discovery cycle immediately (for testing/CLI)."""
        return await self._discovery.run_once()

    async def shutdown(self, sig: Optional[int] = None) -> None:
        """Gracefully shut down the scheduler."""
        sig_name = signal.Signals(sig).name if sig else "user"
        logger.info("Received %s — shutting down gracefully...", sig_name)

        self._scheduler.shutdown(wait=False)

        if self._current_task and not self._current_task.done():
            logger.info("Waiting for current cycle to finish...")
            try:
                await asyncio.wait_for(self._current_task, timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Current cycle did not finish in time — cancelling")
                self._current_task.cancel()

        from utils.http_client import close_http_client
        await close_http_client()

        self._running = False
        logger.info("Shutdown complete")
        sys.exit(0)

    def stats(self) -> dict[str, object]:
        """Get current stats from the database."""
        return self._discovery.get_stats()

    @property
    def is_running(self) -> bool:
        """Check if scheduler is active."""
        return self._running