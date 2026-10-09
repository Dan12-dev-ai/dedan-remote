"""
Live AI interview tests.

Two halves, deliberately:

1. **Rubric arithmetic** — pure functions, no model. These pin the honest part:
   what can be measured from a transcript without a language model.
2. **API contract** — driven through a stub provider so the whole flow
   (start → answers → finish) is exercised with no API key and no network.

A live interview must never silently degrade into a fabricated one, so there is
also a test that the endpoints refuse outright when no model is configured.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.store as store_module
from api.main import app
from api.security import rate_limiter
from interview import rubric
from interview.engine import InterviewEngine, build_role_profile
from interview.followup import FollowUpEngine
from interview.room import InterviewRoom
from interview.room_store import get_room_store, reset_room_store
from interview.store import InterviewStore
from interview.stream_ticket import reset_ticket_store

# ── Fixtures ────────────────────────────────────────────────────────────────


class StubLLM:
    """
    Stand-in for the real model.

    Returns a well-formed plan, well-formed per-turn scores and a well-formed
    debrief, so the API contract can be tested without a provider. It records
    every call so tests can assert the *shape* of what was sent — in
    particular that the role brief reaches the model.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.plan_calls = 0

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str:
        return ""

    def _require(self) -> tuple[str, str]:
        return "https://stub.invalid/v1/chat/completions", "stub-key"

    def complete_json(self, *, system: str, user: str, temperature=None, max_tokens=1400):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if "Plan a" in user and "questions" in user:
            self.plan_calls += 1
            return {
                "questions": [
                    {
                        "kind": "opening",
                        "prompt": "Introduce yourself and why this role caught your attention.",
                        "intent": "motivation and framing",
                        "targets": ["motivation"],
                    },
                    {
                        "kind": "role_depth",
                        "prompt": "Take me deep on the retrieval work you shipped last quarter.",
                        "intent": "depth on a named requirement",
                        "targets": ["retrieval"],
                    },
                ]
            }
        if "Write the debrief" in user:
            return {
                "headline": "Strong on retrieval, thin on evaluation.",
                "role_fit": "The posting names evaluation tooling; the answers covered retrieval in depth but never evaluation.",
                "evidence": ["we shipped the retrieval index in two weeks"],
                "what_they_proved": [
                    "Owned a retrieval index end to end",
                    "Reasoned about ranking trade-offs under latency budget",
                ],
                "what_they_did_not_show": [
                    "No evidence of evaluation harness design",
                    "Never mentioned the named vector database",
                ],
                "moves_the_rank": [
                    "Describe an evaluation harness you built and the metric it moved",
                ],
                "interviewer_notes": ["Answered the deep question without prompting"],
                "cheat_sheet": ["Rehearse one evaluation story for this posting"],
            }
        return {
            "summary": "Described owning a retrieval index under a latency budget.",
            "evidence": ["we shipped the retrieval index in two weeks"],
            "scores": {
                "role_depth": 78,
                "problem_solving": 70,
                "critical_thinking": 64,
                "communication": 72,
                "creativity": 58,
                "ownership": 80,
            },
            "note": "Good depth on retrieval; say more about how you measured it.",
            "follow_up": "Let's go one level deeper on that.",
            "probed": True,
        }


@pytest.fixture()
def stub_llm() -> StubLLM:
    return StubLLM()


