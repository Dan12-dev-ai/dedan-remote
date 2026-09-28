"""
Success Predictor — estimates the probability of successfully obtaining
an opportunity based on multiple factors.
"""

from __future__ import annotations

from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)


class SuccessPredictor:
    """
    Predicts the likelihood (0–100%) of successfully obtaining an opportunity.

    Factors considered:
      - Required experience level
      - Portfolio requirements
      - Interview difficulty
      - Competition level
      - Country restrictions
      - Language requirements
      - Technical requirements
      - Application complexity
    """

    def predict(self, job: Job, skill_score: int, difficulty: dict[str, str | int]) -> int:
        """
        Estimate success probability as a percentage (0–100).

        Args:
            job: The job opportunity.
            skill_score: Score from SkillMatcher (0–100).
            difficulty: Dict from DifficultyEstimator.

        Returns:
            Success probability percentage.
        """
        text = self._build_text(job).lower()
        probability = 70  # Start at baseline

        # ── Experience level impact ──────────────────────────────────
        if skill_score >= 90:
            probability += 20
        elif skill_score >= 80:
            probability += 15
        elif skill_score >= 70:
            probability += 10
        elif skill_score >= 50:
            probability += 0
        else:
            probability -= 20

        # ── Difficulty impact ────────────────────────────────────────
        overall_score = difficulty.get("overall_score", 3)
        if isinstance(overall_score, int):
            if overall_score <= 1:  # Very Easy
                probability += 15
            elif overall_score == 2:  # Easy
                probability += 10
            elif overall_score == 3:  # Moderate
                probability += 0
            elif overall_score == 4:  # Hard
                probability -= 20
            else:  # Very Hard
                probability -= 35

        # ── Competition indicators ───────────────────────────────────
        competition_high = [
            "high competition", "many applicants", "competitive",
            "limited spots", "high volume", "popular",
        ]
        if any(kw in text for kw in competition_high):
            probability -= 15

        competition_low = [
            "urgent", "immediate need", "quick hire",
            "growing team", "new project", "new program",
            "limited applications", "early access",
        ]
        if any(kw in text for kw in competition_low):
            probability += 10

        # ── Country/geography impact ─────────────────────────────────
        if job.country and job.country.lower() in (
            "worldwide", "global", "remote", "anywhere",
        ):
            probability += 10
        elif job.country and job.country.lower() in (
            "united states", "united kingdom", "canada",
        ):
            probability += 5

        # ── Language requirements ────────────────────────────────────
        non_english = [
            "fluent in spanish", "fluent in french", "fluent in german",
            "mandarin required", "japanese required",
            "bilingual required", "native language",
        ]
        if any(kw in text for kw in non_english):
            probability -= 15

        # ── Technical requirements ───────────────────────────────────
        high_tech = [
            "python", "javascript", "react", "node", "docker",
            "kubernetes", "aws", "gcp", "azure", "api",
            "sql", "database", "backend", "frontend",
        ]
        tech_count = sum(1 for kw in high_tech if kw in text)
        if tech_count >= 3:
            probability -= 10
        elif tech_count >= 1:
            probability -= 5

        # ── Application complexity ───────────────────────────────────
        complex_application = [
            "resume required", "cover letter", "portfolio",
            "writing sample", "assessment", "test",
            "multiple rounds", "interview",
        ]
        complex_count = sum(1 for kw in complex_application if kw in text)
        if complex_count >= 3:
            probability -= 15
        elif complex_count >= 1:
            probability -= 5

        # ── Remote/flexible bonus ────────────────────────────────────
        if job.remote:
            probability += 10

        # ── High-priority opportunity bonus ──────────────────────────
        high_priority_titles = [
            "data labeler", "data annotator", "ai trainer", "ai evaluator",
            "search evaluator", "transcriber", "translator",
            "transcription", "captioning", "content moderator",
            "virtual assistant", "data entry", "user testing",
            "qa tester", "microtask", "survey",
            "prompt writer", "prompt evaluator", "rhlf",
        ]
        if any(kw in job.title.lower() for kw in high_priority_titles):
            probability += 10

        return max(0, min(100, probability))

    def _build_text(self, job: Job) -> str:
        """Build searchable text from job."""
        parts = [
            job.title,
            job.description or "",
            " ".join(job.tags),
            job.source,
        ]
        return " ".join(parts)