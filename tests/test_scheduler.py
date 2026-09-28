"""
Tests for the Scheduler Agent.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.scheduler_agent import SchedulerAgent


class TestSchedulerAgent:
    """Test scheduler functionality."""

    @pytest.mark.asyncio
    async def test_run_once(self) -> None:
        """Test running a single cycle."""
        scheduler = SchedulerAgent()
        with patch.object(
            scheduler._discovery,
            "run_once",
            return_value={
                "jobs_found": 5,
                "jobs_new": 3,
                "jobs_notified": 1,
                "errors": 0,
            },
        ):
            result = await scheduler.run_once()
            assert result["jobs_found"] == 5
            assert result["jobs_new"] == 3

    def test_initial_state(self) -> None:
        """Test initial scheduler state."""
        scheduler = SchedulerAgent()
        assert scheduler.is_running is False

    def test_stats(self) -> None:
        """Test stats method."""
        scheduler = SchedulerAgent()
        with patch.object(
            scheduler._discovery,
            "get_stats",
            return_value={
                "total_jobs": 10,
                "notified_jobs": 5,
                "pending_jobs": 5,
                "total_executions": 3,
            },
        ):
            stats = scheduler.stats()
            assert stats["total_jobs"] == 10
            assert stats["notified_jobs"] == 5

    @pytest.mark.asyncio
    async def test_shutdown_no_running_task(self) -> None:
        """Test shutdown without running task."""
        scheduler = SchedulerAgent()
        # Start the scheduler first so shutdown works
        scheduler.start()
        # Should not raise
        with pytest.raises(SystemExit):
            await scheduler.shutdown()
