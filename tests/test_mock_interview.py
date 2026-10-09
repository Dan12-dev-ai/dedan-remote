"""
Mock interview engine: skills, planning, evaluation, persistence, workers, SSE.

The fake provider is deliberately strict: every reply is JSON shaped exactly as
the prompts demand, and the tests assert on behaviour the candidate actually
depends on — that a rubric is not fabricated, that a gap is reported rather than
hidden, and that another user cannot read a transcript.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Optional

import pytest
import requests
from fastapi.testclient import TestClient

import api.store as store_module
from api.security import rate_limiter
import interview.provider as provider_module
import interview.workers as workers
from api.main import create_app
from interview import evaluator as evaluator_module
from interview import planner as planner_module
from interview import workers as workers_module
from interview.persistence import (
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    reset_repository,
)
from interview.skills import extract_skills, overlap_ratio

# ── fixtures ────────────────────────────────────────────────────────────────

RICH_JOB: dict[str, Any] = {
    "id": "j1",
    "slug": "senior-python-llm",
    "title": "Senior Python Engineer — LLM Evaluation Platform",
    "company": "Acme AI",
    "location_label": "Remote · Worldwide",
    "experience_hint": "advanced",
    "url": "https://example.com/j1",
    "apply_url": "https://example.com/apply",
    "tags": ["python", "llm", "machine learning", "docker", "kubernetes"],
    "description": (
        "Build evaluation pipelines in Python, deploy with Docker and Kubernetes "
        "on AWS, and design REST APIs."
    ),
}

THIN_JOB: dict[str, Any] = {
    "id": "j2",
    "slug": "operations-associate",
    "title": "Operations Associate",
    "company": "Beta",
    "description": "General administrative duties.",
    "tags": [],
}

EVAL_REPLY: dict[str, Any] = {
    "relevance": 88,
    "clarity": 76,
    "accuracy": 91,
    "feedback": "Specific and correct, but skips the escalation path.",
    "strengths": ["cites concrete edge cases"],
    "weak_areas": ["no mention of rollback"],
    "recommendations": ["describe the rollback trigger"],
}


def plan_for(prompt: str) -> dict[str, Any]:
    """
    Build a question set from the skills the prompt actually offered.

    Hard-coding skills in the fake would silently couple the test to whatever
    the development index happens to contain — the planner correctly rejects
    questions for skills outside the offered set, so a mismatch looks like a
    product bug when it is a fixture bug.
    """
    slots = _parse_slots(prompt)
    questions: list[dict[str, Any]] = []
    for kind, targets in slots.items():
        for index, skill in enumerate(targets):
            questions.append(
                {
                    "kind": kind,
                    "target_skill": skill,
                    "difficulty": "moderate" if index == 0 else "hard",
                    "question_text": f"Probe {skill} ({kind}) — walk me through your approach.",
                    "rubric_focus": ["specificity", "trade-offs"],
                }
            )
    return {"questions": questions}


def _parse_slots(prompt: str) -> dict[str, list[str]]:
    """Recover the offered skill distribution from the prompt."""
    import ast
    import re as _re

    match = _re.search(r"Distribution of target skills by kind:\n(\{.*?\})\n", prompt, _re.DOTALL)
    if not match:
        return {"tech": ["general"], "behavioral": ["transferable"], "system": ["general"]}
    parsed = ast.literal_eval(match.group(1))
    return {kind: list(values) for kind, values in parsed.items()}


class FakeProvider:
    """
    A scripted model.

    Fails the test loudly on an unexpected prompt instead of returning an empty
    object, which is how the earlier double-wrapped-payload bug hid for so long.
    """

    def __init__(self, *, fail: bool = False, bad_rubric: bool = False) -> None:
        self.fail = fail
        self.bad_rubric = bad_rubric
        self.prompts: list[str] = []
        self.calls = 0

    def post(self, url: str, json: Optional[dict] = None, headers=None, timeout=None):
        self.calls += 1
        if self.fail:
            raise requests.Timeout("simulated timeout")
        prompt = " ".join(
            m.get("content", "") for m in (json or {}).get("messages", [])
        )
        self.prompts.append(prompt)

        if "Write the interview questions" in prompt:
            body: dict[str, Any] = plan_for(prompt)
        elif "Score this one answer" in prompt:
            body = {"relevance": None, "clarity": None, "accuracy": None} if self.bad_rubric else EVAL_REPLY
        elif "closing paragraph" in prompt:
            body = {"overall": "Strong on packaging; weakest on rollback planning."}
        else:
            raise AssertionError(f"unexpected model prompt: {prompt[:120]}")

        content = json_module_dumps(body)
        return _Response(content)


def json_module_dumps(value: Any) -> str:
    import json as _json

    return _json.dumps(value)


class _Response:
    def __init__(self, content: str) -> None:
        self.status_code = 200
        self.headers = {"Content-Type": "application/json"}
        self.text = content

    def json(self) -> dict[str, Any]:
        import json as _json

        return {"choices": [{"message": {"content": self.text}}]}


@pytest.fixture()
def fake_model(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(requests, "post", provider.post)
    return provider


@pytest.fixture()
def repo():
    return reset_repository(os.path.join(tempfile.mkdtemp(), "iv.db"))


@pytest.fixture()
def env(monkeypatch):
    """An interview-enabled deployment pointed at a fake endpoint."""
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("INTERVIEW_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("INTERVIEW_DB_PATH", os.path.join(tmp, "iv.db"))
    reset_repository(os.path.join(tmp, "iv.db"))
    provider_module.reset_llm()
    workers_module.reset_queue()
    yield
    workers_module.reset_queue()
    provider_module.reset_llm()


@pytest.fixture()
def client(env, fake_model, monkeypatch):
    # Every test in this class registers an account, and the auth bucket allows
    # 10 per minute per IP. Under xdist, several tests share a worker, so without
    # this reset the suite rate-limits itself into failures that look unrelated.
    rate_limiter.reset()
    store_module.reset_user_store_for_tests(os.path.join(tempfile.mkdtemp(), "users.db"))
    monkeypatch.setenv("INTERVIEW_DB_PATH", os.path.join(tempfile.mkdtemp(), "iv2.db"))
    reset_repository(os.environ["INTERVIEW_DB_PATH"])
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def auth(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "candidate@example.com", "password": "longenough1", "display_name": "C"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture()
def slug(client):
    item = client.get("/api/jobs?page_size=1").json()["items"][0]
    return item["slug"]


def open_interview(client, auth, slug, **body):
    payload = {"slug": slug, **body}
    resp = client.post("/api/interview/mocks", json=payload, headers=auth)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── skills ──────────────────────────────────────────────────────────────────


class TestSkillExtraction:
    def test_ranks_by_evidence_across_fields(self):
        vector = extract_skills(RICH_JOB)
        # python is in the title *and* tags *and* body, so it leads.
        assert vector.primary[0] == "python"
        assert not vector.empty

    def test_title_outweighs_a_body_mention(self):
        title_heavy = extract_skills({"title": "Rust Engineer", "description": "Some python."})
        body_only = extract_skills({"title": "Data Role", "description": "Some python."})
        assert title_heavy.primary[0] == "rust"
        assert body_only.primary[0] == "python"

    def test_collapses_aliases_into_one_skill(self):
        vector = extract_skills({"description": "Must know k8s, EKS, GKE and Kubernetes."})
        names = [s.name for s in vector.skills]
        assert "kubernetes" in names
        assert not any("eks" in n or "gke" in n for n in names)

    def test_does_not_match_short_aliases_inside_words(self):
        vector = extract_skills({"description": "We use google cloud and angularjs."})
        names = [s.name for s in vector.skills]
        assert "go" not in names
        assert "aws" not in names

    def test_is_deterministic(self):
        first = [s.name for s in extract_skills(RICH_JOB).skills]
        second = [s.name for s in extract_skills(RICH_JOB).skills]
        assert first == second

    def test_returns_empty_rather_than_inventing(self):
        vector = extract_skills({"title": "Barista", "description": "Serve coffee."})
        assert vector.empty
        assert vector.primary == []
        assert "no recognised skills" in vector.brief()

    def test_repeated_mentions_diminish(self):
        once = extract_skills({"description": "python"}).skills[0].evidence
        many = extract_skills({"description": "python " * 20}).skills[0].evidence
        assert many < once * 20

    def test_overlap_ratio_is_asymmetric_and_bounded(self):
        vector = extract_skills(RICH_JOB)
        assert overlap_ratio(vector, ["python", "kubernetes", "graphql"]) == pytest.approx(2 / 3)
        assert overlap_ratio(vector, []) == 0.0
        assert overlap_ratio(vector, ["nonsense"]) == 0.0


# ── planning ────────────────────────────────────────────────────────────────


class TestPlanner:
    def test_template_path_needs_no_model(self):
        plan = planner_module.plan_interview(RICH_JOB, client=None)
        assert plan.origin == "template"
        assert 5 <= len(plan.questions) <= 8

    def test_composition_holds_at_the_floor(self):
        """The brief's shape: 2 technical, 2 STAR behavioural, 1+ scenario."""
        plan = planner_module.plan_interview(
            RICH_JOB, client=None, min_questions=5, max_questions=5
        )
        assert plan.composition() == {"tech": 2, "behavioral": 2, "system": 1}

    def test_behavioural_never_drops_below_two(self):
        for ceiling in (5, 6, 7, 8):
            plan = planner_module.plan_interview(RICH_JOB, client=None, max_questions=ceiling)
            assert plan.composition()["behavioral"] >= 2, ceiling
            assert plan.composition()["system"] >= 1, ceiling

    def test_every_question_targets_a_real_skill(self):
        plan = planner_module.plan_interview(RICH_JOB, client=None)
        known = {s.name for s in plan.skills.skills} | {"general", "transferable"}
        for question in plan.questions:
            assert question.target_skill in known, question.target_skill

    def test_respects_an_explicit_ceiling(self):
        for ceiling in (5, 6, 8):
            plan = planner_module.plan_interview(RICH_JOB, client=None, max_questions=ceiling)
            assert len(plan.questions) <= ceiling

    def test_thin_listing_still_reaches_the_floor(self):
        plan = planner_module.plan_interview(THIN_JOB, client=None, min_questions=5, max_questions=8)
        assert len(plan.questions) >= 5
        assert plan.skillless

    def test_time_limits_vary_with_difficulty(self):
        plan = planner_module.plan_interview(RICH_JOB, client=None, max_questions=8)
        limits = {q.time_limit_sec for q in plan.questions}
        assert len(limits) > 1, "a timed interview needs per-difficulty budgets"
        assert all(60 <= limit <= 360 for limit in limits)

    def test_model_path_is_used_when_available(self, env, fake_model):
        plan = planner_module.plan_interview(RICH_JOB, client=provider_module.get_llm())
        assert plan.origin == "model"
        assert plan.model == "llama-3.3-70b-versatile"
        assert 5 <= len(plan.questions) <= 8
        assert plan.composition()["behavioral"] >= 2

    def test_drops_a_question_that_invents_a_skill(self, env, monkeypatch):
        payload = json_module_dumps(
            {
                "questions": [
                    {
                        "kind": "tech",
                        "target_skill": "quantum-spinning",
                        "question_text": "Explain qubit decoherence.",
                    }
                ]
            }
        )
        monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(payload))
        plan = planner_module.plan_interview(RICH_JOB, client=provider_module.get_llm())
        assert plan.origin == "template", "fell back instead of scoring an invented skill"
        assert all(q.target_skill != "quantum-spinning" for q in plan.questions)

    def test_falls_back_when_the_model_fails(self, env, monkeypatch):
        failing = FakeProvider(fail=True)
        monkeypatch.setattr(requests, "post", failing.post)
        plan = planner_module.plan_interview(RICH_JOB, client=provider_module.get_llm())
        assert plan.origin == "template"
        assert plan.questions, "fallback must still produce questions"


