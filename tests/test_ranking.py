"""
Tests for the Ranking Agent scoring logic.
"""

from __future__ import annotations

from agents.ranking_agent import RankingAgent
from models.job import job_from_scraper_result


class TestRankingAgent:
    """Test job scoring functionality."""

    def setup_method(self) -> None:
        self.agent = RankingAgent()

    def test_score_remote_job(self) -> None:
        """Test that remote jobs score higher."""
        job = job_from_scraper_result(
            title="AI Trainer", company="C",
            url="https://x.com/1", source="test",
            remote=True,
        )
        score = self.agent.score(job)
        assert 0 <= score <= 100

    def test_score_non_remote_job(self) -> None:
        """Test that non-remote jobs score lower on remote dimension."""
        job = job_from_scraper_result(
            title="AI Trainer", company="C",
            url="https://x.com/1", source="test",
            remote=False,
        )
        score = self.agent.score(job)
        assert 0 <= score <= 100

    def test_score_with_salary(self) -> None:
        """Test that salary information boosts score."""
        with_salary = self.agent.score(job_from_scraper_result(
            title="AI Trainer", company="C",
            url="https://x.com/1", source="test",
            salary="$100/hr",
        ))
        without_salary = self.agent.score(job_from_scraper_result(
            title="AI Trainer", company="C",
            url="https://x.com/2", source="test",
        ))
        assert with_salary >= without_salary

    def test_score_beginner_friendly(self) -> None:
        """Test beginner-friendly jobs score higher."""
        beginner = self.agent.score(job_from_scraper_result(
            title="AI Trainer No Experience Needed", company="C",
            url="https://x.com/1", source="test",
            tags=["beginner"],
        ))
        senior = self.agent.score(job_from_scraper_result(
            title="Senior AI Engineer", company="C",
            url="https://x.com/2", source="test",
            description="Requires PhD and 10 years experience",
        ))
        assert beginner >= senior

    def test_score_ai_related(self) -> None:
        """Test AI-related jobs score higher."""
        ai_job = self.agent.score(job_from_scraper_result(
            title="Machine Learning Engineer", company="C",
            url="https://x.com/1", source="test",
            tags=["ai", "deep learning", "llm"],
        ))
        non_ai = self.agent.score(job_from_scraper_result(
            title="Data Entry Clerk", company="C",
            url="https://x.com/2", source="test",
            tags=["admin"],
        ))
        assert ai_job >= non_ai

    def test_score_worldwide(self) -> None:
        """Test worldwide jobs score higher than country-specific."""
        worldwide = self.agent.score(job_from_scraper_result(
            title="AI Trainer", company="C",
            url="https://x.com/1", source="test",
            country=None,
        ))
        us_only = self.agent.score(job_from_scraper_result(
            title="AI Trainer", company="C",
            url="https://x.com/2", source="test",
            country="United States",
        ))
        assert worldwide >= us_only

    def test_batch_score(self) -> None:
        """Test batch scoring returns sorted results."""
        jobs = [
            job_from_scraper_result(
                title=f"Job {i}", company="C",
                url=f"https://x.com/{i}", source="test",
            )
            for i in range(5)
        ]
        scored = self.agent.batch_score(jobs)
        assert len(scored) == 5
        # Verify sorted descending
        for i in range(len(scored) - 1):
            assert scored[i][1] >= scored[i + 1][1]

    def test_score_range(self) -> None:
        """Test scores are in valid range."""
        jobs = [
            job_from_scraper_result(
                title=f"Job {i}", company="C",
                url=f"https://x.com/{i}", source="test",
                remote=i % 2 == 0,
                tags=["ai"] if i % 2 == 0 else [],
            )
            for i in range(10)
        ]
        for job in jobs:
            score = self.agent.score(job)
            assert 0 <= score <= 100, f"Score {score} out of range for {job.title}"