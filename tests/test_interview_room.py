"""
Interview Room — the parts of an interview that are not the model.

The HTTP contract lives in `test_interview_api.py`. This file covers the
behaviours that make a room a *room* rather than a question list, and that can be
tested without a running service:

  - the state machine refuses illegal moves and permits the ones retries need
  - adaptive follow-ups react to answer quality, within a bounded budget
  - ownership is enforced by the store, so a router that forgets cannot leak
  - progress survives a pause, a reload and a duplicate submission
  - the public projection contains no plan, no scores and no follow-up reason

The rule the whole suite defends: nothing about the assessment crosses the wire.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import pytest

from interview.engine import build_role_profile
from interview.followup import (
    FOLLOWUP_BUDGET_CAP,
    Depth,
    FollowUpAction,
    FollowUpEngine,
    decide_depth,
    followup_budget_note,
)
from interview.provider import ProviderError
from interview.room import (
    INTERVIEW_TYPES,
    InterviewRoom,
    RoomError,
    focus_areas,
    privacy_statement,
    resolve_type,
)
from interview.room_state import (
    TRANSITIONS,
    IllegalTransition,
    InterviewerState,
    Phase,
    RoomState,
    can_transition,
    is_live,
    is_terminal,
    phase_for,
    presence_for,
    require_transition,
)
from interview.room_store import RoomSessionStore
from interview.stream_ticket import StreamTicketStore

# ── fixtures ─────────────────────────────────────────────────────────────────

JOB: dict[str, Any] = {
    "slug": "retrieval-engineer-remote",
    "title": "Retrieval Engineer",
    "company": "Northwind",
    "location_label": "Worldwide",
    "salary": None,
    "salary_disclosed": False,
    "category": "ai-ml",
    "experience_hint": "advanced",
    "tags": ["rag", "python", "vector search"],
    "description": (
        "You will build retrieval systems. We are looking for someone with strong "
        "Python and experience with vector search. Responsibilities include owning "
        "the indexing pipeline and designing an evaluation harness."
    ),
    "intelligence": {"insights": ["Latency budget is the hard constraint here."]},
    "score_explanation": {"reasons": [{"label": "Remote", "detail": "Fully remote"}]},
}

SPECIFIC_ANSWER = (
    "I sharded the index across four replicas in Kafka, which keeps per-tenant "
    "ordering while letting us run 400 partitions horizontally. We measured 120k "
    "events per second with p99 latency under 40 ms, and the cost was giving up "
    "global ordering across tenants."
)
THIN_ANSWER = "I would use caching. It is the standard approach and it is fast."
OFF_TOPIC_ANSWER = "I like the ocean and I have never actually built a retrieval system."

STRONG_SCORES = {
    "role_depth": 84.0,
    "problem_solving": 80.0,
    "critical_thinking": 78.0,
    "communication": 76.0,
    "creativity": 62.0,
    "ownership": 81.0,
}
WEAK_SCORES = {key: 22.0 for key in STRONG_SCORES}


class StubLLM:
    """A model that can be told exactly what to return, call by call."""

    def __init__(
        self,
        *,
        plan: Optional[list[dict[str, Any]]] = None,
        follow_up: Optional[dict[str, Any]] = None,
        scores: Optional[dict[str, float]] = None,
        report: Optional[dict[str, Any]] = None,
        #: Marker matched against the *user* prompt, so a test can fail one stage
        #: of the pipeline without accidentally failing the planner too.
        fail_on: str = "",
    ) -> None:
        self.plan = plan if plan is not None else default_plan()
        self.follow_up = (
            follow_up
            if follow_up is not None
            else {
                "question": "What did that latency budget cost you?",
                "reason": "strong but unpriced trade-off",
                "anchor_skill": "retrieval",
            }
        )
        self.scores = scores if scores is not None else dict(STRONG_SCORES)
        self.report = report
        self.fail_on = fail_on
        self.available = True
        self.unavailable_reason = ""
        self.calls: list[dict[str, Any]] = []

    def _require(self) -> tuple[str, str]:
        return "https://stub.invalid/v1/chat/completions", "stub-key"

    def complete_json(self, *, system: str, user: str, temperature=None, max_tokens=1400):
        self.calls.append({"system": system, "user": user})
        if self.fail_on and self.fail_on in user:
            # The same exception the real provider raises. Raising anything else
            # would test the test's stub rather than the room's error handling.
            raise ProviderError(f"stub refused: {self.fail_on}")
        if "Plan a" in user:
            return {"questions": self.plan}
        if "opening a live interview" in system:
            return {"intro": "Hello. I'll be your interviewer for this role."}
        if "mid-conversation" in system:
            return dict(self.follow_up)
        if "debrief" in system:
            return self.report or {
                "headline": "Strong on retrieval, thin on evaluation.",
                "role_fit": "The posting names evaluation tooling; the answers never did.",
                "evidence": ["we shipped the retrieval index in two weeks"],
                "what_they_proved": ["Owned a retrieval index end to end"],
                "what_they_did_not_show": ["No evidence of evaluation harness design"],
                "moves_the_rank": ["Describe an evaluation harness you built"],
                "interviewer_notes": ["Answered the deep question unprompted"],
                "cheat_sheet": ["Rehearse one evaluation story"],
            }
        return {
            "summary": "Described owning the retrieval index under a latency budget.",
            "evidence": ["we shipped the retrieval index in two weeks"],
            "scores": dict(self.scores),
            "note": "Good depth; say more about how you measured it.",
            "follow_up": "",
            "probed": True,
        }


def default_plan() -> list[dict[str, Any]]:
    return [
        {
            "kind": "opening",
            "prompt": "Introduce yourself.",
            "intent": "motivation",
            "targets": ["rag"],
        },
        {
            "kind": "role_depth",
            "prompt": "Take me deep on the retrieval work.",
            "intent": "depth",
            "targets": ["retrieval"],
        },
        {
            "kind": "problem_solving",
            "prompt": "Retrieval fails intermittently. How do you find it?",
            "intent": "debug",
            "targets": ["vector search"],
        },
        {
            "kind": "scenario",
            "prompt": "Scope is larger than the posting. Week one?",
            "intent": "scope",
            "targets": ["python"],
        },
        {
            "kind": "closing",
            "prompt": "What would you need to ask before saying yes?",
            "intent": "questions",
            "targets": ["rag"],
        },
    ]


def make_room(
    tmp_path: str, *, llm: Optional[StubLLM] = None, **open_kwargs
) -> tuple[InterviewRoom, StubLLM]:
    store = RoomSessionStore(os.path.join(tmp_path, f"room-{id(open_kwargs)}.db"))
    llm = llm or StubLLM()
    room = InterviewRoom(client=llm, store=store, followups=FollowUpEngine(client=llm))
    return room, llm


@pytest.fixture()
def room(tmp_path):
    r, llm = make_room(tmp_path)
    r.llm = llm  # type: ignore[attr-defined]
    return r


def open_room(room: InterviewRoom, owner: str = "u1", **kwargs) -> dict[str, Any]:
    return room.open(owner_id=owner, job=JOB, opportunity_id="retrieval-engineer-remote", **kwargs)


def drive(room: InterviewRoom, sid: str, owner: str, answers: list[str], *, limit: int = 12):
    """Run the room forward, answering every question with `answers` in turn."""
    room.begin(sid, owner)
    asked = 0
    for index in range(limit):
        state = room.present_question(sid, owner)
        if state["current"] is None:
            return asked
        room.respond(
            sid,
            owner,
            text=answers[min(index, len(answers) - 1)],
            duration_seconds=42.0,
            input_mode="voice",
        )
        asked += 1
    return asked


# ── the state machine ────────────────────────────────────────────────────────


class TestRoomStateMachine:
    def test_every_state_has_a_transition_entry(self):
        for state in RoomState:
            assert state in TRANSITIONS, f"{state} has no declared edges"

    def test_terminal_states_have_no_way_out(self):
        assert is_terminal(RoomState.COMPLETED)
        assert is_terminal(RoomState.COMPLETED_FAILED)
        for source in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED):
            for target in RoomState:
                assert not can_transition(source, target) or source is target

    def test_a_finished_interview_cannot_be_reopened(self):
        """A late request must not resurrect a report the candidate already read."""
        for target in (RoomState.READY, RoomState.WAITING_FOR_RESPONSE, RoomState.AI_INTRO):
            assert not can_transition(RoomState.COMPLETED, target)
            with pytest.raises(IllegalTransition):
                require_transition(RoomState.COMPLETED, target)

    def test_error_is_recoverable_from_every_live_state(self):
        """Losing an interview to a dropped connection is the one real failure."""
        for state in RoomState:
            if is_terminal(state):
                continue
            assert can_transition(state, RoomState.ERROR), f"{state} cannot reach ERROR"
        for target in (RoomState.WAITING_FOR_RESPONSE, RoomState.AI_INTRO, RoomState.COMPLETING):
            assert can_transition(RoomState.ERROR, target)

    def test_pause_does_not_terminate(self):
        """Pause keeps the session alive, so it must reach a terminal state via resume."""
        assert can_transition(RoomState.PAUSED, RoomState.WAITING_FOR_RESPONSE)
        assert can_transition(RoomState.PAUSED, RoomState.COMPLETED)
        assert not can_transition(RoomState.PAUSED, RoomState.READY)

    def test_a_self_transition_is_a_no_op_not_an_error(self):
        """
        The room persists its state before answering, so every retry arrives while
        the room is already in the state the caller asked for. Refusing that would
        turn a network hiccup into something the candidate has to think about.
        """
        for state in RoomState:
            assert can_transition(state, state)
        assert (
            require_transition(RoomState.COMPLETING, RoomState.COMPLETING) is RoomState.COMPLETING
        )

    def test_every_state_has_a_presence_and_a_phase(self):
        for state in RoomState:
            assert isinstance(presence_for(state), InterviewerState)
            assert isinstance(phase_for(state), Phase)

    def test_a_question_on_screen_means_the_candidate_has_the_floor(self):
        assert presence_for(RoomState.WAITING_FOR_RESPONSE) is InterviewerState.LISTENING
        assert presence_for(RoomState.PROCESSING_RESPONSE) is InterviewerState.PROCESSING
        assert presence_for(RoomState.COMPLETED) is InterviewerState.COMPLETE

    def test_room_state_and_presence_are_separate_axes(self):
        """The room is answering while the presence is idle — not a contradiction."""
        assert is_live(RoomState.WAITING_FOR_RESPONSE)
        assert presence_for(RoomState.PAUSED) is InterviewerState.IDLE


# ── adaptive follow-ups ──────────────────────────────────────────────────────


class TestAdaptiveDepth:
    def test_a_short_answer_is_not_probeable(self):
        depth, reason = decide_depth(STRONG_SCORES, answer="yes", remaining_plan=3)
        assert depth is Depth.FOUNDATIONAL
        assert "not enough" in reason

    def test_a_strong_specific_answer_goes_advanced(self):
        depth, _ = decide_depth(STRONG_SCORES, answer=SPECIFIC_ANSWER, remaining_plan=3)
        assert depth is Depth.ADVANCED

    def test_a_strong_but_generic_answer_only_goes_one_level_deeper(self):
        """
        Eloquence is not depth. A high score on short, generic prose must not buy
        the hardest question in the interview.
        """
        depth, reason = decide_depth(STRONG_SCORES, answer=THIN_ANSWER * 3, remaining_plan=3)
        assert depth is Depth.DEEPER
        assert "generic" in reason

    def test_a_strong_answer_with_no_plan_left_stops_at_deeper(self):
        depth, reason = decide_depth(STRONG_SCORES, answer=SPECIFIC_ANSWER, remaining_plan=1)
        assert depth is Depth.DEEPER
        assert "little plan left" in reason

    def test_a_weak_answer_re_asks_narrower(self):
        depth, _ = decide_depth(WEAK_SCORES, answer=OFF_TOPIC_ANSWER * 2, remaining_plan=3)
        assert depth is Depth.FOUNDATIONAL

    def test_a_partial_answer_keeps_the_current_depth(self):
        depth, _ = decide_depth(
            {"role_depth": 50.0, "problem_solving": 48.0}, answer=SPECIFIC_ANSWER, remaining_plan=3
        )
        assert depth is Depth.STANDARD

    def test_depth_is_deterministic(self):
        """Adaptivity that changes on a re-run is not adaptivity, it is a coin toss."""
        args = (STRONG_SCORES,)
        kwargs = {"answer": SPECIFIC_ANSWER, "remaining_plan": 3}
        assert decide_depth(*args, **kwargs) == decide_depth(*args, **kwargs)

    def test_specificity_ignores_function_words(self):
        """Measuring against a bag of `the`/`and` measures English, not the person."""
        from interview.followup import _specificity

        padded = "the and for with you your that this from " * 12 + SPECIFIC_ANSWER
        assert _specificity(padded) == _specificity(SPECIFIC_ANSWER)

    def test_specificity_notices_named_systems_and_numbers(self):
        from interview.followup import _specificity

        vague = "I built a thing that was good and the team was happy with the result."
        assert _specificity(SPECIFIC_ANSWER) > _specificity(vague)

    def test_role_vocabulary_raises_specificity(self):
        from interview.followup import _specificity

        answer = "I spent most of the role on the retrieval and rag work, in python."
        assert _specificity(answer, role_terms=["retrieval", "rag", "python"]) > _specificity(
            answer
        )


class TestFollowUpBudget:
    def test_budget_is_a_bounded_fraction_of_the_plan(self):
        engine = FollowUpEngine(client=None)
        assert engine.budget_for(6) == 2  # int(6 * 0.4)
        assert engine.budget_for(3) == 1
        assert engine.budget_for(10) == FOLLOWUP_BUDGET_CAP
        assert engine.budget_for(0) == 0

    def test_the_budget_note_is_plain_language(self):
        assert "follow-up question" in followup_budget_note(6)

    def test_a_followup_is_asked_when_the_answer_is_strong(self, room):
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        result = room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        assert result["state"] == "ai_follow_up"
        assert result["progress"]["followups"] == 1

    def test_the_budget_is_never_exceeded(self, room):
        sid = open_room(room, count=5)["session_id"]
        drive(room, sid, "u1", [SPECIFIC_ANSWER])
        spent = room.snapshot(sid, "u1")["progress"]["followups"]
        assert spent <= FollowUpEngine(client=None).budget_for(5)

    def test_a_thin_answer_is_re_asked_narrower(self, room):
        """
        FOUNDATIONAL depth is not "give up", it is "ask the same thing again,
        narrower". A thin answer mid-interview earns a re-ask.
        """
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        result = room.respond(sid, "u1", text="Too short.")
        assert result["state"] == "ai_follow_up"

    def test_the_final_question_is_not_probed_when_there_is_nothing_to_probe(self):
        """
        With the plan spent there is nothing left to go deeper *on*, so a thin
        last answer closes the interview rather than being probed forever.
        """
        engine = FollowUpEngine(client=StubLLM())
        decision = engine.decide(
            profile=build_role_profile(JOB, "x"),
            question={"kind": "closing", "prompt": "Questions for us?", "intent": "questions"},
            answer="Too short.",
            scores=WEAK_SCORES,
            remaining_plan=0,
            plan_total=4,
            followups_spent=0,
            turn_index=4,
            turn_count=4,
            last_question=True,
        )
        assert decision.action is FollowUpAction.ADVANCE
        assert decision.probed is False

    def test_a_strong_final_answer_is_still_probed(self):
        """The rule is about having something to probe, not about position."""
        engine = FollowUpEngine(client=StubLLM())
        decision = engine.decide(
            profile=build_role_profile(JOB, "x"),
            question={"kind": "closing", "prompt": "Questions for us?", "intent": "questions"},
            answer=SPECIFIC_ANSWER,
            scores=STRONG_SCORES,
            remaining_plan=0,
            plan_total=4,
            followups_spent=0,
            turn_index=4,
            turn_count=4,
            last_question=True,
        )
        assert decision.action is FollowUpAction.ASK_FOLLOW_UP

    def test_the_budget_beats_a_strong_answer(self):
        """Adaptivity is bounded. Depth cannot run away into a 40-question session."""
        engine = FollowUpEngine(client=StubLLM())
        decision = engine.decide(
            profile=build_role_profile(JOB, "x"),
            question={"kind": "role_depth", "prompt": "Deep?", "intent": "depth"},
            answer=SPECIFIC_ANSWER,
            scores=STRONG_SCORES,
            remaining_plan=5,
            plan_total=5,
            followups_spent=engine.budget_for(5),
            turn_index=1,
            turn_count=5,
            last_question=False,
        )
        assert decision.action is FollowUpAction.ADVANCE
        assert "budget exhausted" in decision.reason
        assert decision.probed is False

    def test_adaptivity_survives_a_provider_failure(self, tmp_path):
        """
        If the model is down the decision must still be made, from scores we
        already have. Adaptivity that vanishes mid-interview is worse than none,
        because the candidate can hear it happen.
        """
        room, _ = make_room(tmp_path, llm=StubLLM())
        engine = FollowUpEngine(client=StubLLM(fail_on="THIS IS TURN"))
        room._followups = engine  # noqa: SLF001 - injecting the degraded path
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        result = room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        # No line, but the interview continued rather than erroring out.
        assert result["state"] == "next_question"
        assert engine.degraded is True

    def test_a_followup_reason_never_reaches_the_client(self, room):
        """
        The reasoning behind an adaptive follow-up is internal.

        Scoped to the question object: the room's own payload legitimately
        contains the word "advanced" as the posting's published seniority, which
        is the role's requirement rather than a read on the candidate.
        """
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        result = room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        question = room.present_question(sid, "u1")["current"]

        assert question["origin"] == "follow_up"
        blob = json.dumps(result) + json.dumps(question)
        assert "reason" not in blob
        assert "budget exhausted" not in blob
        assert "generic" not in blob  # the specificity reasoning
        for band in ("foundational", "advanced", "deeper", "standard"):
            assert band not in question["prompt"].lower()

    def test_the_decision_is_recorded_for_audit(self, room):
        """Server-side provenance is kept even though the candidate never sees it."""
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        turns = room._store.turns(sid, "u1")  # noqa: SLF001 - asserting on the record
        follow_ups = [t for t in turns if t["origin"] == "follow_up"]
        assert follow_ups
        decision = follow_ups[0]["follow_up"]
        assert decision["reason"]
        assert decision["depth"] in {d.value for d in Depth}


# ── ownership ────────────────────────────────────────────────────────────────


class TestOwnership:
    def test_a_session_belongs_to_exactly_one_account(self, tmp_path):
        store = RoomSessionStore(os.path.join(tmp_path, "own.db"))
        sid = store.create(
            owner_id="u1",
            opportunity_id="op",
            role_title="T",
            role_company="C",
            role={"title": "T"},
            plan=[{"id": "q1"}],
        )
        assert store.get(sid, "u1") is not None
        assert store.get(sid, "u2") is None

    def test_an_unowned_read_returns_nothing_rather_than_raising(self, tmp_path):
        """
        A store that raised would let a caller that forgot to check the return
        value render another candidate's transcript. Returning nothing makes the
        safe path the default one.
        """
        store = RoomSessionStore(os.path.join(tmp_path, "own.db"))
        sid = store.create(
            owner_id="u1",
            opportunity_id="op",
            role_title="T",
            role_company="C",
            role={"title": "T"},
            plan=[{"id": "q1"}],
        )
        store.add_turn(sid, "u1", {"sequence": 1, "prompt": "secret question"})
        assert store.turns(sid, "u2") == []

    def test_an_unowned_write_changes_nothing(self, tmp_path):
        store = RoomSessionStore(os.path.join(tmp_path, "own.db"))
        sid = store.create(
            owner_id="u1",
            opportunity_id="op",
            role_title="T",
            role_company="C",
            role={"title": "T"},
            plan=[{"id": "q1"}],
        )
        assert store.update(sid, "u2", state="completed") is False
        assert store.add_turn(sid, "u2", {"sequence": 1, "prompt": "injected"}) is None
        assert store.delete(sid, "u2") is False
        assert store.get(sid, "u1")["state"] == "ready"
        assert store.turns(sid, "u1") == []

    def test_the_room_refuses_an_unowned_session(self, room):
        sid = open_room(room)["session_id"]
        with pytest.raises(RoomError) as caught:
            room.snapshot(sid, "someone-else")
        # 404, not 403: a 403 confirms the id exists.
        assert caught.value.status == 404
        assert "not yours" in caught.value.message

    def test_a_room_requires_an_owner(self, room):
        with pytest.raises(RoomError) as caught:
            room.open(
                owner_id="",
                job=JOB,
                opportunity_id="x",
            )
        assert caught.value.status == 401

    def test_history_and_recovery_are_scoped_to_the_account(self, room):
        open_room(room, owner="u1")
        assert len(room.recoverable("u1")) == 1
        assert room.recoverable("u2") == []
        assert [row["opportunity_id"] for row in room.history("u2")] == []


# ── durability and lifecycle ─────────────────────────────────────────────────


class TestDurability:
    def test_progress_survives_a_reload(self, tmp_path):
        """
        The strongest guarantee about an interview in progress: the work is on
        the server, not in the tab. A brand-new room object over the same
        database must pick up exactly where the first one stopped.
        """
        db = os.path.join(tmp_path, "reload.db")
        first = InterviewRoom(
            client=StubLLM(), store=RoomSessionStore(db), followups=FollowUpEngine(client=StubLLM())
        )
        sid = open_room(first, count=4)["session_id"]
        first.begin(sid, "u1")
        first.present_question(sid, "u1")
        first.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)

        # Same database, brand-new objects: the reload.
        second = InterviewRoom(
            client=StubLLM(), store=RoomSessionStore(db), followups=FollowUpEngine(client=StubLLM())
        )
        resumed = second.snapshot(sid, "u1")
        assert resumed["progress"]["answered"] == 1
        assert resumed["current"]["prompt"]

    def test_pause_keeps_the_question_on_screen(self, room):
        sid = open_room(room)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        paused = room.pause(sid, "u1")
        assert paused["state"] == "paused"
        assert paused["current"]["prompt"]
        resumed = room.resume(sid, "u1")
        assert resumed["state"] == "waiting_for_response"
        assert resumed["current"]["prompt"] == paused["current"]["prompt"]

    def test_pause_is_idempotent(self, room):
        """A double-click on a slow connection must not present an error."""
        sid = open_room(room)["session_id"]
        room.begin(sid, "u1")
        assert room.pause(sid, "u1")["state"] == "paused"
        assert room.pause(sid, "u1")["state"] == "paused"

    def test_an_answer_while_paused_is_refused(self, room):
        sid = open_room(room)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.pause(sid, "u1")
        with pytest.raises(RoomError) as caught:
            room.respond(sid, "u1", text=SPECIFIC_ANSWER)
        assert caught.value.code == "interview_paused"

    def test_a_duplicate_submission_does_not_count_twice(self, tmp_path):
        """
        A retry after a timeout must not produce two turns from one answer.

        Uses a thin answer so the room advances rather than opening a follow-up —
        otherwise the follow-up genuinely *is* the pending question and answering
        it a second time is not a duplicate, it is the next turn.
        """
        # No follow-up engine: this test is about duplicate submissions, and a
        # follow-up would legitimately be the next pending question.
        room, _ = make_room(tmp_path, llm=StubLLM())
        room._followups = FollowUpEngine(client=None)  # noqa: SLF001
        sid = open_room(room, count=4)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=4)

        with pytest.raises(RoomError) as caught:
            room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=4)
        assert caught.value.code == "no_question_pending"
        assert room.snapshot(sid, "u1")["progress"]["exchanged"] == 1
        assert room.snapshot(sid, "u1")["progress"]["answered"] == 1

    def test_a_followup_is_the_next_question_not_a_duplicate(self, tmp_path):
        """
        After a follow-up is generated the pending question *is* the follow-up.
        Answering it without re-presenting is the reconnect path and must work.
        """
        room, _ = make_room(tmp_path, llm=StubLLM())
        sid = open_room(room, count=4)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        assert (
            room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)["state"]
            == "ai_follow_up"
        )
        answered = room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=48)
        assert answered["state"] in ("next_question", "ai_follow_up")
        assert answered["progress"]["exchanged"] == 2

    def test_re_presenting_a_question_is_idempotent(self, room):
        sid = open_room(room, count=4)["session_id"]
        room.begin(sid, "u1")
        first = room.present_question(sid, "u1")
        again = room.present_question(sid, "u1")
        assert again["current"]["id"] == first["current"]["id"]
        assert again["progress"]["position"] == 1

    def test_skipping_is_recorded_not_dropped(self, room):
        """Coverage in the report must reflect what was actually asked."""
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        result = room.skip(sid, "u1")
        assert result["progress"]["exchanged"] == 1
        assert result["progress"]["answered"] == 0
        transcript = room.transcript(sid, "u1")
        assert transcript["turns_asked"] == 1
        assert transcript["turns_answered"] == 0

    def test_a_skipped_question_does_not_wedged_the_room(self, room):
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.skip(sid, "u1")
        assert room.present_question(sid, "u1")["current"]["prompt"]

    def test_discard_removes_the_transcript(self, room, tmp_path):
        sid = open_room(room)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.discard(sid, "u1")
        assert room._store.turns(sid, "u1") == []  # noqa: SLF001
        with pytest.raises(RoomError):
            room.snapshot(sid, "u1")

    def test_retention_purge_deletes_old_sessions(self, tmp_path):
        store = RoomSessionStore(os.path.join(tmp_path, "purge.db"))
        sid = store.create(
            owner_id="u1",
            opportunity_id="op",
            role_title="T",
            role_company="C",
            role={"title": "T"},
            plan=[{"id": "q1"}],
        )
        assert store.purge_expired(retention_days=30) == 0
        assert store.get(sid, "u1") is not None
        # Backdate past the window.
        store._execute(  # noqa: SLF001 - the only way to age a row in a test
            "UPDATE interview_room_sessions SET created_at = '2000-01-01T00:00:00+00:00'"
        )
        assert store.purge_expired(retention_days=30) == 1
        assert store.get(sid, "u1") is None

    def test_update_rejects_unknown_fields(self, tmp_path):
        """A typo must not look like a successful save."""
        store = RoomSessionStore(os.path.join(tmp_path, "fields.db"))
        sid = store.create(
            owner_id="u1",
            opportunity_id="op",
            role_title="T",
            role_company="C",
            role={"title": "T"},
            plan=[{"id": "q1"}],
        )
        with pytest.raises(ValueError):
            store.update(sid, "u1", total_score=100)


# ── the public projection ────────────────────────────────────────────────────


class TestPublicProjection:
    def test_the_plan_is_absent(self, room):
        payload = open_room(room, count=5)
        assert "plan" not in payload
        assert "questions" not in payload
        assert payload["current"] is None

    def test_no_score_is_published_mid_interview(self, room):
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        result = room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        body = json.dumps(result)
        assert "scores" not in body
        # The word "evaluation" appears in this listing's own description, so the
        # check is for the key, not the substring.
        assert '"evaluation"' not in body
        assert '"intent"' not in body
        assert '"scores"' not in body
        assert '"dimension"' not in body

    def test_the_question_carries_no_rubric(self, room):
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        question = room.present_question(sid, "u1")["current"]
        assert set(question) == {
            "id",
            "sequence",
            "position",
            "kind",
            "category",
            "origin",
            "prompt",
            "focus",
            "suggested_seconds",
        }

    def test_the_interviewers_intent_is_never_published(self, room):
        """The intent is the interviewer's private note about what it is probing."""
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        question = room.present_question(sid, "u1")["current"]
        assert "intent" not in json.dumps(question)

    def test_a_followup_is_labelled_as_one(self, room):
        """Adaptivity is visible; the reasoning behind it is not."""
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        question = room.present_question(sid, "u1")["current"]
        assert question["origin"] == "follow_up"
        assert question["category"] == "follow-up"

    def test_progress_counts_exchanges_not_hidden_questions(self, room):
        sid = open_room(room, count=5)["session_id"]
        room.begin(sid, "u1")
        assert room.snapshot(sid, "u1")["progress"]["position"] == 0
        room.present_question(sid, "u1")
        assert room.snapshot(sid, "u1")["progress"]["position"] == 1
        # The floor of what is left, never a promise adaptivity could break.
        assert "remaining_minimum" in room.snapshot(sid, "u1")["progress"]

    def test_the_transcript_has_no_scores(self, room):
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        transcript = room.transcript(sid, "u1")
        assert "scores" not in json.dumps(transcript)
        assert transcript["audio_note"]

    def test_privacy_states_only_what_happens(self, room):
        statement = privacy_statement()
        # There is no media store on this deployment, so claiming otherwise
        # would be the worst possible lie in an interview product.
        assert statement["audio_recorded"] is False
        assert statement["audio_transmitted"] is False
        assert statement["transcript_stored"] is True
        assert statement["shared_with_employer"] is False
        assert statement["retention_days"] > 0
        assert statement["third_party_model"]

    def test_focus_areas_come_from_the_listing_only(self, room):
        """A focus area the posting never named is an invented requirement."""
        areas = [a.label for a in focus_areas(build_role_profile(JOB, "x"))]
        assert areas
        assert any("rag" in a.lower() or "python" in a.lower() for a in areas)

    def test_a_thin_listing_does_not_invent_focus(self):
        thin = {
            "slug": "t",
            "title": "Barista",
            "company": "C",
            "location_label": "W",
            "salary": None,
            "salary_disclosed": False,
            "category": "other",
            "experience_hint": None,
            "tags": [],
            "description": "Serve coffee.",
        }
        assert focus_areas(build_role_profile(thin, "t")) == []