@pytest.fixture()
def client():
    rate_limiter.reset()
    tmp = tempfile.mkdtemp(prefix="dedan_test_")
    store_module.reset_user_store_for_tests(os.path.join(tmp, "users.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def live(monkeypatch: pytest.MonkeyPatch):
    """
    Configure the app as if a live interview provider were available.

    Patched through the environment because ``get_settings()`` constructs a
    fresh Settings per call, so mutating one instance would not be seen by the
    request handler.
    """
    monkeypatch.setenv("INTERVIEW_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "stub-key")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    monkeypatch.setenv("INTERVIEW_MAX_QUESTIONS", "4")

    # ``get_llm`` caches a client process-wide, and that client captured the
    # settings it saw on first construction. Without this, a test that ran
    # earlier on the same xdist worker with INTERVIEW_ENABLED=false leaves the
    # cached client reporting the deployment as unavailable.
    import interview.provider as provider_module

    provider_module.reset_llm()
    yield
    provider_module.reset_llm()


@pytest.fixture()
def engine(stub_llm: StubLLM) -> InterviewEngine:
    return InterviewEngine(client=stub_llm, store=InterviewStore(ttl_seconds=600, max_sessions=10))


SAMPLE_JOB: dict[str, Any] = {
    "slug": "retrieval-engineer-remote",
    "title": "Retrieval Engineer (Remote)",
    "company": "Northwind",
    "location_label": "Worldwide",
    "salary": "$120k - $150k",
    "salary_disclosed": True,
    "category": "ai-ml",
    "experience_hint": "advanced",
    "tags": ["rag", "python", "vector search", "evaluation"],
    "description": (
        "You will build retrieval systems. We are looking for someone with strong "
        "Python and experience with vector search. Responsibilities include owning "
        "the indexing pipeline and designing an evaluation harness."
    ),
    "intelligence": {"insights": ["Latency budget is the hard constraint here."]},
    "score_explanation": {"reasons": [{"label": "Remote", "detail": "Fully remote listing"}]},
}


# ── Rubric arithmetic (no model) ────────────────────────────────────────────


class TestSpeechMeasurement:
    def test_counts_words_pace_and_duration(self):
        turns = [
            {"text": " ".join(["word"] * 150), "duration_seconds": 60},
            {"text": " ".join(["word"] * 90), "duration_seconds": 60},
        ]
        m = rubric.measure_speech(turns)
        assert m.turns == 2
        assert m.words == 240
        assert m.speaking_seconds == 120
        assert m.words_per_minute == 120.0

    def test_filler_rate_and_structure_markers(self):
        turns = [
            {
                "text": "um I think we basically um shipped it because the tradeoff was latency "
                "so that p99 stayed under budget and then we measured it",
                "duration_seconds": 20,
            }
        ]
        m = rubric.measure_speech(turns)
        assert m.filler_rate > 0
        assert m.structure_markers >= 2

    def test_counts_questions_asked_back(self):
        m = rubric.measure_speech(
            [{"text": "Would you consider Python? Yes.", "duration_seconds": 10}]
        )
        assert m.questions_asked_back == 1

    def test_empty_input_is_all_zero(self):
        m = rubric.measure_speech([])
        assert m.words == 0
        assert m.words_per_minute == 0.0

    def test_to_dict_is_json_friendly(self):
        m = rubric.measure_speech([{"text": "hello there", "duration_seconds": 5}])
        payload = m.to_dict()
        assert all(isinstance(v, (int, float)) for v in payload.values())


class TestSpeechScore:
    def test_short_speech_scores_zero(self):
        score, notes = rubric.speech_score(
            rubric.measure_speech([{"text": "ok", "duration_seconds": 1}])
        )
        assert score == 0.0
        assert notes

    def test_fast_pace_is_penalised_with_a_note(self):
        words = " ".join(["word"] * 400)
        score, notes = rubric.speech_score(
            rubric.measure_speech([{"text": words, "duration_seconds": 60}])
        )
        assert score < 100
        assert any("Pace" in n or "pace" in n for n in notes)

    def test_clean_delivery_scores_well(self):
        sentence = "We rebuilt the indexer so that p99 dropped because we batched writes."
        text = " ".join([sentence] * 8)
        score, _ = rubric.speech_score(
            rubric.measure_speech([{"text": text, "duration_seconds": 90}])
        )
        assert score >= 70


class TestBandsAndVerdicts:
    def test_band_lookup_by_level(self):
        assert rubric.band_for("advanced")[0] == 68.0
        assert rubric.band_for("senior")[0] == 78.0
        assert rubric.band_for("beginner")[0] == 48.0

    def test_band_vocabulary_aliases(self):
        assert rubric.band_for("principal")[0] == rubric.band_for("senior")[0]
        assert rubric.band_for("mid")[0] == rubric.band_for("intermediate")[0]

    def test_unknown_level_falls_back(self):
        bar, label = rubric.band_for("something else")
        assert bar > 0 and label

    @pytest.mark.parametrize(
        "overall,expected",
        [(95, "strong"), (64, "competitive"), (55, "borderline"), (10, "below_bar")],
    )
    def test_verdict_thresholds(self, overall, expected):
        assert rubric.verdict_for(overall, 58.0) == expected

    def test_weights_sum_to_one(self):
        assert abs(sum(d.weight for d in rubric.DIMENSIONS) - 1.0) < 1e-9

    def test_weighted_overall_respects_weights(self):
        scores = {d.key: (100.0 if d.key == "role_depth" else 0.0) for d in rubric.DIMENSIONS}
        assert rubric.weighted_overall(scores) == pytest.approx(26.0, abs=0.05)

    def test_communication_is_half_model_half_measured(self):
        assert rubric.combine_communication(100, 50) == pytest.approx(75.0)

    def test_strengths_and_gaps_are_ranked(self):
        scores = {
            "role_depth": 90,
            "problem_solving": 20,
            "critical_thinking": 50,
            "communication": 70,
            "creativity": 30,
            "ownership": 45,
        }
        strengths, gaps = rubric.strengths_gaps(scores)
        assert any("Role depth" in s for s in strengths)
        assert any("Problem solving" in g for g in gaps)


class TestRoleProfile:
    def test_profile_is_derived_from_the_listing(self):
        profile = build_role_profile(SAMPLE_JOB, SAMPLE_JOB["slug"])
        assert profile.title == "Retrieval Engineer (Remote)"
        assert profile.experience == "advanced"
        assert profile.pay == "$120k - $150k"
        assert any("vector search" in m.lower() for m in profile.must_haves)
        assert "Latency budget" in " ".join(profile.signals)

    def test_profile_never_invents_pay(self):
        job = dict(SAMPLE_JOB, salary=None, salary_disclosed=False)
        assert build_role_profile(job, "x").pay == "Not disclosed"

    def test_brief_is_a_plain_string(self):
        assert "Retrieval Engineer" in build_role_profile(SAMPLE_JOB, "x").as_brief()


# ── Engine (stub provider) ──────────────────────────────────────────────────


class TestEngineFlow:
    def test_start_returns_only_the_first_question(self, engine, stub_llm):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 6)
        public = session.to_public()
        assert public["status"] == "live"
        assert public["answered"] == 0
        assert public["question"]["prompt"]
        # The plan must not leak past the first question.
        assert "questions" not in public
        assert len(session.plan) >= 3

    def test_thin_plan_is_padded_to_the_requested_count(self, engine):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 6)
        assert len(session.plan) == 6
        kinds = {q["kind"] for q in session.plan}
        assert {"opening", "role_depth", "problem_solving", "closing"} <= kinds

    def test_plan_count_is_clamped(self, engine):
        assert len(engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 99).plan) <= 10
        assert len(engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 1).plan) >= 3

    def test_submit_does_not_return_scores(self, engine):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 4)
        result = engine.submit(session.id, "I owned the retrieval index end to end.", 42)
        assert "scores" not in result
        assert "feedback" not in result
        assert result["answered"] == 1
        assert result["complete"] is False

    def test_short_answers_are_not_scored(self, engine, stub_llm):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 4)
        engine.submit(session.id, "yes", 1)
        assert session.turns[0]["evaluation"]["error"]

    def test_model_failure_does_not_destroy_the_interview(self, engine, stub_llm, monkeypatch):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 4)

        def boom(*args, **kwargs):
            from interview.provider import ProviderError

            raise ProviderError("model exploded")

        monkeypatch.setattr("interview.engine.evaluate_turn", boom)
        result = engine.submit(
            session.id,
            "I owned the retrieval index end to end and measured p99 latency.",
            30,
        )
        assert result["answered"] == 1
        assert session.turns[0]["evaluation"]["error"] == "model exploded"

    def test_finish_produces_ranked_report(self, engine):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 4)
        engine.submit(session.id, "I owned the retrieval index and measured p99 latency.", 55)
        engine.submit(session.id, "I would design an evaluation harness before tuning.", 48)
        report = engine.finish(session.id)

        m = report["measurement"]
        assert m["band"] == 68.0
        assert m["verdict"] in {"strong", "competitive", "borderline", "below_bar"}
        assert m["questions_asked"] == 4
        assert m["questions_answered"] == 2
        assert 0 <= m["overall"] <= 100

        # Every dimension is present with its published weight.
        assert len(report["dimensions"]) == len(rubric.DIMENSIONS)
        assert report["communication_split"]["model_read"] == pytest.approx(72.0)
        assert "measured" in report["communication_split"]

        assert report["role_analysis"]["headline"]
        assert report["role_analysis"]["what_they_did_not_show"]
        assert report["speech"]["words"] > 0
        assert report["limitations"]

    def test_report_states_its_own_limits(self, engine):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 3)
        engine.submit(session.id, "A long enough answer about the retrieval index work.", 40)
        report = engine.finish(session.id)
        assert any("not a hiring decision" in n for n in report["limitations"])

    def test_finish_is_idempotent(self, engine):
        session = engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 3)
        engine.submit(session.id, "A long enough answer about the retrieval index work.", 40)
        first = engine.finish(session.id)
        assert engine.finish(session.id)["measurement"] == first["measurement"]

    def test_role_brief_reaches_the_model(self, engine, stub_llm):
        engine.start(SAMPLE_JOB, SAMPLE_JOB["slug"], 4)
        assert any("Retrieval Engineer" in c["user"] for c in stub_llm.calls)

    def test_unknown_session_raises(self, engine):
        with pytest.raises(KeyError):
            engine.submit("nope", "hello", 5)