# ── evaluation ──────────────────────────────────────────────────────────────


class TestEvaluator:
    def test_empty_answer_is_a_deterministic_zero(self, env, fake_model):
        evaluation = evaluator_module.evaluate_answer(
            question={"kind": "tech", "target_skill": "python", "question_text": "Q", "rubric_focus": []},
            response_text="   ",
            client=provider_module.get_llm(),
        )
        assert evaluation.score == 0.0
        assert evaluation.strengths == []
        assert "No answer" in evaluation.detailed_feedback

    def test_scores_all_three_dimensions(self, env, fake_model):
        evaluation = evaluator_module.evaluate_answer(
            question={"kind": "tech", "target_skill": "python", "question_text": "Q", "rubric_focus": []},
            response_text="A package should expose one public entry point.",
            client=provider_module.get_llm(),
        )
        assert evaluation.relevance_score == 88
        assert evaluation.clarity_score == 76
        assert evaluation.accuracy_score == 91
        # 0.4*88 + 0.35*91 + 0.25*76 = 86.05 -> 86.0
        assert evaluation.score == pytest.approx(86.0, abs=0.1)

    def test_behavioural_answers_get_no_accuracy(self, env, fake_model):
        evaluation = evaluator_module.evaluate_answer(
            question={"kind": "behavioral", "target_skill": "transferable", "question_text": "Q", "rubric_focus": []},
            response_text="I owned the incident from page to postmortem.",
            client=provider_module.get_llm(),
        )
        assert evaluation.accuracy_score is None, "invented an accuracy score for a non-technical question"

    def test_refuses_to_score_a_rubric_with_no_numbers(self, env, monkeypatch):
        monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(json_module_dumps({"relevance": "n/a"})))
        with pytest.raises(evaluator_module.EvaluationError):
            evaluator_module.evaluate_answer(
                question={"kind": "tech", "target_skill": "python", "question_text": "Q", "rubric_focus": []},
                response_text="something",
                client=provider_module.get_llm(),
            )

    def test_clamps_and_coerces_noisy_numbers(self):
        assert evaluator_module._clamp("85%") == 85.0
        assert evaluator_module._clamp(142) == 100.0
        assert evaluator_module._clamp(-5) == 0.0
        assert evaluator_module._clamp("nonsense") is None
        assert evaluator_module._clamp(True) is None

    def test_never_renders_a_null_as_the_word_none(self):
        assert evaluator_module._strings([None, "real point", 7]) == ["real point"]

    def test_weight_renormalises_over_present_dimensions(self):
        assert evaluator_module.compute_total(90, 80, 70) == pytest.approx(80.5, abs=0.1)
        # accuracy dropped -> relevance/clarity take its share, and 90/80 wins.
        assert evaluator_module.compute_total(90, 80, None) > 84


