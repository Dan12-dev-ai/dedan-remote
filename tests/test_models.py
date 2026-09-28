"""
Tests for the Job model.
"""

from __future__ import annotations

from models.job import Job, job_from_scraper_result


class TestJobModel:
    """Test Job dataclass creation and behavior."""

    def test_create_job(self) -> None:
        """Test basic job creation."""
        job = job_from_scraper_result(
            title="AI Trainer",
            company="OpenAI",
            url="https://openai.com/careers/123",
            source="openai",
        )
        assert job.title == "AI Trainer"
        assert job.company == "OpenAI"
        assert job.source == "openai"
        assert job.remote is True
        assert job.id is not None
        assert len(job.id) == 16  # SHA-256 truncated

    def test_job_id_deterministic(self) -> None:
        """Test that same source+url produces same id."""
        job1 = job_from_scraper_result(
            title="Job A",
            company="C",
            url="https://x.com/1",
            source="test",
        )
        job2 = job_from_scraper_result(
            title="Job B",
            company="C",
            url="https://x.com/1",
            source="test",
        )
        assert job1.id == job2.id  # Same source+url = same id

    def test_job_id_different_urls(self) -> None:
        """Test that different URLs produce different ids."""
        job1 = job_from_scraper_result(
            title="Job A",
            company="C",
            url="https://x.com/1",
            source="test",
        )
        job2 = job_from_scraper_result(
            title="Job A",
            company="C",
            url="https://x.com/2",
            source="test",
        )
        assert job1.id != job2.id

    def test_job_immutable(self) -> None:
        """Test that Job is frozen (immutable)."""
        job = job_from_scraper_result(
            title="AI Trainer",
            company="OpenAI",
            url="https://openai.com/123",
            source="openai",
        )
        import dataclasses

        assert dataclasses.fields(job)

    def test_short_summary(self) -> None:
        """Test short_summary property."""
        job = job_from_scraper_result(
            title="AI Trainer",
            company="OpenAI",
            url="https://openai.com/123",
            source="openai",
            salary="$100/hr",
            tags=["ai", "training"],
        )
        summary = job.short_summary
        assert "AI Trainer" in summary
        assert "OpenAI" in summary
        assert "$100/hr" in summary

    def test_to_dict_and_from_dict(self) -> None:
        """Test serialization round-trip."""
        job = job_from_scraper_result(
            title="AI Evaluator",
            company="TestCo",
            url="https://test.com/job/1",
            source="test",
            salary="$50/hr",
            country="US",
            remote=True,
            posted_date="2026-06-28",
            description="Evaluate AI models",
            tags=["ai", "evaluation"],
        )
        data = job.to_dict()
        restored = Job.from_dict(data)
        assert restored.title == job.title
        assert restored.company == job.company
        assert restored.url == job.url
        assert restored.source == job.source
        assert restored.salary == job.salary
        assert restored.country == job.country
        assert restored.remote == job.remote
        assert restored.tags == job.tags

    def test_apply_url(self) -> None:
        """Test apply_url property."""
        job = job_from_scraper_result(
            title="Test",
            company="C",
            url="https://example.com/apply",
            source="test",
        )
        assert job.apply_url == "https://example.com/apply"

    def test_empty_tags(self) -> None:
        """Test job with no tags."""
        job = job_from_scraper_result(
            title="Test",
            company="C",
            url="https://example.com/job",
            source="test",
        )
        assert job.tags == []