# ── Store ───────────────────────────────────────────────────────────────────


class TestStore:
    def test_evicts_expired_sessions(self):
        from datetime import datetime, timedelta, timezone

        store = InterviewStore(ttl_seconds=1, max_sessions=5)
        session = engine_session(store)
        session.created_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        with pytest.raises(KeyError):
            store.get(session.id)

    def test_caps_total_sessions(self):
        store = InterviewStore(ttl_seconds=600, max_sessions=2)
        for _ in range(4):
            store.put(engine_session(store))
        assert store.count() <= 3

    def test_pop_removes(self):
        store = InterviewStore(ttl_seconds=600, max_sessions=5)
        session = store.put(engine_session(store))
        store.pop(session.id)
        assert store.count() == 0


def engine_session(store: InterviewStore):
    from datetime import datetime, timezone

    from interview.engine import InterviewSession

    return InterviewSession(
        id=store.new_id(),
        profile=build_role_profile(SAMPLE_JOB, "x"),
        plan=[{"id": "q1", "kind": "opening", "prompt": "Hi", "intent": "", "targets": []}],
        created_at=datetime.now(timezone.utc),
    )


# ── HTTP contract ───────────────────────────────────────────────────────────


@pytest.fixture()
def auth(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "room@example.com", "password": "longenough1", "display_name": "Room"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture()
def slug(client):
    item = client.get("/api/jobs?page_size=1").json()["items"][0]
    return item["slug"]


@pytest.fixture()
def room(client, live, monkeypatch):
    """
    Point the router at a stub-backed room over a throwaway database.

    The stub must be injected through `get_room`, which is what the router calls
    on every request — replacing the router's engine factory, as the old live
    flow's test did, would no longer be where the work happens.
    """
    monkeypatch.setenv("INTERVIEW_DB_PATH", os.path.join(tempfile.mkdtemp(), "room.db"))
    reset_room_store(os.environ["INTERVIEW_DB_PATH"])
    reset_ticket_store()
    stub = StubLLM()
    monkeypatch.setattr(
        "api.routers.interview.get_room",
        lambda: InterviewRoom(
            client=stub,
            store=get_room_store(),
            followups=FollowUpEngine(client=stub),
        ),
    )
    return stub


def _open_room(client, auth, slug, **body) -> dict[str, Any]:
    resp = client.post("/api/interview/sessions", json={"opportunity": slug, **body}, headers=auth)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestInterviewApi:
    """
    The HTTP contract for the Interview Room.

    Every test here registers an account: an interview is a record about a
    person, so the room API requires sign-in. The browser-only rehearsal mode
    remains anonymous — a different product that stores nothing.
    """

    def test_status_is_honest_when_unconfigured(self, client, monkeypatch):
        monkeypatch.setenv("INTERVIEW_ENABLED", "false")
        monkeypatch.setenv("LLM_API_KEY", "stub-key")
        body = client.get("/api/interview/status").json()
        assert body["live"] is False
        assert body["mode"] == "unavailable"
        assert body["detail"]

    def test_status_never_leaks_provider_details(self, client, live):
        body = client.get("/api/interview/status").json()
        assert body["live"] is True
        assert "stub-model" not in str(body)
        assert "stub-key" not in str(body)

    def test_status_publishes_shape_and_privacy(self, client, live):
        body = client.get("/api/interview/status").json()
        assert body["default_questions"] >= 3
        assert "role_rehearsal" in body["interview_types"]
        # The privacy block is read from settings, so it cannot drift from what
        # the deployment actually does.
        privacy = body["privacy"]
        assert privacy["audio_recorded"] is False
        assert privacy["audio_transmitted"] is False
        assert privacy["transcript_stored"] is True
        assert privacy["retention_days"] > 0

    def test_start_requires_a_model(self, client, auth, monkeypatch):
        monkeypatch.setenv("INTERVIEW_ENABLED", "false")
        monkeypatch.setenv("LLM_API_KEY", "stub-key")
        resp = client.post(
            "/api/interview/sessions", json={"opportunity": "anything"}, headers=auth
        )
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "interview_not_configured"

    def test_opening_a_room_requires_sign_in(self, client, live):
        resp = client.post("/api/interview/sessions", json={"opportunity": "anything"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "auth_required"

    def test_opening_a_room_returns_the_brief_not_a_question(self, client, live, auth, slug, room):
        payload = _open_room(client, auth, slug)
        # The preparation screen gets orientation, never the first question.
        assert payload["current"] is None
        assert payload["state"] == "ready"
        assert payload["phase"] == "ready"
        assert payload["progress"]["position"] == 0
        assert payload["interview"]["focus"]
        assert payload["interview"]["duration_minutes"] > 0
        assert payload["privacy"]["audio_recorded"] is False

    def test_the_plan_never_reaches_the_browser(self, client, live, auth, slug, room):
        payload = _open_room(client, auth, slug)
        # The response model has no field for it, so even a leaking engine
        # cannot put the remaining questions in the payload.
        assert "plan" not in payload
        assert "questions" not in payload
        assert "rubric" not in str(payload)

    def test_full_room_flow(self, client, live, auth, slug, room):
        opened = _open_room(client, auth, slug)
        sid = opened["session_id"]

        begun = client.post(f"/api/interview/sessions/{sid}/begin", headers=auth)
        assert begun.status_code == 200
        assert begun.json()["state"] == "ai_intro"
        assert begun.json()["introduction"]

        first = client.post(f"/api/interview/sessions/{sid}/question", headers=auth)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["state"] == "waiting_for_response"
        assert body["current"]["prompt"]
        assert body["progress"]["position"] == 1

        answered = client.post(
            f"/api/interview/sessions/{sid}/answers",
            json={
                "text": "I owned the retrieval index end to end and measured p99 latency "
                "under a two hundred millisecond budget, trading recall for speed.",
                "duration_seconds": 58,
                "input_mode": "voice",
            },
            headers=auth,
        )
        assert answered.status_code == 200, answered.text
        payload = answered.json()
        # No score, anywhere in the response.
        assert "scores" not in str(payload)
        assert payload["progress"]["answered"] == 1

        report = client.post(f"/api/interview/sessions/{sid}/finish", headers=auth)
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["measurement"]["overall"] > 0
        assert body["role_analysis"]["headline"]

        # The report is stored, so a refresh re-reads rather than re-generating.
        again = client.get(f"/api/interview/sessions/{sid}/feedback", headers=auth)
        assert again.status_code == 200
        assert again.json()["measurement"]["overall"] == body["measurement"]["overall"]

        dropped = client.delete(f"/api/interview/sessions/{sid}", headers=auth)
        assert dropped.status_code == 200
        assert client.get(f"/api/interview/sessions/{sid}", headers=auth).status_code == 404

    def test_transcript_has_no_scores(self, client, live, auth, slug, room):
        sid = _open_room(client, auth, slug)["session_id"]
        client.post(f"/api/interview/sessions/{sid}/begin", headers=auth)
        client.post(f"/api/interview/sessions/{sid}/question", headers=auth)
        client.post(
            f"/api/interview/sessions/{sid}/answers",
            json={
                "text": "I built the index and the harness around it, with a 40 ms budget.",
                "duration_seconds": 31,
            },
            headers=auth,
        )
        resp = client.get(f"/api/interview/sessions/{sid}/transcript", headers=auth)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [e["speaker"] for e in body["entries"]][:2] == ["interviewer", "candidate"]
        assert "scores" not in str(body)
        assert "audio_recorded" not in body
        # The audio statement is what actually happens, not what we wish happened.
        assert "No audio was recorded" in body["audio_note"]

    def test_missing_session_is_404(self, client, live, auth):
        resp = client.get("/api/interview/sessions/deadbeef", headers=auth)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "interview_session_missing"

    def test_unknown_opportunity_is_404(self, client, live, auth):
        resp = client.post(
            "/api/interview/sessions",
            json={"opportunity": "no-such-opportunity-xyz"},
            headers=auth,
        )
        assert resp.status_code == 404

    def test_validation_rejects_empty_opportunity(self, client, live, auth):
        assert (
            client.post(
                "/api/interview/sessions", json={"opportunity": ""}, headers=auth
            ).status_code
            == 422
        )

    def test_validation_rejects_extra_fields(self, client, live, auth, slug):
        """A client cannot smuggle in an owner id or a score."""
        resp = client.post(
            "/api/interview/sessions",
            json={"opportunity": slug, "owner_id": "someone-else", "total_score": 100},
            headers=auth,
        )
        assert resp.status_code == 422

    def test_versioned_alias_exists(self, client, live, auth, slug, room):
        sid = _open_room(client, auth, slug)["session_id"]
        assert client.get("/api/v1/interview/status").status_code == 200
        assert client.get(f"/api/v1/interview/sessions/{sid}", headers=auth).status_code == 200

    def test_stream_ticket_is_single_use(self, client, live, auth, slug, room, monkeypatch):
        """
        A stream ticket is single-use and session-bound.

        `TestClient` blocks until a streaming response's generator finishes, so
        the stream budget is squeezed for this test. A room left in `ready` never
        reaches a terminal state on its own, and without this the test would sit
        on an open connection for the production 15-minute budget.
        """
        monkeypatch.setattr("api.routers.interview.STREAM_BUDGET_SECONDS", 0.75)
        monkeypatch.setattr("api.routers.interview.STREAM_POLL_SECONDS", 0.05)

        sid = _open_room(client, auth, slug)["session_id"]
        issued = client.post(f"/api/interview/sessions/{sid}/ticket", headers=auth)
        assert issued.status_code == 200, issued.text
        ticket = issued.json()["ticket"]
        assert 0 < issued.json()["expires_in"] <= 120

        frames: list[str] = []
        with client.stream(
            "GET", f"/api/interview/sessions/{sid}/events", params={"ticket": ticket}
        ) as stream:
            assert stream.status_code == 200
            assert "text/event-stream" in stream.headers["content-type"]
            assert "no-cache" in stream.headers["cache-control"]
            assert stream.headers["x-accel-buffering"] == "no"
            for line in stream.iter_lines():
                if line:
                    frames.append(line)

        assert "event: connection.established" in frames
        assert "event: interview.ready" in frames
        # The budget is reported rather than the connection closing silently.
        assert "stream_budget" in " ".join(frames)
        # The first frame carries the room, so the client renders before any
        # state change rather than waiting for one.
        assert "connection.established" in " ".join(frames)
        assert '"room"' in " ".join(frames)

        # Same ticket, second connection: refused. A replayable ticket would be a
        # session token with extra steps.
        second = client.get(f"/api/interview/sessions/{sid}/events", params={"ticket": ticket})
        assert second.status_code == 401
        assert second.json()["error"]["code"] == "stream_ticket_invalid"

    def test_a_ticket_for_one_session_opens_another(self, client, live, auth, slug, room):
        """Session binding: a ticket is scoped to the interview it was issued for."""
        monkey = pytest.MonkeyPatch()
        monkey.setattr("api.routers.interview.STREAM_BUDGET_SECONDS", 0.4)
        monkey.setattr("api.routers.interview.STREAM_POLL_SECONDS", 0.05)
        try:
            first = _open_room(client, auth, slug)["session_id"]
            second_id = _open_room(client, auth, slug)["session_id"]
            ticket = client.post(f"/api/interview/sessions/{first}/ticket", headers=auth).json()[
                "ticket"
            ]
            resp = client.get(
                f"/api/interview/sessions/{second_id}/events", params={"ticket": ticket}
            )
            assert resp.status_code == 401
        finally:
            monkey.undo()

    def test_the_stream_pushes_the_current_state_on_connect(
        self, client, live, auth, slug, room, monkeypatch
    ):
        """
        A client connecting mid-interview is told the state immediately.

        `TestClient` cannot issue a nested request while a streaming response is
        open on the same portal, so the room is driven forward first. That is the
        more valuable assertion anyway: a candidate who reloads during their
        introduction must see the introduction, not wait for the next transition.
        """
        monkeypatch.setattr("api.routers.interview.STREAM_BUDGET_SECONDS", 1.0)
        monkeypatch.setattr("api.routers.interview.STREAM_POLL_SECONDS", 0.05)

        sid = _open_room(client, auth, slug)["session_id"]
        client.post(f"/api/interview/sessions/{sid}/begin", headers=auth)
        client.post(f"/api/interview/sessions/{sid}/question", headers=auth)

        ticket = client.post(f"/api/interview/sessions/{sid}/ticket", headers=auth).json()["ticket"]

        events: list[str] = []
        with client.stream(
            "GET", f"/api/interview/sessions/{sid}/events", params={"ticket": ticket}
        ) as stream:
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())

        assert events[0] == "connection.established"
        # `waiting_for_response` maps to `question.presented`, so a reconnecting
        # candidate is handed the question that is already on screen.
        assert "question.presented" in events
        assert "interview.error" in events  # the budget frame closes it cleanly

    def test_a_completed_room_terminates_the_stream(
        self, client, live, auth, slug, room, monkeypatch
    ):
        """The stream closes itself once the interview is over, not on a timer."""
        monkeypatch.setattr("api.routers.interview.STREAM_BUDGET_SECONDS", 60.0)
        monkeypatch.setattr("api.routers.interview.STREAM_POLL_SECONDS", 0.05)

        sid = _open_room(client, auth, slug)["session_id"]
        client.post(f"/api/interview/sessions/{sid}/begin", headers=auth)
        client.post(f"/api/interview/sessions/{sid}/question", headers=auth)
        client.post(
            f"/api/interview/sessions/{sid}/answers",
            json={
                "text": "Sharded it across four replicas and held p99 under 40 ms.",
                "duration_seconds": 30,
            },
            headers=auth,
        )
        client.post(f"/api/interview/sessions/{sid}/finish", headers=auth)

        ticket = client.post(f"/api/interview/sessions/{sid}/ticket", headers=auth).json()["ticket"]
        events: list[str] = []
        with client.stream(
            "GET", f"/api/interview/sessions/{sid}/events", params={"ticket": ticket}
        ) as stream:
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())

        assert events[-1] == "interview.completed"
        # With a 60s budget it clearly did not run to the budget.
        assert "stream_budget" not in " ".join(events)

    def test_stream_without_a_ticket_is_unauthorised(self, client, live, auth, slug, room):
        sid = _open_room(client, auth, slug)["session_id"]
        assert client.get(f"/api/interview/sessions/{sid}/events").status_code == 422


def _first_job_slug(client: TestClient) -> str:
    resp = client.get("/api/jobs", params={"page_size": 1})
    if resp.status_code != 200:
        return ""
    items = resp.json().get("items") or []
    return items[0]["slug"] if items else ""
