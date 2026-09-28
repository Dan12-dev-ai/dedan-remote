"""
Tests for the intelligence layer — eligibility, skill matching,
difficulty estimation, success prediction, and the comprehensive scorer.
"""

from __future__ import annotations

import pytest

from intelligence.difficulty_estimator import DifficultyEstimator
from intelligence.eligibility import EligibilityEngine
from intelligence.scorer import ComprehensiveScorer, ScorerResult
from intelligence.skill_matcher import SkillMatcher
from intelligence.success_predictor import SuccessPredictor
from models.job import Job, job_from_scraper_result


def make_job(**kwargs: object) -> Job:
    """Factory for test jobs with sensible defaults."""
    defaults: dict[str, object] = {
        "title": "AI Trainer",
        "company": "TestCorp",
        "url": "https://example.com/job/1",
        "source": "test",
    }
    defaults.update(kwargs)
    return job_from_scraper_result(**defaults)  # type: ignore[arg-type]


# ── Eligibility Engine ───────────────────────────────────────────────────────

class TestEligibilityEngine:
    """Geographic/payment eligibility for applicants in Ethiopia."""

    def setup_method(self) -> None:
        self.engine = EligibilityEngine()

    def test_worldwide_remote_scores_full(self) -> None:
        job = make_job(remote=True)
        assert self.engine.score(job) == 100

    def test_blocked_ethiopia_text_scores_zero(self) -> None:
        job = make_job(description="Not available in Ethiopia.")
        assert self.engine.score(job) == 0

    def test_us_citizenship_required_scores_zero(self) -> None:
        job = make_job(description="US citizens only may apply.")
        assert self.engine.score(job) == 0

    def test_sanctioned_country_scores_zero(self) -> None:
        job = make_job(country="Iran")
        assert self.engine.score(job) == 0

    def test_non_remote_is_penalised(self) -> None:
        remote = self.engine.score(make_job(remote=True))
        onsite = self.engine.score(make_job(remote=False))
        assert onsite < remote

    def test_geographic_restriction_is_penalised(self) -> None:
        job = make_job(description="Local candidates only, hybrid role.")
        assert self.engine.score(job) < 100

    def test_unfriendly_country_is_penalised(self) -> None:
        worldwide = self.engine.score(make_job(country=None))
        specific = self.engine.score(make_job(country="France"))
        assert specific < worldwide

    def test_ethiopia_friendly_payment_recovers_points(self) -> None:
        without = self.engine.score(make_job(country="France"))
        with_pay = self.engine.score(
            make_job(country="France", description="Paid via PayPal"),
        )
        assert with_pay > without

    def test_is_eligible_threshold(self) -> None:
        assert self.engine.is_eligible(make_job(remote=True)) is True
        blocked = make_job(description="Not available in Ethiopia.")
        assert self.engine.is_eligible(blocked) is False

    def test_score_always_within_bounds(self) -> None:
        jobs = [
            make_job(remote=False, country="Syria"),
            make_job(description="US only, must reside in US"),
            make_job(description="Pay via Payoneer", remote=True),
        ]
        for job in jobs:
            score = self.engine.score(job)
            assert 0 <= score <= 100


# ── Skill Matcher ────────────────────────────────────────────────────────────