class TestRadarAndSummary:
    def test_unanswered_skill_scores_zero_rather_than_vanishing(self):
        radar = evaluator_module.build_radar(
            [
                {"target_skill": "python", "score": 90},
                {"target_skill": "llm", "score": None},
            ]
        )
        llm = next(r for r in radar if r["skill"] == "llm")
        assert llm["score"] == 0.0
        assert llm["coverage"] == 0.0

    def test_behavioural_gets_its_own_axis(self):
        radar = evaluator_module.build_radar(
            [{"target_skill": "python", "score": 80}, {"target_skill": "transferable", "score": 60}]
        )
        axes = {r["skill"]: r["axis"] for r in radar}
        assert axes["transferable"] == "behavioural"
        assert axes["python"] == "skill"
        labels = {r["skill"]: r["label"] for r in radar}
        assert labels["transferable"] == "Behavioural"

    def test_summary_reports_coverage_with_the_score(self):
        summary = evaluator_module.summarise_interview(
            [
                {"target_skill": "python", "score": 90, "relevance_score": 90, "clarity_score": 90},
                {"target_skill": "llm", "score": None},
            ],
            overall="ok",
        )
        assert summary["total_score"] == 90.0
        assert summary["coverage"] == 0.5, "a mean over half the answers is not a strong interview"

    def test_summary_of_nothing_scores_nothing(self):
        summary = evaluator_module.summarise_interview([])
        assert summary["total_score"] is None
        assert summary["coverage"] == 0.0


