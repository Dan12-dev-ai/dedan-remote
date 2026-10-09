"""
Interview Room client-contract tests.

These tests exist to prove that the *browser clients* (interview-room.html and
interview-results.html) are wired to the real /api/interview/* contract, and
that nothing the client depends on has drifted. They drive the exact sequence
the room client performs:

    GET  /status                 is a live interview even possible?
    POST /sessions               open a room for an opportunity
    POST /sessions/{id}/begin    the interviewer introduces itself
    POST /sessions/{id}/question advance to the next question
    POST /sessions/{id}/answers  submit an answer (no score comes back)
    GET  /sessions/{id}/ticket   one-use SSE ticket
    POST /sessions/{id}/finish   write the report
    GET  /sessions/{id}/feedback read the stored report
    GET  /sessions/{id}/transcript read the transcript

and assert the fields the clients actually read: `session_id`, `state`, `phase`,
`interviewer_state`, `role`, `progress`, `current.prompt`, `introduction`,
`privacy`, `outcome`. If a field the room client renders disappears from the
response model, one of these tests fails instead of the UI silently blanking.

The provider is a deterministic stub (the same seam the rest of the interview
suite uses) — this is a contract test, not a claim that a real model ran. See
tests/test_interview_api.py for the stub definition and the documented reason a
live model is an external dependency that is not available in CI.
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
from interview.followup import FollowUpEngine
from interview.room import InterviewRoom
from interview.room_store import get_room_store, reset_room_store
from interview.stream_ticket import reset_ticket_store


class ClientContractStub:
    """Deterministic model stand-in. Same contract as the production LLMClient."""

    @property
    def available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str:
        return ""

    def _require(self) -> tuple[str, str]:
        return "https://stub.invalid/v1/chat/completions", "stub-key"

    def complete_json(self, *, system: str, user: str, temperature=None, max_tokens=1400):
        if "Plan a" in user and "questions" in user:
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
        if "Write the debrief" in user or "debrief" in user.lower():
            return {
                "headline": "Strong on retrieval, thin on evaluation.",
                "role_fit": "The posting names evaluation tooling; answers covered retrieval only.",
                "evidence": ["we shipped the retrieval index in two weeks"],
                "what_they_proved": ["Owned a retrieval index end to end"],
                "what_they_did_not_show": ["No evidence of evaluation harness design"],
                "moves_the_rank": ["Describe an evaluation harness you built"],
                "interviewer_notes": ["Answered the deep question without prompting"],
                "cheat_sheet": ["Rehearse one evaluation story"],
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
def client():
    rate_limiter.reset()
    tmp = tempfile.mkdtemp(prefix="dedan_client_")
    store_module.reset_user_store_for_tests(os.path.join(tmp, "users.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def auth(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "client@example.com", "password": "longenough1", "display_name": "Client"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture()
def slug(client):
    item = client.get("/api/jobs?page_size=1").json()["items"][0]
    return item["slug"]


@pytest.fixture()
def live(client, monkeypatch):
    monkeypatch.setenv("INTERVIEW_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "stub-key")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    monkeypatch.setenv("INTERVIEW_MAX_QUESTIONS", "4")
    monkeypatch.setenv("INTERVIEW_DB_PATH", os.path.join(tempfile.mkdtemp(), "room.db"))
    reset_room_store(os.environ["INTERVIEW_DB_PATH"])
    reset_ticket_store()
    monkeypatch.setattr(
        "api.routers.interview.get_room",
        lambda: InterviewRoom(
            client=ClientContractStub(),
            store=get_room_store(),
            followups=FollowUpEngine(client=ClientContractStub()),
        ),
    )
    yield


# Fields the room client reads directly out of a room snapshot.
REQUIRED_ROOM_FIELDS = [
    "session_id",
    "state",
    "phase",
    "interviewer_state",
    "interviewer",
    "opportunity_id",
    "mode",
    "interview_type",
    "role",
    "interview",
    "progress",
    "current",
    "report_available",
    "privacy",
    "elapsed_seconds",
]


class TestRoomClientContract:
    """The browser room client's contract with /api/interview/*."""

    def test_status_shape_drives_the_boot_screen(self, client, live):
        body = client.get("/api/interview/status").json()
        assert body["live"] is True
        assert body["mode"] == "live"
        # The boot screen shows these three without inventing anything.
        assert "default_questions" in body
        assert isinstance(body["interview_types"], list) and body["interview_types"]
        assert body["privacy"]

    def test_open_returns_preparing_not_a_question(self, client, auth, slug, live):
        resp = client.post(
            "/api/interview/sessions",
            json={"opportunity": slug, "interview_type": "role_rehearsal"},
            headers=auth,
        )
        assert resp.status_code == 201, resp.text
        room = resp.json()
        for field in REQUIRED_ROOM_FIELDS:
            assert field in room, f"room snapshot missing client field: {field}"
        # The interviewer has not spoken yet; the plan is never leaked.
        assert room["current"] is None
        assert "questions" not in room
        assert room["session_id"]

    def test_full_lifecycle_matches_what_the_client_renders(self, client, auth, slug, live):
        # open
        room = client.post(
            "/api/interview/sessions",
            json={"opportunity": slug, "interview_type": "role_rehearsal"},
            headers=auth,
        ).json()
        sid = room["session_id"]

        # begin -> the introduction arrives
        room = client.post(f"/api/interview/sessions/{sid}/begin", headers=auth).json()
        assert room["state"] == "ai_intro"
        assert room["introduction"], "client speaks and shows the introduction"
        assert room["phase"] == "intro"

        # question -> the first question is on screen
        room = client.post(f"/api/interview/sessions/{sid}/question", headers=auth).json()
        assert room["current"] is not None
        assert room["current"]["prompt"]
        assert room["current"]["origin"] in ("planned", "follow_up")
        assert "sequence" in room["current"] and "position" in room["current"]

        # answers -> no score returned, the room advances
        room = client.post(
            f"/api/interview/sessions/{sid}/answers",
            json={"text": "I owned the retrieval index end to end and cut p99 latency.", "duration_seconds": 42, "input_mode": "voice"},
            headers=auth,
        ).json()
        assert "scores" not in room  # the client must never receive a per-answer score
        assert room["progress"]["answered"] >= 1

        # ticket -> the room client opens SSE with this
        ticket = client.post(f"/api/interview/sessions/{sid}/ticket", headers=auth).json()
        assert ticket["ticket"] and ticket["session_id"] == sid
        assert ticket["expires_in"] > 0

        # finish -> the report
        report = client.post(f"/api/interview/sessions/{sid}/finish", headers=auth).json()
        assert report  # a report came back

        # feedback -> the results client reads this
        fb = client.get(f"/api/interview/sessions/{sid}/feedback", headers=auth).json()
        assert fb
        assert "role" in fb  # results client renders role + summary

        # transcript -> the results client renders this
        tr = client.get(f"/api/interview/sessions/{sid}/transcript", headers=auth).json()
        assert "entries" in tr and isinstance(tr["entries"], list)
        assert any(e["speaker"] == "interviewer" for e in tr["entries"])
        assert any(e["speaker"] == "candidate" for e in tr["entries"])

    def test_snapshot_is_the_resume_path_after_reload(self, client, auth, slug, live):
        sid = client.post(
            "/api/interview/sessions", json={"opportunity": slug}, headers=auth
        ).json()["session_id"]
        # A reload fetches GET /sessions/{id} and renders exactly what the server holds.
        room = client.get(f"/api/interview/sessions/{sid}", headers=auth).json()
        assert room["session_id"] == sid
        for field in REQUIRED_ROOM_FIELDS:
            assert field in room

    def test_privacy_flags_are_real_not_invented(self, client, auth, slug, live):
        room = client.post(
            "/api/interview/sessions", json={"opportunity": slug}, headers=auth
        ).json()
        p = room["privacy"]
        # This deployment has no media store: audio is browser-transcribed.
        assert p["audio_recorded"] is False
        assert p["audio_transmitted"] is False
        assert isinstance(p["transcript_stored"], bool)
        assert isinstance(p["retention_days"], int)

    def test_ownership_blocks_another_candidate(self, client, auth, slug, live):
        sid = client.post(
            "/api/interview/sessions", json={"opportunity": slug}, headers=auth
        ).json()["session_id"]
        # A second account must not read or advance the first's session.
        other = client.post(
            "/api/auth/register",
            json={"email": "intruder@example.com", "password": "longenough1", "display_name": "X"},
        )
        other_auth = {"Authorization": "Bearer " + str(other.json()["token"])}
        assert client.get("/api/interview/sessions/" + sid, headers=other_auth).status_code == 404
        assert client.post("/api/interview/sessions/" + sid + "/begin", headers=other_auth).status_code == 404

    def test_anonymous_cannot_open_a_room(self, client, slug, live):
        # The room client sends a bearer token; without one the server refuses.
        resp = client.post("/api/interview/sessions", json={"opportunity": slug})
        assert resp.status_code == 401

    def test_unconfigured_status_is_reported_honestly(self, client, monkeypatch):
        # With no model configured, the boot screen must be told "unavailable".
        monkeypatch.setenv("INTERVIEW_ENABLED", "false")
        body = client.get("/api/interview/status").json()
        assert body["live"] is False
        assert body["mode"] == "unavailable"
        assert body["detail"]