class TestSkillMatcher:
    """Skill/experience matching biased toward beginners."""

    def setup_method(self) -> None:
        self.matcher = SkillMatcher()

    def test_no_experience_scores_high(self) -> None:
        job = make_job(description="No experience needed. Training provided.")
        assert self.matcher.score(job) >= 90

    def test_senior_role_scores_low(self) -> None:
        job = make_job(
            title="Senior Software Engineer",
            description="10+ years experience, lead responsibilities.",
        )
        assert self.matcher.score(job) <= 40

    def test_specialized_role_rejected(self) -> None:
        job = make_job(title="AI Research Scientist")
        assert self.matcher.score(job) == 0

    def test_specialized_role_with_entry_level_exception(self) -> None:
        job = make_job(
            title="AI Research Scientist",
            description="Entry level position, new graduates welcome.",
        )
        assert self.matcher.score(job) == 60

    def test_get_experience_level_no_experience(self) -> None:
        job = make_job(description="No experience required.")
        assert self.matcher.get_experience_level(job) == "No Experience"

    def test_get_experience_level_unknown(self) -> None:
        job = make_job(description="We want someone mysterious.")
        assert self.matcher.get_experience_level(job) == "Unknown"

    def test_is_specialized_role(self) -> None:
        assert self.matcher.is_specialized_role(
            make_job(title="Principal AI Engineer"),
        ) is True
        assert self.matcher.is_specialized_role(
            make_job(title="Data Annotator"),
        ) is False

    def test_is_high_priority(self) -> None:
        assert self.matcher.is_high_priority(
            make_job(title="AI Data Labeler"),
        ) is True
        assert self.matcher.is_high_priority(
            make_job(title="Office Manager"),
        ) is False

    @pytest.mark.parametrize("score_job", [
        make_job(title="AI Trainer", description="No experience required"),
        make_job(title="Senior ML Engineer", description="10 years experience"),
        make_job(title="Mystery Role", description="Enigmatic listing"),
    ])
    def test_score_always_within_bounds(self, score_job: Job) -> None:
        assert 0 <= self.matcher.score(score_job) <= 100


# ── Difficulty Estimator ─────────────────────────────────────────────────────

class TestDifficultyEstimator:
    """Difficulty estimation across learning/application/interview dimensions."""

    EXPECTED_KEYS = {
        "learning_curve", "application_difficulty", "interview_difficulty",
        "technical_difficulty", "overall", "overall_score",
    }

    def setup_method(self) -> None:
        self.estimator = DifficultyEstimator()

    def test_returns_all_dimensions(self) -> None:
        result = self.estimator.estimate(make_job())
        assert set(result.keys()) == self.EXPECTED_KEYS

    def test_quick_apply_is_very_easy(self) -> None:
        job = make_job(description="No experience, quick apply, start earning.")
        result = self.estimator.estimate(job)
        assert result["overall_score"] == 1
        assert result["overall"] == "Very Easy"

    def test_phd_role_is_very_hard(self) -> None:
        job = make_job(
            title="Research Lead",
            description="PhD required, system design, 5+ years experience.",
        )
        result = self.estimator.estimate(job)
        assert result["overall_score"] == 5
        assert result["overall"] == "Very Hard"

    def test_no_clues_defaults_to_moderate(self) -> None:
        result = self.estimator.estimate(
            make_job(title="Data Helper", description=None),
        )
        assert result["overall_score"] == 3
        assert result["overall"] == "Moderate"

    def test_hard_indicators_override_easy_ones(self) -> None:
        job = make_job(
            description="Beginner friendly but requires technical interview "
                        "and assessment.",
        )
        result = self.estimator.estimate(job)
        assert result["overall_score"] == 4
        assert result["overall"] == "Hard"

    @pytest.mark.parametrize("label", [
        "Very Easy", "Easy", "Moderate", "Hard", "Very Hard",
    ])
    def test_score_to_label_mapping(self, label: str) -> None:
        inv = {"Very Easy": 1, "Easy": 2, "Moderate": 3, "Hard": 4, "Very Hard": 5}
        assert self.estimator._score_to_label(inv[label]) == label

    def test_score_to_label_unknown_defaults_moderate(self) -> None:
        assert self.estimator._score_to_label(99) == "Moderate"


# ── Success Predictor ────────────────────────────────────────────────────────