# ── interview types ──────────────────────────────────────────────────────────


class TestInterviewTypes:
    def test_every_type_has_a_distinct_label(self):
        labels = [spec.label for spec in INTERVIEW_TYPES.values()]
        assert len(labels) == len(set(labels))

    def test_an_unknown_type_falls_back_rather_than_erroring(self):
        assert resolve_type("nonsense").key == "role_rehearsal"
        assert resolve_type(None).key == "role_rehearsal"
        assert resolve_type("  TECHNICAL ").key == "technical"

    def test_the_type_reorders_the_plan(self, tmp_path):
        """
        The lens changes the shape of the conversation without re-writing the
        questions: a behavioural-first interview reaches its scenario and
        background work before a system-design-first one does.

        Relative order, not absolute — the plan's own contents decide which kinds
        are present at all, and the test must not depend on a stub's question set.
        """
        orders: dict[str, list[str]] = {}
        for kind in ("technical", "behavioral"):
            room, _ = make_room(tmp_path, llm=StubLLM())
            payload = room.open(
                owner_id="u1", job=JOB, opportunity_id="x", count=5, interview_type=kind
            )
            record = room._store.get(payload["session_id"], "u1")  # noqa: SLF001
            orders[kind] = [row["kind"] for row in record["plan"]]

        technical = orders["technical"]
        behavioural = orders["behavioral"]
        # Technical leads with depth; behavioural leads with situational work.
        assert technical.index("role_depth") < technical.index("scenario")
        assert behavioural.index("scenario") < behavioural.index("role_depth")
        # Reordering must not change the membership of the plan.
        assert sorted(technical) == sorted(behavioural)

    def test_the_plan_keeps_its_length_across_types(self, tmp_path):
        for kind in INTERVIEW_TYPES:
            room, _ = make_room(tmp_path, llm=StubLLM())
            payload = room.open(
                owner_id="u1", job=JOB, opportunity_id="x", count=4, interview_type=kind
            )
            assert payload["progress"]["planned_total"] == 4

    def test_the_type_is_reported_back(self, tmp_path):
        room, _ = make_room(tmp_path, llm=StubLLM())
        payload = room.open(
            owner_id="u1", job=JOB, opportunity_id="x", interview_type="system_design"
        )
        assert payload["interview_type"] == "system_design"
        assert payload["interview"]["type_label"] == "System design"


