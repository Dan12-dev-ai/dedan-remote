"""
Difficulty Estimator — evaluates learning curve, application complexity,
interview difficulty, and technical requirements.
"""

from __future__ import annotations

from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)


class DifficultyEstimator:
    """
    Estimates the overall difficulty of obtaining and completing an opportunity.

    Evaluates:
      - Learning curve
      - Application difficulty
      - Interview difficulty
      - Technical difficulty

    Returns one of: Very Easy, Easy, Moderate, Hard, Very Hard
    """

    # Indicators that make an opportunity very easy
    VERY_EASY_INDICATORS: list[str] = [
        "no experience", "no skills", "no qualifications",
        "anyone can", "start today", "immediate start",
        "no interview", "quick apply", "instant",
        "no sign up", "no application", "just sign up",
        "work immediately", "start earning",
        "no resume", "no cv", "no cover letter",
        "easy", "simple", "quick", "fast",
    ]

    # Indicators that make it easy
    EASY_INDICATORS: list[str] = [
        "beginner", "basic", "fundamental",
        "training provided", "learn",
        "online", "remote", "flexible",
        "part-time", "freelance", "contract",
        "short application", "simple application",
        "apply now", "sign up",
    ]

    # Indicators that make it moderate
    MODERATE_INDICATORS: list[str] = [
        "some experience", "basic knowledge",
        "familiar with", "some familiarity",
        "resume required", "cv required",
        "short assessment", "brief test",
        "sample required", "portfolio",
        "online interview", "video interview",
        "1-2 years", "entry level",
    ]

    # Indicators that make it hard
    HARD_INDICATORS: list[str] = [
        "3+ years", "extensive experience",
        "multiple rounds", "technical interview",
        "coding challenge", "technical test",
        "take-home", "assessment",
        "portfolio required", "work sample",
        "background check", "reference check",
        "phone screen", "panel interview",
        "bachelor", "bachelor's", "bachelors",
        "degree required", "university degree",
    ]

    # Indicators that make it very hard
    VERY_HARD_INDICATORS: list[str] = [
        "phd", "doctorate", "ph.d",
        "5+ years", "10 years",
        "senior", "lead", "principal",
        "multiple technical rounds",
        "system design", "architecture",
        "whiteboard", "live coding",
        "extensive portfolio",
        "security clearance",
        "published research",
        "expert", "world-class",
    ]

    DIFFICULTY_LEVELS = [
        ("Very Easy", VERY_EASY_INDICATORS, 1),
        ("Easy", EASY_INDICATORS, 2),
        ("Moderate", MODERATE_INDICATORS, 3),
        ("Hard", HARD_INDICATORS, 4),
        ("Very Hard", VERY_HARD_INDICATORS, 5),
    ]

    def estimate(self, job: Job) -> dict[str, str | int]:
        """
        Estimate difficulty across all dimensions.

        Returns dict with keys:
          - learning_curve
          - application_difficulty
          - interview_difficulty
          - technical_difficulty
          - overall
          - overall_score (1-5, 1=Very Easy, 5=Very Hard)
        """
        text = self._build_text(job).lower()

        learning_curve = self._score_dimension(text, "learning")
        application = self._score_dimension(text, "application")
        interview = self._score_dimension(text, "interview")
        technical = self._score_dimension(text, "technical")

        # Overall is the maximum of all dimensions
        overall_score = max(learning_curve, application, interview, technical)

        overall_label = self._score_to_label(overall_score)

        return {
            "learning_curve": self._score_to_label(learning_curve),
            "application_difficulty": self._score_to_label(application),
            "interview_difficulty": self._score_to_label(interview),
            "technical_difficulty": self._score_to_label(technical),
            "overall": overall_label,
            "overall_score": overall_score,
        }

    def _score_dimension(self, text: str, dimension: str) -> int:
        """
        Score a difficulty dimension from 1 (Very Easy) to 5 (Very Hard).

        Uses the indicator lists above; dimension-specific logic
        can be added in the future.
        """
        score = 3  # Default: moderate

        # Check Very Easy indicators
        if any(kw in text for kw in self.VERY_EASY_INDICATORS):
            score = 1
        # Check Easy indicators
        elif any(kw in text for kw in self.EASY_INDICATORS):
            score = 2

        # Check Hard indicators (can override Easy)
        if any(kw in text for kw in self.HARD_INDICATORS):
            score = 4
        if any(kw in text for kw in self.VERY_HARD_INDICATORS):
            score = 5

        return score

    def _score_to_label(self, score: int) -> str:
        """Convert numeric score to label."""
        mapping = {1: "Very Easy", 2: "Easy", 3: "Moderate", 4: "Hard", 5: "Very Hard"}
        return mapping.get(score, "Moderate")

    def _build_text(self, job: Job) -> str:
        """Build searchable text from job."""
        parts = [
            job.title,
            job.description or "",
            " ".join(job.tags),
            job.source,
        ]
        return " ".join(parts)