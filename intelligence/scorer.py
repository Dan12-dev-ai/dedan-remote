"""
Comprehensive Scorer — combines all intelligence modules into a single
holistic evaluation pipeline for job opportunities.

Produces a final recommendation with:
  - Overall score (0-100)
  - Sub-scores across all dimensions
  - Verdict: RECOMMENDED / CONSIDER / REJECT
  - Actionable insights and next steps
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.job import Job
from intelligence.eligibility import EligibilityEngine
from intelligence.skill_matcher import SkillMatcher
from intelligence.difficulty_estimator import DifficultyEstimator
from intelligence.success_predictor import SuccessPredictor
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ScorerResult:
    """Result of a comprehensive job scoring evaluation."""

    overall_score: float
    eligibility_score: int
    skill_match_score: int
    success_probability: int
    difficulty: dict[str, Any]
    experience_level: str
    is_eligible: bool
    is_high_priority: bool
    is_specialized_role: bool
    verdict: str
    confidence: float
    insights: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    raw_scores: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "eligibility_score": self.eligibility_score,
            "skill_match_score": self.skill_match_score,
            "success_probability": self.success_probability,
            "difficulty": self.difficulty,
            "experience_level": self.experience_level,
            "is_eligible": self.is_eligible,
            "is_high_priority": self.is_high_priority,
            "is_specialized_role": self.is_specialized_role,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "insights": self.insights,
            "next_steps": self.next_steps,
            "raw_scores": self.raw_scores,
        }


class ComprehensiveScorer:
    """
    Holistic scorer combining all intelligence sub-modules.

    Evaluation pipeline:
      1. Eligibility Engine — geographic/payment access
      2. Skill Matcher — experience/skill level fit
      3. Difficulty Estimator — application/learning curve
      4. Success Predictor — probability of obtaining the role

    Weighted final score with explicit verdicts and recommendations.
    """

    WEIGHTS = {
        "eligibility": 0.30,
        "skill_match": 0.25,
        "success_probability": 0.25,
        "ease_of_application": 0.20,
    }

    VERDICT_THRESHOLDS = {
        "RECOMMENDED": 75,
        "CONSIDER": 45,
    }

    def __init__(self) -> None:
        self._eligibility = EligibilityEngine()
        self._skill_matcher = SkillMatcher()
        self._difficulty = DifficultyEstimator()
        self._success_predictor = SuccessPredictor()

    def evaluate(self, job: Job) -> ScorerResult:
        """
        Run a full evaluation pipeline on a job.

        Args:
            job: The job opportunity to evaluate.

        Returns:
            ScorerResult with comprehensive evaluation data.
        """
        eligibility_score = self._eligibility.score(job)
        skill_match_score = self._skill_matcher.score(job)
        difficulty_info = self._difficulty.estimate(job)
        success_prob = self._success_predictor.predict(
            job, skill_match_score, difficulty_info,
        )
        experience_level = self._skill_matcher.get_experience_level(job)
        is_eligible = self._eligibility.is_eligible(job)
        is_high_priority = self._skill_matcher.is_high_priority(job)
        is_specialized = self._skill_matcher.is_specialized_role(job)

        ease_score = self._ease_from_difficulty(difficulty_info)

        overall = (
            eligibility_score * self.WEIGHTS["eligibility"] +
            skill_match_score * self.WEIGHTS["skill_match"] +
            success_prob * self.WEIGHTS["success_probability"] +
            ease_score * self.WEIGHTS["ease_of_application"]
        )
        overall = round(max(0.0, min(100.0, overall)), 1)

        verdict = self._verdict(overall, is_eligible, is_specialized)
        confidence = self._compute_confidence(
            eligibility_score, skill_match_score, success_prob,
        )
        insights = self._generate_insights(
            job, eligibility_score, skill_match_score, difficulty_info,
            success_prob, is_high_priority, is_specialized,
        )
        next_steps = self._generate_next_steps(
            verdict, difficulty_info, is_high_priority,
        )

        raw_scores = {
            "ease_of_application": ease_score,
            "difficulty_overall_score": difficulty_info.get("overall_score", 3),
        }

        logger.debug(
            "Scored '%s @ %s': overall=%.1f verdict=%s eligible=%s",
            job.title, job.company, overall, verdict, is_eligible,
        )

        return ScorerResult(
            overall_score=overall,
            eligibility_score=eligibility_score,
            skill_match_score=skill_match_score,
            success_probability=success_prob,
            difficulty=difficulty_info,
            experience_level=experience_level,
            is_eligible=is_eligible,
            is_high_priority=is_high_priority,
            is_specialized_role=is_specialized,
            verdict=verdict,
            confidence=confidence,
            insights=insights,
            next_steps=next_steps,
            raw_scores=raw_scores,
        )

    def batch_evaluate(self, jobs: list[Job]) -> list[tuple[Job, ScorerResult]]:
        """Evaluate a batch of jobs, sorted by overall score descending."""
        results = [(job, self.evaluate(job)) for job in jobs]
        results.sort(key=lambda x: x[1].overall_score, reverse=True)
        return results

    def get_recommended(self, jobs: list[Job]) -> list[tuple[Job, ScorerResult]]:
        """Return only RECOMMENDED jobs from a batch."""
        return [
            (job, result) for job, result in self.batch_evaluate(jobs)
            if result.verdict == "RECOMMENDED"
        ]

    def _ease_from_difficulty(self, difficulty: dict[str, Any]) -> float:
        """Convert difficulty score (1-5) to ease score (0-100)."""
        overall_score = difficulty.get("overall_score", 3)
        if isinstance(overall_score, int):
            mapping = {1: 100, 2: 80, 3: 60, 4: 30, 5: 10}
            return float(mapping.get(overall_score, 50))
        return 50.0

    def _verdict(self, overall: float, eligible: bool, specialized: bool) -> str:
        """Determine final verdict from scores."""
        if not eligible:
            return "REJECT"
        if specialized and overall < 60:
            return "REJECT"
        if overall >= self.VERDICT_THRESHOLDS["RECOMMENDED"]:
            return "RECOMMENDED"
        if overall >= self.VERDICT_THRESHOLDS["CONSIDER"]:
            return "CONSIDER"
        return "REJECT"

    def _compute_confidence(
        self,
        eligibility: int,
        skill: int,
        success: int,
    ) -> float:
        """
        Compute confidence in the evaluation based on score consistency.

        High consistency → high confidence. Large disagreements → lower.
        """
        scores = [float(eligibility), float(skill), float(success)]
        mean = sum(scores) / len(scores)
        if mean == 0:
            return 0.5
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = variance ** 0.5
        normalized_std = std / 100.0
        confidence = max(0.3, min(0.95, 1.0 - normalized_std * 2))
        return round(confidence, 2)

    def _generate_insights(
        self,
        job: Job,
        eligibility: int,
        skill: int,
        difficulty: dict[str, Any],
        success: int,
        high_priority: bool,
        specialized: bool,
    ) -> list[str]:
        """Generate human-readable evaluation insights."""
        insights: list[str] = []

        if high_priority:
            insights.append("✅ Matches high-priority opportunity categories")

        if eligibility >= 80:
            insights.append("🌍 Strong geographic/payment eligibility for Ethiopia")
        elif eligibility >= 50:
            insights.append("⚠️ Moderate eligibility — verify payment methods before applying")
        else:
            insights.append("❌ Low eligibility — may face payment or geographic restrictions")

        if skill >= 80:
            insights.append("🎯 Excellent skill/experience match for entry level")
        elif skill >= 50:
            insights.append("📚 Moderate skill fit — may require some upskilling")
        else:
            insights.append("💡 Skill mismatch — consider building relevant skills first")

        diff_overall = difficulty.get("overall", "Moderate")
        insights.append(f"📊 Application difficulty: {diff_overall}")

        if success >= 70:
            insights.append("🚀 High estimated success probability — prioritize this")
        elif success >= 40:
            insights.append("⚖️ Moderate success probability — worth trying")
        else:
            insights.append("📉 Low success probability — apply selectively")

        if specialized:
            insights.append("⚠️ Specialized role — ensure you meet requirements before applying")

        if job.salary:
            insights.append(f"💰 Listed compensation: {job.salary}")

        return insights

    def _generate_next_steps(
        self,
        verdict: str,
        difficulty: dict[str, Any],
        high_priority: bool,
    ) -> list[str]:
        """Generate actionable next steps based on evaluation."""
        steps: list[str] = []

        if verdict == "RECOMMENDED":
            steps.append("🔴 PRIORITY: Apply immediately — this is a strong match")
            if high_priority:
                steps.append("📌 This is in a high-demand category — act fast")
        elif verdict == "CONSIDER":
            steps.append("🟡 Add to watchlist — apply if no better options emerge")
        else:
            steps.append("⚫ Skip — low match or eligibility issues")

        overall_score = difficulty.get("overall_score", 3)
        if isinstance(overall_score, int) and overall_score >= 4:
            steps.append("📝 Prepare application materials: resume, cover letter, portfolio samples")
            steps.append("🎯 Practice interview questions for this role type")
        elif overall_score <= 2:
            steps.append("⚡ Quick application process — can apply in under 15 minutes")

        learning = difficulty.get("learning_curve", "Moderate")
        if learning in ("Hard", "Very Hard"):
            steps.append("📚 Plan 2-4 weeks for onboarding/learning curve")

        steps.append("🔗 Visit the job URL for full details and latest updates")

        return steps