# ── completion ───────────────────────────────────────────────────────────────


class TestCompletion:
    def test_finish_writes_a_report(self, room):
        sid = open_room(room, count=3)["session_id"]
        drive(room, sid, "u1", [SPECIFIC_ANSWER])
        report = room.finish(sid, "u1")
        assert report["measurement"]["overall"] > 0
        assert report["role_analysis"]["headline"]

    def test_the_report_is_stored_so_a_refresh_re_reads_it(self, room):
        """Regenerating a debrief on every reload produces a different verdict."""
        sid = open_room(room, count=3)["session_id"]
        drive(room, sid, "u1", [SPECIFIC_ANSWER])
        first = room.finish(sid, "u1")
        assert room.finish(sid, "u1")["measurement"]["overall"] == first["measurement"]["overall"]
        assert room.feedback(sid, "u1")["measurement"]["overall"] == first["measurement"]["overall"]

    def test_feedback_before_the_end_is_refused(self, room):
        sid = open_room(room, count=3)["session_id"]
        with pytest.raises(RoomError) as caught:
            room.feedback(sid, "u1")
        assert caught.value.code == "interview_not_finished"

    def test_an_unscored_report_says_so_and_invents_no_numbers(self, tmp_path):
        """
        The failure mode to avoid is a report that *looks* scored. Every numeric
        field must be null and the summary must state what happened.
        """
        dead = StubLLM()
        dead.available = False  # no provider configured
        dead.unavailable_reason = "Live AI interviews are switched off on this deployment."
        room, _ = make_room(tmp_path, llm=dead)
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        report = room.finish(sid, "u1")

        assert report["scored"] is False
        assert report["measurement"]["overall"] is None
        assert all(d["score"] is None for d in report["dimensions"])
        assert any(
            "not" in note.lower() and "scored" in note.lower() for note in report["limitations"]
        )
        # The transcript is still there — that is the part worth reviewing.
        assert report["turns"]
        # And the outcome is distinguishable from a finished, scored interview.
        assert room.snapshot(sid, "u1")["outcome"] == "completed_failed"
        assert room.snapshot(sid, "u1")["state"] == "completed_failed"

    def test_an_answer_that_is_too_short_is_recorded_unevaluated(self, room):
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.respond(sid, "u1", text="yes")
        # The room is mid-conversation; `drive` continues it rather than
        # restarting, so `begin` is not called a second time.
        for _ in range(4):
            state = room.present_question(sid, "u1")
            if state["current"] is None:
                break
            room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        report = room.finish(sid, "u1")
        assert report["measurement"]["turns_unevaluated"] >= 1
        assert any("could not be scored" in n for n in report["limitations"])

    def test_an_evaluator_failure_never_ends_the_interview(self, tmp_path):
        """A free tier shedding a worker must not cost the candidate the interview."""
        llm = StubLLM(fail_on="Score every dimension")
        room, _ = make_room(tmp_path, llm=llm)
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        result = room.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)
        assert result["state"] in ("next_question", "ai_follow_up")
        assert room.present_question(sid, "u1")["current"]["prompt"]

    def test_answers_after_completion_are_refused(self, room):
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.finish(sid, "u1")
        with pytest.raises(RoomError) as caught:
            room.respond(sid, "u1", text=SPECIFIC_ANSWER)
        assert caught.value.status == 409

    def test_the_room_runs_out_of_questions_and_says_so(self, room):
        sid = open_room(room, count=3)["session_id"]
        drive(room, sid, "u1", [SPECIFIC_ANSWER])
        exhausted = room.present_question(sid, "u1")
        assert exhausted["current"] is None
        assert exhausted["state"] == "completing"