class TestSuccessPredictor:
    """Probability estimation is bounded and monotonic in key factors."""

    def setup_method(self) -> None:
        self.predictor = SuccessPredictor()
        self.easy = DifficultyEstimator().estimate(
            make_job(description="No experience, quick apply."),
        )
        self.hard = DifficultyEstimator().estimate(
            make_job(description="PhD, 10 years, system design."),
        )

    def test_probability_always_bounded(self) -> None:
        extremes = [
            (0, self.hard),
            (100, self.easy),
            (50, {"overall_score": 3}),
        ]
        for skill, diff in extremes:
            prob = self.predictor.predict(make_job(), skill, diff)
            assert 0 <= prob <= 100

    def test_higher_skill_raises_probability(self) -> None:
        low = self.predictor.predict(make_job(), 40, self.easy)
        high = self.predictor.predict(make_job(), 95, self.easy)
        assert high > low

    def test_easier_difficulty_raises_probability(self) -> None:
        job = make_job()
        easy = self.predictor.predict(job, 70, self.easy)
        hard = self.predictor.predict(job, 70, self.hard)
        assert easy > hard

    def test_remote_bonus(self) -> None:
        # Neutral inputs (no priority-title bonus) so the +10 remote
        # bonus is observable without clamping at 100.
        neutral = dict(title="Data Helper", tags=[])
        remote = self.predictor.predict(
            make_job(remote=True, **neutral), 50, {"overall_score": 3},
        )
        onsite = self.predictor.predict(
            make_job(remote=False, **neutral), 50, {"overall_score": 3},
        )
        assert remote == onsite + 10

    def test_high_priority_title_bonus(self) -> None:
        priority = self.predictor.predict(
            make_job(title="Data Labeler"), 70, {"overall_score": 3},
        )
        generic = self.predictor.predict(
            make_job(title="Office Assistant"), 70, {"overall_score": 3},
        )
        assert priority > generic


# ── Comprehensive Scorer ─────────────────────────────────────────────────────

class TestComprehensiveScorer:
    """End-to-end evaluation pipeline."""

    def setup_method(self) -> None:
        self.scorer = ComprehensiveScorer()

    def test_evaluate_returns_scorer_result(self) -> None:
        result = self.scorer.evaluate(make_job(remote=True))
        assert isinstance(result, ScorerResult)
        assert 0.0 <= result.overall_score <= 100.0
        assert 0 <= result.eligibility_score <= 100
        assert 0 <= result.skill_match_score <= 100
        assert 0 <= result.success_probability <= 100
        assert 0.3 <= result.confidence <= 0.95
        assert result.verdict in {"RECOMMENDED", "CONSIDER", "REJECT"}

    def test_to_dict_exposes_all_fields(self) -> None:
        data = self.scorer.evaluate(make_job()).to_dict()
        for key in (
            "overall_score", "eligibility_score", "skill_match_score",
            "success_probability", "difficulty", "experience_level",
            "is_eligible", "verdict", "confidence", "insights", "next_steps",
        ):
            assert key in data

    def test_ineligible_job_is_rejected(self) -> None:
        job = make_job(description="Not available in Ethiopia.")
        result = self.scorer.evaluate(job)
        assert result.is_eligible is False
        assert result.verdict == "REJECT"

    def test_strong_beginner_job_is_recommended(self) -> None:
        job = make_job(
            title="AI Trainer",
            description="No experience needed, quick apply, start earning today.",
            remote=True,
        )
        result = self.scorer.evaluate(job)
        assert result.is_eligible is True
        assert result.verdict == "RECOMMENDED"
        assert result.overall_score >= 75

    def test_insights_and_next_steps_populated(self) -> None:
        result = self.scorer.evaluate(make_job(remote=True))
        assert len(result.insights) > 0
        assert len(result.next_steps) > 0

    def test_batch_evaluate_sorted_descending(self) -> None:
        jobs = [
            make_job(url="https://example.com/a",
                     description="No experience, quick apply"),
            make_job(url="https://example.com/b", title="Senior ML Engineer",
                     description="PhD, 10 years, system design"),
            make_job(url="https://example.com/c"),
        ]
        results = self.scorer.batch_evaluate(jobs)
        assert len(results) == 3
        scores = [r.overall_score for _, r in results]
        assert scores == sorted(scores, reverse=True)

    def test_get_recommended_filters_verdict(self) -> None:
        jobs = [
            make_job(url="https://example.com/good",
                     description="No experience, quick apply, start earning"),
            make_job(url="https://example.com/blocked",
                     description="Not available in Ethiopia."),
        ]
        recommended = self.scorer.get_recommended(jobs)
        assert len(recommended) == 1
        assert recommended[0][0].url == "https://example.com/good"

    def test_difficulty_dict_propagates_to_result(self) -> None:
        result = self.scorer.evaluate(make_job())
        assert "overall" in result.difficulty
        assert "overall_score" in result.difficulty
        assert isinstance(result.raw_scores["ease_of_application"], float)


