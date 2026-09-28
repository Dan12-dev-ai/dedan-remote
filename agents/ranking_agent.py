"""
Ranking Agent — scores every job using configurable criteria.
Produces a 0-100 score used to determine notification urgency.
"""

from __future__ import annotations

from datetime import datetime, timezone

from config.settings import get_settings
from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)


class RankingAgent:
    """
    Scores a Job based on multiple weighted criteria.

    Weights are loaded from configuration and can be adjusted via .env
    without code changes.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._weights = self._settings.get_ranking_weights()

    def score(self, job: Job) -> float:
        """
        Compute a score (0-100) for the given job.

        Higher scores indicate higher-quality opportunities that
        should trigger immediate notification.

        Scoring dimensions:
          - Remote work (up to weight * 100)
          - Worldwide availability
          - Salary presence and estimated value
          - Beginner-friendly indicators
          - AI-related tags
          - English language
          - Application simplicity (low barrier to entry)
          - Freshness (recently posted)
        """
        scores: dict[str, float] = {
            "remote": self._score_remote(job),
            "worldwide": self._score_worldwide(job),
            "salary": self._score_salary(job),
            "beginner": self._score_beginner(job),
            "ai_related": self._score_ai_related(job),
            "english": self._score_english(job),
            "simplicity": self._score_simplicity(job),
            "freshness": self._score_freshness(job),
        }

        total = sum(scores[dim] * self._weights[dim] for dim in self._weights)

        logger.debug(
            "Score for %s @ %s: %.1f (details: %s)",
            job.title,
            job.company,
            total,
            {k: round(v, 1) for k, v in scores.items()},
        )
        return round(min(max(total, 0.0), 100.0), 1)

    def _score_remote(self, job: Job) -> float:
        """Remote jobs get full marks."""
        return 100.0 if job.remote else 0.0

    def _score_worldwide(self, job: Job) -> float:
        """Worldwide (no country restriction) gets 100. Country-specific gets 30."""
        if job.country is None or job.country.lower() in ("worldwide", "global", "remote", ""):
            return 100.0
        return 30.0

    def _score_salary(self, job: Job) -> float:
        """Jobs with salary info score higher. Rough estimate from string."""
        if not job.salary:
            return 0.0

        salary_lower = job.salary.lower()
        # Check for high-value indicators
        high_value_keywords = [
            "$50",
            "$60",
            "$70",
            "$80",
            "$90",
            "$100",
            "50k",
            "60k",
            "70k",
            "80k",
            "90k",
            "100k",
            "competitive",
            "negotiable",
            "doe",
        ]
        if any(kw in salary_lower for kw in high_value_keywords):
            return 100.0

        # Medium value
        medium_keywords = [
            "$20",
            "$30",
            "$40",
            "20k",
            "30k",
            "40k",
            "hour",
            "hr",
            "/hr",
            "per hour",
        ]
        if any(kw in salary_lower for kw in medium_keywords):
            return 60.0

        # Has salary data but unclear value
        return 40.0

    def _score_beginner(self, job: Job) -> float:
        """
        Detect beginner-friendly indicators in title, description, and tags.
        """
        text = f"{job.title} {job.description or ''} {' '.join(job.tags)}".lower()

        beginner_indicators = [
            "beginner",
            "entry level",
            "entry-level",
            "no experience",
            "training provided",
            "junior",
            "no degree",
            "anyone can",
            "start today",
            "immediate start",
            "easy",
            "simple",
            "no prior experience",
            "welcome",
            "all levels",
            "remote friendly",
            "flexible hours",
            "work from home",
            "no interview",
            "quick apply",
            "instant",
        ]
        for keyword in beginner_indicators:
            if keyword in text:
                return 100.0

        return 20.0

    def _score_ai_related(self, job: Job) -> float:
        """Score how strongly AI-related the job is."""
        text = f"{job.title} {job.description or ''} {' '.join(job.tags)}".lower()

        high_ai = [
            "ai",
            "artificial intelligence",
            "machine learning",
            "ml",
            "deep learning",
            "llm",
            "gpt",
            "neural network",
            "nlp",
            "prompt engineer",
            "rhlf",
            "rlhf",
            "reinforcement learning",
            "model training",
            "ai training",
            "ai evaluation",
            "data annotation",
            "ai data",
            "training data",
        ]
        for kw in high_ai:
            if kw in text:
                return 100.0

        medium_ai = [
            "data labeling",
            "data labelling",
            "tagging",
            "categorization",
            "classification",
            "transcription",
            "annotation",
            "human feedback",
            "search evaluation",
            "rating",
        ]
        for kw in medium_ai:
            if kw in text:
                return 70.0

        return 30.0

    def _score_english(self, job: Job) -> float:
        """English-language jobs score higher."""
        text = f"{job.title} {job.description or ''} {' '.join(job.tags)}".lower()
        non_english = [
            "español",
            "français",
            "deutsch",
            "italiano",
            "português",
            "中文",
            "日本語",
            "한국어",
            "русский",
            "arabic",
            "hindi",
        ]
        for lang in non_english:
            if lang in text:
                return 30.0
        # Most AI work is English
        return 80.0

    def _score_simplicity(self, job: Job) -> float:
        """
        Score how easy it is to apply (low barrier = higher score).
        """
        text = f"{job.title} {job.description or ''} {' '.join(job.tags)}".lower()

        simple_indicators = [
            "online",
            "remote",
            "flexible",
            "work from home",
            "no experience needed",
            "start immediately",
            "freelance",
            "contract",
            "part-time",
            "anywhere",
            "worldwide",
            "open to all",
        ]
        for kw in simple_indicators:
            if kw in text:
                return 90.0

        complex_indicators = [
            "phd",
            "doctorate",
            "10 years",
            "senior",
            "extensive experience",
            "advanced degree",
        ]
        for kw in complex_indicators:
            if kw in text:
                return 30.0

        return 60.0

    def _score_freshness(self, job: Job) -> float:
        """
        Fresher jobs score higher. If we can't determine, assume medium.
        """
        if not job.posted_date:
            return 70.0

        try:
            # Try various date formats
            for fmt in (
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%d/%m/%Y",
                "%B %d, %Y",
                "%b %d, %Y",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S",
            ):
                try:
                    posted = datetime.strptime(job.posted_date, fmt)
                    if posted.tzinfo is None:
                        posted = posted.replace(tzinfo=timezone.utc)
                    days_ago = (datetime.now(timezone.utc) - posted).days
                    if days_ago <= 1:
                        return 100.0
                    if days_ago <= 3:
                        return 85.0
                    if days_ago <= 7:
                        return 70.0
                    if days_ago <= 14:
                        return 50.0
                    if days_ago <= 30:
                        return 30.0
                    return 10.0
                except (ValueError, TypeError):
                    continue
        except Exception:
            pass

        return 70.0

    def explain(self, job: Job) -> dict[str, object]:
        """
        Return a full score breakdown for a job without changing ``score()``.

        Public, additive API used by DEDAN Remote to explain ranking to users.
        Dimensions, weights and sub-scores are exactly the ones used by
        :meth:`score` — nothing new is invented here.
        """
        scores: dict[str, float] = {
            "remote": self._score_remote(job),
            "worldwide": self._score_worldwide(job),
            "salary": self._score_salary(job),
            "beginner": self._score_beginner(job),
            "ai_related": self._score_ai_related(job),
            "english": self._score_english(job),
            "simplicity": self._score_simplicity(job),
            "freshness": self._score_freshness(job),
        }
        total = sum(scores[dim] * self._weights[dim] for dim in self._weights)
        return {
            "total": round(min(max(total, 0.0), 100.0), 1),
            "dimensions": {
                dim: {
                    "score": round(scores[dim], 1),
                    "weight": self._weights[dim],
                    "contribution": round(scores[dim] * self._weights[dim], 2),
                }
                for dim in self._weights
            },
        }

    def batch_score(self, jobs: list[Job]) -> list[tuple[Job, float]]:
        """Score multiple jobs and return sorted list of (job, score)."""
        scored = [(job, self.score(job)) for job in jobs]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