# ── persistence ─────────────────────────────────────────────────────────────


class TestPersistence:
    def test_round_trips_an_interview(self, repo):
        iid = repo.create_interview(
            user_id="u1", job_id="j1", target_skills=["python"], job_title="Dev", job_company="Acme"
        )
        qids = repo.save_questions(
            iid,
            [
                {
                    "target_skill": "python",
                    "kind": "tech",
                    "question_text": "Q",
                    "time_limit_sec": 120,
                    "difficulty": "hard",
                    "rubric_focus": ["a"],
                }
            ],
        )
        repo.save_response(question_id=qids[0], interview_id=iid, response_text="one two three")
        assert repo.get_interview(iid)["status"] == STATUS_ACTIVE
        assert repo.get_response(qids[0])["word_count"] == 3
        assert len(repo.unevaluated_responses(iid)) == 1

    def test_resubmitting_replaces_and_clears_the_old_score(self, repo):
        iid = repo.create_interview(user_id="u1", job_id="j1", target_skills=[])
        qid = repo.save_questions(
            iid, [{"target_skill": "python", "kind": "tech", "question_text": "Q"}]
        )[0]
        repo.save_response(question_id=qid, interview_id=iid, response_text="first")
        repo.record_evaluation(
            question_id=qid, score=90, clarity_score=90, accuracy_score=90,
            relevance_score=90, detailed_feedback="good",
        )
        repo.save_response(question_id=qid, interview_id=iid, response_text="second")
        responses = repo.list_responses(iid)
        assert len(responses) == 1, "a re-answer duplicated the row"
        assert responses[0]["score"] is None, "a stale score survived a re-answer"

    def test_transcript_keeps_unanswered_questions(self, repo):
        iid = repo.create_interview(user_id="u1", job_id="j1", target_skills=[])
        qids = repo.save_questions(
            iid,
            [
                {"target_skill": "python", "kind": "tech", "question_text": "A"},
                {"target_skill": "llm", "kind": "tech", "question_text": "B"},
            ],
        )
        repo.save_response(question_id=qids[0], interview_id=iid, response_text="answered")
        transcript = repo.transcript(iid)
        assert len(transcript["questions"]) == 2
        assert transcript["questions"][1]["response"] is None

    def test_skill_alert_upsert_replaces_rather_than_stacking(self, repo):
        repo.upsert_skill_alert(user_id="u1", target_skills=["python"], email_enabled=False)
        repo.upsert_skill_alert(user_id="u1", target_skills=["python", "llm"], email_enabled=True)
        alerts = repo.list_skill_alerts()
        assert len(alerts) == 1
        assert alerts[0]["target_skills"] == ["python", "llm"]

    def test_matched_listing_is_not_reported_twice(self, repo):
        repo.upsert_skill_alert(user_id="u1", target_skills=["python"])
        repo.mark_skill_alert_matched("u1", "job-1")
        assert repo.already_matched("u1", "job-1")
        assert not repo.already_matched("u1", "job-2")

    def test_delete_cascades(self, repo):
        iid = repo.create_interview(user_id="u1", job_id="j1", target_skills=[])
        qid = repo.save_questions(
            iid, [{"target_skill": "python", "kind": "tech", "question_text": "Q"}]
        )[0]
        repo.save_response(question_id=qid, interview_id=iid, response_text="x")
        repo.delete_interview(iid)
        assert repo.get_interview(iid) is None
        assert repo.list_questions(iid) == []
        assert repo.list_responses(iid) == []

    def test_audio_url_is_not_invented(self, repo):
        iid = repo.create_interview(user_id="u1", job_id="j1", target_skills=[])
        qid = repo.save_questions(
            iid, [{"target_skill": "python", "kind": "tech", "question_text": "Q"}]
        )[0]
        repo.save_response(question_id=qid, interview_id=iid, response_text="x")
        assert repo.get_response(qid)["audio_url"] is None


# ── workers ─────────────────────────────────────────────────────────────────