# ── recovery ─────────────────────────────────────────────────────────────────


class TestRecovery:
    def test_a_crashed_session_is_offered_back(self, tmp_path):
        db = os.path.join(tmp_path, "recover.db")
        llm = StubLLM()
        first = InterviewRoom(
            client=llm, store=RoomSessionStore(db), followups=FollowUpEngine(client=llm)
        )
        sid = open_room(first, count=4)["session_id"]
        first.begin(sid, "u1")
        first.present_question(sid, "u1")
        first.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)

        second = InterviewRoom(
            client=llm, store=RoomSessionStore(db), followups=FollowUpEngine(client=llm)
        )
        rows = second.recoverable("u1")
        assert len(rows) == 1
        assert rows[0]["session_id"] == sid
        assert rows[0]["turns_answered"] == 1
        # The candidate is told where they will pick up.
        assert rows[0]["resumes_at"]

    def test_a_finished_session_is_not_offered_as_recoverable(self, room):
        sid = open_room(room, count=3)["session_id"]
        drive(room, sid, "u1", [SPECIFIC_ANSWER])
        room.finish(sid, "u1")
        assert room.recoverable("u1") == []

    def test_recovery_survives_a_provider_outage(self, tmp_path):
        """
        The transcript is the candidate's work; it must not depend on a model
        being reachable to be readable.
        """
        db = os.path.join(tmp_path, "outage.db")
        llm = StubLLM()
        first = InterviewRoom(
            client=llm, store=RoomSessionStore(db), followups=FollowUpEngine(client=llm)
        )
        sid = open_room(first, count=3)["session_id"]
        first.begin(sid, "u1")
        first.present_question(sid, "u1")
        first.respond(sid, "u1", text=SPECIFIC_ANSWER, duration_seconds=61)

        dead = StubLLM()
        dead.available = False
        second = InterviewRoom(
            client=dead, store=RoomSessionStore(db), followups=FollowUpEngine(client=dead)
        )
        transcript = second.transcript(sid, "u1")
        assert transcript["entries"]
        assert second.recoverable("u1")

    def test_an_error_can_be_resumed(self, room):
        sid = open_room(room, count=3)["session_id"]
        room.begin(sid, "u1")
        room.present_question(sid, "u1")
        room.mark_error(sid, "u1", "The evaluator timed out.")
        assert room.snapshot(sid, "u1")["state"] == "error"
        resumed = room.resume(sid, "u1")
        assert resumed["state"] == "waiting_for_response"
        assert resumed["error"] is None