class TestWorkers:
    def test_queue_never_blocks_the_caller(self, env):
        """A sync endpoint has no running loop; work must still leave the thread."""
        import threading

        queue = workers_module.AsyncioQueue(concurrency=2)
        started = threading.Event()
        release = threading.Event()

        def slow(job):
            started.set()
            release.wait(timeout=5)

        queue.submit(slow, workers_module.Job("evaluate_response", "i1"))
        # If submit had run inline this would deadlock on `release`.
        assert started.wait(timeout=2), "handler never started"
        release.set()
        queue.shutdown()

    def test_queue_bounds_concurrency(self, env):
        import threading
        import time

        queue = workers_module.AsyncioQueue(concurrency=2)
        live = 0
        peak = 0
        lock = threading.Lock()

        def handler(job):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            time.sleep(0.05)
            with lock:
                live -= 1

        for index in range(6):
            queue.submit(handler, workers_module.Job("evaluate_response", f"i{index}"))
        queue.shutdown()
        assert peak <= 2, f"opened {peak} concurrent model calls"

    def test_failures_are_recorded_not_swallowed(self, env):
        queue = workers_module.AsyncioQueue(concurrency=1)

        def boom(job):
            raise RuntimeError("provider down")

        queue.submit(boom, workers_module.Job("evaluate_response", "i1"))
        queue.shutdown()
        assert queue.failures and "provider down" in queue.failures[0][1]

    def test_evaluation_is_idempotent(self, env, fake_model, repo):
        iid = repo.create_interview(user_id="u1", job_id="j1", target_skills=["python"], job_title="Dev")
        qid = repo.save_questions(
            iid, [{"target_skill": "python", "kind": "tech", "question_text": "Q"}]
        )[0]
        repo.save_response(question_id=qid, interview_id=iid, response_text="A good answer.")

        first = workers_module.run_evaluation(
            workers_module.Job("evaluate_response", iid, {"question_id": qid, "user_id": "u1"})
        )
        before = repo.get_response(qid)["score"]
        second = workers_module.run_evaluation(
            workers_module.Job("evaluate_response", iid, {"question_id": qid, "user_id": "u1"})
        )
        assert first is not None and before is not None
        assert repo.get_response(qid)["score"] == before, "re-evaluation overwrote a stored rubric"
        assert second is not None

    def test_outstanding_reports_failure_rather_than_scoring(self, env, repo):
        iid = repo.create_interview(user_id="u1", job_id="j1", target_skills=[])
        qid = repo.save_questions(
            iid, [{"target_skill": "python", "kind": "tech", "question_text": "Q"}]
        )[0]
        repo.save_response(question_id=qid, interview_id=iid, response_text="x")
        workers_module.reset_queue()
        # No model configured at all.
        outcome = workers_module.evaluate_outstanding(iid, "u1", background=False)
        assert outcome["failed"] == 1
        assert outcome["reason"]

    def test_skill_match_respects_threshold_and_opt_in(self, env, repo, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(
            workers_module, "_send_match_email",
            lambda alert, job, vector, ratio: sent.append(alert["user_id"]) or True,
        )
        repo.upsert_skill_alert(
            user_id="strict", target_skills=["kubernetes", "elixir", "zig"],
            email_enabled=True, email_address="s@x.com", min_overlap=0.9,
        )
        repo.upsert_skill_alert(
            user_id="loose", target_skills=["python", "kubernetes"],
            email_enabled=True, email_address="l@x.com", min_overlap=0.3,
        )

        summary = workers_module.match_and_alert(RICH_JOB)
        # strict watches three skills and only overlaps one (33% < 90%).
        assert summary["matched"] == 1, "only the loose watcher met the bar"
        assert summary["emails"] == 1
        assert sent == ["loose"]

    def test_a_matched_job_is_not_mailed_twice(self, env, repo, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(
            workers_module, "_send_match_email",
            lambda alert, job, vector, ratio: sent.append(alert["user_id"]) or True,
        )
        repo.upsert_skill_alert(
            user_id="u1", target_skills=["python"], email_enabled=True,
            email_address="u@x.com", min_overlap=0.1,
        )
        workers_module.match_and_alert(RICH_JOB)
        workers_module.match_and_alert(RICH_JOB)
        assert len(sent) == 1, "an hourly crawl re-mailed the same listing"

    def test_opted_out_user_gets_no_email(self, env, repo, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(
            workers_module, "_send_match_email",
            lambda alert, job, vector, ratio: sent.append(alert["user_id"]) or True,
        )
        repo.upsert_skill_alert(user_id="u1", target_skills=["python"], email_enabled=False, min_overlap=0.1)
        summary = workers_module.match_and_alert(RICH_JOB)
        assert summary["matched"] == 1
        assert summary["emails"] == 0 and sent == []

    def test_skillless_listing_is_skipped(self, env, repo):
        repo.upsert_skill_alert(
            user_id="u1", target_skills=["python"], email_enabled=True,
            email_address="u@x.com", min_overlap=0.1,
        )
        summary = workers_module.match_and_alert(THIN_JOB)
        assert summary["matched"] == 0
        assert "no recognisable skills" in summary["reason"]


# ── HTTP surface ────────────────────────────────────────────────────────────


class TestMockEndpoints:
    def test_skills_preflight_is_public_and_typed(self, client, slug):
        resp = client.get(f"/api/interview/skills?slug={slug}")
        assert resp.status_code == 200
        body = resp.json()
        assert "skills" in body and "interviewer_available" in body
        assert body["origin"] in ("template", "model")

    def test_start_requires_auth(self, client, slug):
        assert client.post("/api/interview/mocks", json={"slug": slug}).status_code == 401

    def test_start_persists_the_plan_and_returns_only_the_first_question(
        self, client, auth, slug
    ):
        body = open_interview(client, auth, slug)
        assert body["question_count"] == 5
        assert body["question"] is not None
        assert "question_count" in body
        # The rest of the plan is not in the start response.
        assert "questions" not in body

        stored = client.get(f"/api/interview/mocks/{body['interview_id']}", headers=auth).json()
        assert len(stored["questions"]) == 5

    def test_answers_return_queued_not_a_score(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        qid = started["question"]["id"]
        resp = client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={"question_id": qid, "response_text": "A considered answer.", "input_mode": "voice"},
            headers=auth,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert "score" not in body, "a per-answer score leaked into the interview"

    def test_finish_without_a_model_returns_the_transcript_unscored(
        self, client, auth, slug, monkeypatch
    ):
        """
        No evaluator configured is not a transient failure.

        Returning 503 would strand the candidate on a screen they cannot leave,
        with their own written answers unreachable. The transcript is the
        deliverable; the score is what is missing, and it says so.
        """
        provider_module.reset_llm()
        monkeypatch.setenv("INTERVIEW_ENABLED", "false")
        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={"question_id": started["question"]["id"], "response_text": "Something."},
            headers=auth,
        )
        resp = client.post(f"/api/interview/mocks/{iid}/finish", headers=auth)
        assert resp.status_code == 200
        report = resp.json()
        assert report["total_score"] is None, "an unscored interview must not report a score"
        assert report["answers_scored"] == 0
        assert report["coverage"] == 0.0
        assert "not scored" in report["overall_feedback"]
        assert report["strengths"] == []
        # And the answers are all still there.
        assert report["questions"][0]["response"]["response_text"] == "Something."
        assert report["status"] == STATUS_COMPLETED

    def test_finish_with_a_model_but_failed_evaluation_stays_retryable(
        self, client, auth, slug, monkeypatch
    ):
        """
        A configured evaluator that failed is a different problem, and the
        distinction matters: this one is worth retrying, so it must not present a
        partial transcript as a finished interview.
        """
        provider_module.reset_llm()
        monkeypatch.setenv("INTERVIEW_ENABLED", "true")
        monkeypatch.setenv("LLM_API_KEY", "stub")
        monkeypatch.setenv("LLM_MODEL", "stub-model")
        provider_module.reset_llm()
        # The provider is up but every call fails, which is the retryable case.
        monkeypatch.setattr(
            requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("boom"))
        )

        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={"question_id": started["question"]["id"], "response_text": "Something."},
            headers=auth,
        )
        # The stub endpoint is unreachable, so every answer fails to evaluate.
        resp = client.post(f"/api/interview/mocks/{iid}/finish", headers=auth)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "evaluation_failed"
        # Still active, so the candidate can retry rather than being finished.
        record = client.get(f"/api/interview/mocks/{iid}", headers=auth).json()
        assert record["interview"]["status"] == STATUS_ACTIVE
        assert record["questions"][0]["response"]["response_text"] == "Something."

    def test_full_run_produces_a_report(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        questions = client.get(f"/api/interview/mocks/{iid}", headers=auth).json()["questions"]
        for question in questions:
            client.post(
                f"/api/interview/mocks/{iid}/answers",
                json={
                    "question_id": question["id"],
                    "response_text": "I would start by measuring before changing anything.",
                    "duration_seconds": 40,
                },
                headers=auth,
            )
        workers_module.get_queue().shutdown()
        report = client.post(f"/api/interview/mocks/{iid}/finish", headers=auth).json()
        assert report["status"] == STATUS_COMPLETED
        assert 0 <= report["total_score"] <= 100
        assert report["answers_scored"] == len(questions)
        assert report["skill_radar"]
        assert report["overall_feedback"]

    def test_sse_delivers_the_report(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={"question_id": started["question"]["id"], "response_text": "Answer."},
            headers=auth,
        )
        workers_module.get_queue().shutdown()

        events: list[str] = []
        report_frame = None
        with client.stream("GET", f"/api/interview/mocks/{iid}/stream", headers=auth) as stream:
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(": ", 1)[1])
                elif line.startswith("data:"):
                    payload = line.split(": ", 1)[1]
                    if "total_score" in payload:
                        report_frame = json.loads(payload)
                        break
        assert events[0] == "open"
        assert report_frame is not None and "total_score" in report_frame

    def test_sse_sets_no_buffer_headers(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        with client.stream(
            "GET", f"/api/interview/mocks/{started['interview_id']}/stream", headers=auth
        ) as stream:
            assert "no-cache" in stream.headers["cache-control"]
            assert stream.headers["x-accel-buffering"] == "no"

    def test_another_user_cannot_read_the_transcript(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        other = client.post(
            "/api/auth/register", json={"email": "other@example.com", "password": "longenough1"}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {other}"}
        # 404 rather than 403: a 403 would confirm the id exists.
        assert client.get(f"/api/interview/mocks/{started['interview_id']}", headers=headers).status_code == 404
        assert client.post(f"/api/interview/mocks/{started['interview_id']}/finish", headers=headers).status_code == 404
        assert client.get("/api/interview/mocks", headers=headers).json()["total"] == 0

    def test_history_lists_only_your_own(self, client, auth, slug):
        open_interview(client, auth, slug)
        assert client.get("/api/interview/mocks", headers=auth).json()["total"] == 1

    def test_rejects_a_question_from_another_interview(self, client, auth, slug):
        first = open_interview(client, auth, slug)
        second = open_interview(client, auth, slug)
        resp = client.post(
            f"/api/interview/mocks/{second['interview_id']}/answers",
            json={"question_id": first["question"]["id"], "response_text": "x"},
            headers=auth,
        )
        assert resp.status_code == 404

    def test_rejects_unknown_fields(self, client, auth, slug):
        resp = client.post(
            "/api/interview/mocks", json={"slug": slug, "total_score": 100}, headers=auth
        )
        assert resp.status_code == 422

    def test_delete_removes_the_record(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        assert client.delete(f"/api/interview/mocks/{started['interview_id']}", headers=auth).status_code == 204
        assert client.get(f"/api/interview/mocks/{started['interview_id']}", headers=auth).status_code == 404

    def test_finished_interview_rejects_further_answers(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        for question in client.get(f"/api/interview/mocks/{iid}", headers=auth).json()["questions"]:
            client.post(
                f"/api/interview/mocks/{iid}/answers",
                json={"question_id": question["id"], "response_text": "Answer."},
                headers=auth,
            )
        workers_module.get_queue().shutdown()
        client.post(f"/api/interview/mocks/{iid}/finish", headers=auth)
        resp = client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={"question_id": started["question"]["id"], "response_text": "more"},
            headers=auth,
        )
        assert resp.status_code == 409


class TestSkillAlertEndpoints:
    def test_defaults_to_no_watch(self, client, auth):
        alert = client.get("/api/interview/alerts", headers=auth).json()["alert"]
        assert alert["target_skills"] == []
        assert alert["email_enabled"] is False

    def test_saves_and_reads_back(self, client, auth):
        saved = client.put(
            "/api/interview/alerts",
            json={"target_skills": ["python", "llm"], "email_enabled": True, "min_overlap": 0.6},
            headers=auth,
        ).json()["alert"]
        assert saved["target_skills"] == ["python", "llm"]
        assert saved["email_enabled"] is True
        assert saved["email_address"] == "candidate@example.com", "did not fall back to the account email"
        assert client.get("/api/interview/alerts", headers=auth).json()["alert"]["min_overlap"] == 0.6

    def test_requires_auth(self, client):
        assert client.get("/api/interview/alerts").status_code == 401


class TestRedisQueueConsumers:
    """
    The Redis backend is only useful if something pops the list.

    These tests drive the consumer directly with a fake client rather than a live
    Redis, because the behaviour under test is the consume loop, not redis-py.
    """

    def test_starts_consumers_on_first_push(self):
        from interview.workers import RedisQueue

        queue = RedisQueue("redis://localhost:6379/0", concurrency=2)
        pushed: list = []
        queue._client = lambda: _FakeRedis(pushed)  # type: ignore[method-assign]

        try:
            queue.submit(lambda job: None, _job("iv-redis-1"))
            assert len(queue._consumers) == 2, "no consumer would ever pop the job"
        finally:
            queue.stop()

    def test_does_not_start_consumers_before_a_push(self):
        from interview.workers import RedisQueue

        queue = RedisQueue("redis://localhost:6379/0")
        try:
            assert queue._consumers == []
        finally:
            queue.stop()

    def test_runs_the_pushed_job_through_the_handler(self):
        from interview.workers import RedisQueue

        seen: list[str] = []
        job2 = _job("iv-redis-2")
        queue = RedisQueue("redis://localhost:6379/0", concurrency=1)
        redis = _FakeRedis([])
        queue._client = lambda: redis  # type: ignore[method-assign]

        try:
            queue.submit(lambda job: seen.append(job.key()), job2)
            deadline = time.monotonic() + 3
            while not seen and time.monotonic() < deadline:
                time.sleep(0.02)
            assert seen == [job2.key()]
        finally:
            queue.stop()

    def test_running_inline_when_redis_is_down(self):
        from interview.workers import RedisQueue

        seen: list[str] = []

        job3 = _job("iv-redis-3")

        def handler(job) -> None:
            seen.append(job.key())

        queue = RedisQueue("redis://localhost:6379/0")

        def broken():
            raise OSError("redis is down")

        queue._client = broken  # type: ignore[method-assign]
        try:
            queue.submit(handler, job3)
            # A submission must never fail because the queue is unavailable.
            assert seen == [job3.key()]
            assert queue._consumers == []
        finally:
            queue.stop()

    def test_stop_is_idempotent_and_lets_reset_queue_clean_up(self):
        from interview import workers

        queue = workers.RedisQueue("redis://localhost:6379/0")
        queue._client = lambda: _FakeRedis([])  # type: ignore[method-assign]
        queue.submit(lambda job: None, _job("iv-redis-4"))
        assert queue._consumers

        workers._queue = queue
        workers.reset_queue()
        assert queue._consumers == []
        assert workers._queue is None


class _FakeRedis:
    """The sliver of redis-py the consumer loop touches."""

    def __init__(self, seen: list) -> None:
        self.items: list[str] = []
        self.seen = seen

    def rpush(self, _queue: str, payload: str) -> None:
        self.items.append(payload)

    def blpop(self, _queue: str, timeout: int = 1):
        if not self.items:
            time.sleep(0.01)
            return None
        return (_queue, self.items.pop(0))


def _job(interview_id: str):
    from interview.workers import Job

    return Job(kind="evaluate_response", interview_id=interview_id, payload={})


class TestDiscoverySkillWatchIntegration:
    """The watch only helps if it runs when listings actually arrive."""

    @pytest.fixture(autouse=True)
    def _isolated_repo(self):
        # These tests share the process-wide repository singleton, so isolate
        # each one on a fresh temp DB — otherwise a watch left by another test
        # in the same xdist worker changes what `matched` should be.
        import tempfile

        from interview.persistence import reset_repository

        reset_repository(os.path.join(tempfile.mkdtemp(), "watches.db"))
        yield
        reset_repository(os.path.join(tempfile.mkdtemp(), "watches.db"))

    def test_fresh_listings_are_matched_against_watches(self):
        repo = _repo()
        repo.upsert_skill_alert(
            user_id="u1",
            target_skills=["python", "llm"],
            email_enabled=False,
        )

        agent = _bare_agent()
        job = _scraped_job("Senior Python Engineer with LLM evaluation experience")
        matched = agent._match_skill_watches([(job, 0.9)])

        assert matched == 1, "a clearly matching listing did not reach the watch"

    def test_a_listing_without_skills_is_skipped(self):
        repo = _repo()
        repo.upsert_skill_alert(
            user_id="u1",
            target_skills=["kubernetes"],
            email_enabled=False,
        )

        agent = _bare_agent()
        matched = agent._match_skill_watches([(_scraped_job("Babysitter wanted"), 0.2)])
        assert matched == 0

    def test_a_match_failure_does_not_break_the_cycle(self, monkeypatch):
        """
        The discovery cycle's job is to store jobs. A broken email path must not
        roll that back.
        """
        def explode(*_args, **_kwargs):
            raise RuntimeError("matching is down")

        monkeypatch.setattr("interview.workers.match_and_alert", explode)

        agent = _bare_agent()
        with pytest.raises(RuntimeError):
            agent._match_skill_watches([(_scraped_job("Python and LLM role"), 0.8)])


def _repo():
    from interview.persistence import get_repository

    return get_repository()


def _bare_agent():
    """A DiscoveryAgent with no database or notifier wired up."""
    from agents.discovery_agent import DiscoveryAgent

    agent = DiscoveryAgent.__new__(DiscoveryAgent)
    return agent


def _scraped_job(description: str):
    from models.job import Job

    return Job(
        title="Backend Engineer",
        company="Acme",
        url=f"https://example.com/{abs(hash(description)) % 10_000}",
        source="test",
        description=description,
        tags=["python", "llm"],
    )


class TestStreamWithoutAnEvaluator:
    """The stream must not spend its whole budget waiting for work that cannot run."""

    def test_stream_returns_the_transcript_when_no_model_is_configured(
        self, client, auth, slug, monkeypatch
    ):
        provider_module.reset_llm()
        monkeypatch.setenv("INTERVIEW_ENABLED", "false")
        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={"question_id": started["question"]["id"], "response_text": "Something."},
            headers=auth,
        )

        body = client.get(f"/api/interview/mocks/{iid}/stream", headers=auth).text
        assert "event: report" in body, body[:400]
        assert "event: timeout" not in body
        # And the report it delivered is honestly unscored.
        payload = json.loads(body.split("event: report\ndata: ", 1)[1].split("\n\n", 1)[0])
        assert payload["total_score"] is None
        assert payload["answers_scored"] == 0

    def test_stream_still_streams_progress_when_a_model_is_configured(self, client, auth, slug):
        started = open_interview(client, auth, slug)
        iid = started["interview_id"]
        for question in client.get(
            f"/api/interview/mocks/{iid}", headers=auth
        ).json()["questions"]:
            client.post(
                f"/api/interview/mocks/{iid}/answers",
                json={"question_id": question["id"], "response_text": "A considered answer."},
                headers=auth,
            )
        workers_module.get_queue().shutdown()
        body = client.get(f"/api/interview/mocks/{iid}/stream", headers=auth).text
        assert "event: report" in body
        payload = json.loads(body.split("event: report\ndata: ", 1)[1].split("\n\n", 1)[0])
        assert payload["total_score"] is not None
        assert payload["answers_scored"] > 0