# ── stream tickets ───────────────────────────────────────────────────────────


class TestStreamTickets:
    def test_a_ticket_is_single_use(self):
        store = StreamTicketStore()
        ticket = store.issue(owner_id="u1", session_id="s1")
        assert store.redeem(ticket.value, session_id="s1") == "u1"
        assert store.redeem(ticket.value, session_id="s1") is None

    def test_a_ticket_is_bound_to_one_session(self):
        store = StreamTicketStore()
        ticket = store.issue(owner_id="u1", session_id="s1")
        assert store.redeem(ticket.value, session_id="s2") is None

    def test_an_expired_ticket_is_refused(self):
        store = StreamTicketStore(ttl_seconds=-1)
        ticket = store.issue(owner_id="u1", session_id="s1")
        assert store.redeem(ticket.value, session_id="s1") is None

    def test_an_unknown_ticket_is_refused(self):
        assert StreamTicketStore().redeem("nope", session_id="s1") is None

    def test_the_store_is_bounded(self):
        store = StreamTicketStore()
        for index in range(600):
            store.issue(owner_id="u1", session_id=f"s{index}")
        assert store.count() <= 512

    def test_ticket_values_are_unpredictable(self):
        store = StreamTicketStore()
        values = {store.issue(owner_id="u1", session_id="s").value for _ in range(200)}
        assert len(values) == 200
