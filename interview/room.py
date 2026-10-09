"""
Interview Room — the orchestrator for a conversational AI interview.

This is the seam between the API and the interview engine. The engine knows how
to plan, score and write a report; the room knows about *candidates*: whose
session this is, where they are in the conversation, whether they paused, and
what they are allowed to be told right now.

    API  ──►  InterviewRoom  ──►  InterviewEngine   (plan / evaluate / report)
                    │            FollowUpEngine     (adaptive follow-ups)
                    └──────────► RoomSessionStore   (ownership + recovery)
                    └──────────► room_state          (one authoritative state)

Two invariants the code exists to hold:

1. **The plan, the intent and the follow-up reason never leave the server.**
   ``to_public`` is the only projection that leaves this module, and it contains
   a question, never a score. Evaluation happens on submit; the report is the
   first and only time a number reaches the candidate.

2. **Progress is durable before it is acknowledged.** Every state change writes
   to SQLite before the API returns. A candidate who closes the tab mid-answer
   comes back to the same turn, not to question one.

The room is deliberately *not* a second evaluation engine. Scoring is
`interview.engine.evaluate_turn`, reporting is `interview.engine.build_report`,
and the plan is `interview.engine.plan_questions`. The room's own contribution is
adaptivity (via `interview.followup`), lifecycle and access control.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from interview import engine as engine_module
from interview import rubric
from interview.engine import InterviewEngine, InterviewSession, RoleProfile
from interview.followup import FollowUpAction, FollowUpEngine
from interview.provider import LLMClient, ProviderError, ProviderUnavailable, get_llm
from interview.room_state import (
    InterviewerState,
    Phase,
    RoomState,
    can_transition,
    phase_for,
    presence_for,
    require_transition,
)
from interview.room_store import RoomSessionStore, get_room_store
from interview.skills import skill_label

logger = logging.getLogger("dedan.interview.room")
llm_logger = logging.getLogger("dedan.interview.llm")


class RoomError(RuntimeError):
    """A room operation could not be completed. Carries a stable ``code``."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ── interview types ──────────────────────────────────────────────────────────
#
# The type is a *lens*, not a different engine: it reorders the kind ladder and
# changes what the preparation screen promises. Every type still runs the same
# orchestrator, evaluator and report.


@dataclass(frozen=True)
class InterviewType:
    key: str
    label: str
    #: Ordered kind priorities. The planner honours this before its own ladder.
    emphasis: tuple[str, ...]
    #: Behavioural coverage is guaranteed for these types even when the plan is
    #: otherwise technical.
    behavioural: bool = False


INTERVIEW_TYPES: dict[str, InterviewType] = {
    "role_rehearsal": InterviewType(
        key="role_rehearsal",
        label="Role rehearsal",
        emphasis=("opening", "role_depth", "problem_solving", "scenario", "background", "closing"),
        behavioural=True,
    ),
    "technical": InterviewType(
        key="technical",
        label="Technical",
        emphasis=("role_depth", "problem_solving", "background", "scenario", "closing"),
    ),
    "behavioral": InterviewType(
        key="behavioral",
        label="Behavioural",
        emphasis=("background", "scenario", "opening", "role_depth", "closing"),
        behavioural=True,
    ),
    "system_design": InterviewType(
        key="system_design",
        label="System design",
        emphasis=("role_depth", "problem_solving", "scenario", "background", "closing"),
    ),
    "ai_engineering": InterviewType(
        key="ai_engineering",
        label="AI engineering",
        emphasis=("role_depth", "problem_solving", "scenario", "background", "closing"),
    ),
    "coding": InterviewType(
        key="coding",
        label="Coding & debugging",
        emphasis=("problem_solving", "role_depth", "scenario", "closing"),
    ),
    "scenario": InterviewType(
        key="scenario",
        label="Scenario",
        emphasis=("scenario", "problem_solving", "role_depth", "closing"),
    ),
    "general_rehearsal": InterviewType(
        key="general_rehearsal",
        label="General rehearsal",
        emphasis=("opening", "background", "role_depth", "problem_solving", "scenario", "closing"),
        behavioural=True,
    ),
}

#: The type requested when a caller does not say.
DEFAULT_TYPE = "role_rehearsal"


def resolve_type(key: Optional[str]) -> InterviewType:
    return INTERVIEW_TYPES.get((key or "").strip().lower(), INTERVIEW_TYPES[DEFAULT_TYPE])


# ── what the candidate is told ───────────────────────────────────────────────


@dataclass(frozen=True)
class FocusArea:
    """One line in the interview context rail. Orientation, not evaluation."""

    label: str
    #: 'skill' | 'requirement' | 'signal'
    source: str


def focus_areas(profile: RoleProfile) -> list[FocusArea]:
    """
    The competency list the candidate is oriented with.

    Built from the listing only. A focus area the posting never mentioned would
    be an invented requirement, which is the single fastest way to lose a
    candidate's trust in the whole product.
    """
    areas: list[FocusArea] = []
    seen: set[str] = set()

    def add(label: str, source: str) -> None:
        key = label.strip().lower()
        if len(key) < 3 or key in seen:
            return
        seen.add(key)
        areas.append(FocusArea(label=label.strip(), source=source))

    for name in profile.tags[:5]:
        add(skill_label(name), "skill")
    for requirement in profile.must_haves[:4]:
        add(requirement, "requirement")
    for signal in profile.signals[:2]:
        add(signal, "signal")
    return areas[:8]


def question_metadata(kind: str, intent: str, targets: list[str]) -> list[str]:
    """
    The compact metadata line under a question: category plus focus labels.

    Derived from the posting, never from the evaluation. `intent` is excluded on
    purpose — it is the interviewer's private note about what the question is
    probing, and publishing it would tell the candidate how to score.
    """
    labels = [skill_label(t) for t in targets if t]
    if intent:
        # Only the category word survives, not the note.
        head = intent.strip().split()[0].lower() if intent.strip() else ""
        if head and head.isalpha() and head not in labels:
            labels.insert(0, head.capitalize())
    return labels[:3] or [kind.replace("_", " ")]


# ── the room ─────────────────────────────────────────────────────────────────


#: States in which the interviewer is talking and no question has been asked yet.
#: The client renders the introduction for all of them rather than re-deciding the
#: condition itself, so there is one definition of "nothing to answer yet".
_INTRO_STATES: frozenset[RoomState] = frozenset(
    {RoomState.READY, RoomState.AI_INTRO, RoomState.AI_ASKING}
)


class InterviewRoom:
    """
    Lifecycle owner for one interview session.

    Constructed per request with a process-wide store, mirroring
    ``InterviewEngine``. The client and the follow-up engine are injectable so
    the whole lifecycle is testable without a provider.
    """

    def __init__(
        self,
        *,
        client: Optional[LLMClient] = None,
        store: Optional[RoomSessionStore] = None,
        followups: Optional[FollowUpEngine] = None,
    ) -> None:
        self._store = store or get_room_store()
        self._client = client
        self._followups = followups
        self._engine = InterviewEngine(client=client)

    @property
    def client(self) -> LLMClient:
        return self._client or self._engine.client

    @property
    def followups(self) -> FollowUpEngine:
        if self._followups is None:
            self._followups = FollowUpEngine(client=self.client)
        return self._followups

    # ── capability ──────────────────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        return self._engine.available()

    # ── lookup + ownership ──────────────────────────────────────────────────

    def _owned(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        Load a session the caller owns.

        Raises ``RoomError(404)`` for both "does not exist" and "belongs to
        someone else". The two are deliberately indistinguishable: a distinct
        status for the second one confirms to an attacker that the id is real.
        """
        if not owner_id:
            raise RoomError("auth_required", "Sign in to use this interview.", status=401)
        record = self._store.get(session_id, owner_id)
        if record is None:
            raise RoomError(
                "interview_session_missing",
                "That interview session was not found, or it is not yours.",
                status=404,
            )
        return record

    @staticmethod
    def _profile(record: dict[str, Any]) -> RoleProfile:
        """
        Rebuild the role brief from the stored row.

        ``to_public`` deliberately omits the slug (it is the opportunity id, and
        the caller already holds it), so it is restored from the session on read.
        Every other field round-trips exactly.
        """
        stored = dict(record.get("role") or {})
        stored.setdefault("slug", record.get("opportunity_id") or "")
        fields = set(RoleProfile.__dataclass_fields__)
        return RoleProfile(**{k: v for k, v in stored.items() if k in fields})

    @staticmethod
    def _state(record: dict[str, Any]) -> RoomState:
        try:
            return RoomState(record.get("state") or RoomState.PREPARING.value)
        except ValueError:
            # A row written by a newer build must not crash an older one.
            return RoomState.ERROR

    def _transition(self, record: dict[str, Any], target: RoomState) -> dict[str, Any]:
        """
        Move the room and persist before returning.

        Persistence is not an optimisation here — it is what makes a reload
        recoverable. A state change the API acknowledged but did not write is a
        state the candidate cannot get back to.
        """
        source = self._state(record)
        require_transition(source, target)
        if not self._store.update(record["id"], record["owner_id"], state=target.value):
            raise RoomError(
                "interview_session_lost",
                "That interview session is no longer available.",
                status=404,
            )
        record["state"] = target.value
        return record

    # ── open ────────────────────────────────────────────────────────────────

    def open(
        self,
        *,
        owner_id: str,
        job: dict[str, Any],
        opportunity_id: str,
        count: Optional[int] = None,
        interview_type: Optional[str] = None,
        mode: str = "live",
    ) -> dict[str, Any]:
        """
        Compose a plan, persist the session, and return the preparation state.

        The plan is written here and never returned. The response carries the
        focus areas, the interview type and the estimated length — enough to
        orient, nothing that lets a candidate pre-study the questions.
        """
        if not owner_id:
            raise RoomError("auth_required", "Sign in to run an AI interview.", status=401)

        spec = resolve_type(interview_type)
        profile = engine_module.build_role_profile(job, opportunity_id)
        total = max(3, min(int(count or _default_count()), 12))
        plan = self._plan(profile, total, spec)

        intro = self._compose_intro(profile, spec, total)

        session_id = self._store.create(
            owner_id=owner_id,
            opportunity_id=opportunity_id,
            role_title=profile.title,
            role_company=profile.company,
            role=profile.to_dict(),
            plan=plan,
            mode=mode,
            interview_type=spec.key,
            intro_text=intro,
        )
        record = self._store.get(session_id, owner_id)
        if record is None:  # pragma: no cover - the row was just written
            raise RoomError(
                "interview_not_persisted",
                "The interview could not be saved, so it was not started.",
                status=503,
            )
        return self.to_public(record)

    def _plan(self, profile: RoleProfile, count: int, spec: InterviewType) -> list[dict[str, Any]]:
        """Compose the plan, then enforce the type's emphasis on the result."""
        plan = engine_module.plan_questions(profile, count, client=self.client)
        return self._apply_emphasis(plan, spec)

    @staticmethod
    def _apply_emphasis(plan: list[dict[str, Any]], spec: InterviewType) -> list[dict[str, Any]]:
        """
        Re-order a composed plan to the requested interview type.

        Sorting an already-written plan rather than asking the model again: the
        question text stays the model's, and the ordering rule is ours. A
        behavioural-first interview reads differently from a system-design-first
        one even when the underlying prompts overlap.
        """
        rank = {kind: index for index, kind in enumerate(spec.emphasis)}
        tail = len(rank)
        return sorted(
            plan,
            key=lambda row: (
                rank.get(str(row.get("kind")), tail),
                str(row.get("id") or ""),
            ),
        )

    def _compose_intro(self, profile: RoleProfile, spec: InterviewType, count: int) -> str:
        """
        The interviewer's opening words.

        Generated when a model is available, because a fixed script makes the
        first ten seconds feel like a chatbot. Falls back to a short honest
        paragraph built from the listing, and the fallback says nothing about
        itself — the candidate does not need to know which path produced it.
        """
        focus = ", ".join(a.label for a in focus_areas(profile)[:4]) or "this role"
        fallback = (
            f"Hello. I'll be your interviewer for the {profile.title} role at "
            f"{profile.company}. We'll spend about {count} exchanges on "
            f"{focus}, and I'll follow up on anything you leave thin. "
            "Answer as you would in the room — there is no trick to this. "
            "Whenever you're ready, we'll begin."
        )
        if not (self.client.available):
            return fallback
        try:
            data = self.client.complete_json(
                system=(
                    "You are a senior hiring manager opening a live interview. "
                    "Write what you would actually say out loud, in three or four "
                    "short sentences. Name the role, name what you will cover, "
                    "tell them to take their time, and hand them the floor. "
                    "No enthusiasm, no flattery, no bullet points, no markdown, "
                    "no mention of scoring or rubrics. JSON only."
                ),
                user=(
                    f"{profile.as_brief()}\n\n"
                    f"Interview type: {spec.label}\n"
                    f"Planned exchanges: about {count}\n"
                    f"Focus: {focus}\n\n"
                    'Return JSON: {"intro": "..."}'
                ),
                temperature=0.5,
                max_tokens=300,
            )
            text = str(data.get("intro") or "").strip()
            return text[:900] if len(text) > 40 else fallback
        except (ProviderError, ProviderUnavailable) as exc:
            llm_logger.warning("intro generation failed: %s", exc)
            return fallback

    # ── lifecycle ───────────────────────────────────────────────────────────

    def begin(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        READY → AI_INTRO. Returns the interviewer's opening words.

        Idempotent past READY. `begin` is the first thing a client calls after a
        reload, and a reload can land after the interview has already started —
        returning the current state is correct, whereas rewinding to the
        introduction would replay it over the top of a conversation in progress.
        """
        record = self._owned(session_id, owner_id)
        state = self._state(record)
        if state is not RoomState.READY:
            return self.to_public(record)
        self._transition(record, RoomState.AI_INTRO)
        return self.to_public(record)

    def present_question(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        AI_INTRO / NEXT_QUESTION / AI_FOLLOW_UP → a question is on screen.

        Creates the turn row before the question is served. The candidate
        therefore always has a durable record of what they were asked, even if
        the network drops between this response and their first word.
        """
        record = self._owned(session_id, owner_id)
        state = self._state(record)

        # `COMPLETING` is not an error state: it is the room already finishing
        # because the plan ran out. A retry from a client that raced us must
        # return the current snapshot, not raise.
        if state in (
            RoomState.PAUSED,
            RoomState.COMPLETING,
            RoomState.COMPLETED,
            RoomState.COMPLETED_FAILED,
        ):
            return self.to_public(record)

        pending = self._pending_turn(record)
        if pending is not None:
            # Already asked and not yet answered: re-present rather than
            # duplicating. This is the reload path.
            self._transition(record, RoomState.WAITING_FOR_RESPONSE)
            return self.to_public(record)

        if state in (RoomState.AI_INTRO, RoomState.NEXT_QUESTION, RoomState.AI_FOLLOW_UP):
            if not self._materialise_next(record):
                # The plan and the follow-up budget are both spent. The client
                # calls `finish()` next, which is the only place a report is
                # written — this method never produces one.
                self._transition(record, RoomState.COMPLETING)
                return self.to_public(record)
            self._transition(record, RoomState.WAITING_FOR_RESPONSE)
            return self.to_public(record)

        raise RoomError(
            "interview_bad_state",
            f"This interview cannot present a question from state {state.value}.",
            status=409,
        )

    def _materialise_next(self, record: dict[str, Any]) -> bool:
        """
        Write the next question as a turn. Returns False when the plan is done.

        Every turn gets a fresh row so the transcript is a flat, ordered list —
        planned questions and follow-ups share one sequence, because that is the
        order the candidate actually experienced.
        """
        plan = record.get("plan") or []
        cursor = int(record.get("cursor") or 0)
        if cursor >= len(plan):
            return False

        row = plan[cursor]
        sequence = self._next_sequence(record)
        self._store.add_turn(
            record["id"],
            record["owner_id"],
            {
                "sequence": sequence,
                "kind": str(row.get("kind") or "role_depth"),
                "origin": "planned",
                "prompt": str(row.get("prompt") or ""),
                "category": str(row.get("kind") or "role_depth"),
                "difficulty": "moderate",
                "intent": str(row.get("intent") or ""),
                "anchor_skill": ", ".join(str(t) for t in (row.get("targets") or [])[:2]),
            },
        )
        self._store.update(
            record["id"], record["owner_id"], cursor=cursor + 1, state=record["state"]
        )
        record["cursor"] = cursor + 1
        return True

    def _next_sequence(self, record: dict[str, Any]) -> int:
        turns = self._store.turns(record["id"], record["owner_id"])
        return max((int(t["sequence"]) for t in turns), default=0) + 1

    def _pending_turn(self, record: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The turn currently on screen — asked, not yet answered."""
        for turn in reversed(self._store.turns(record["id"], record["owner_id"])):
            if turn.get("answer_text") in (None, ""):
                return turn
        return None

    def respond(
        self,
        session_id: str,
        owner_id: str,
        *,
        text: str,
        duration_seconds: float = 0.0,
        input_mode: str = "voice",
    ) -> dict[str, Any]:
        """
        Record one answer, evaluate it silently, and decide what is asked next.

        Idempotent by construction: the answer is written to the pending turn
        *before* the model is called, so a retry after a timeout cannot produce
        two turns from one answer. A duplicate submission finds no pending turn
        and is rejected with a 409 rather than silently counted twice.
        """
        record = self._owned(session_id, owner_id)
        state = self._state(record)

        if state in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED):
            raise RoomError(
                "interview_finished", "This interview has already finished.", status=409
            )
        if state is RoomState.PAUSED:
            raise RoomError(
                "interview_paused", "Resume the interview before answering.", status=409
            )
        if state is RoomState.COMPLETING:
            raise RoomError(
                "interview_completing", "This interview is being written up.", status=409
            )

        pending = self._pending_turn(record)
        if pending is None:
            raise RoomError(
                "no_question_pending",
                "There is no question waiting for an answer.",
                status=409,
            )

        answer = (text or "").strip()
        words = len(answer.split())
        self._transition(record, RoomState.PROCESSING_RESPONSE)

        # Durable first. The model call is the slow, failure-prone part; the
        # candidate's words must survive it.
        self._store.answer_turn(
            record["id"],
            owner_id,
            int(pending["sequence"]),
            answer_text=answer,
            duration_seconds=max(0.0, float(duration_seconds or 0.0)),
            word_count=words,
            evaluation={"pending": True},
        )

        evaluation = self._evaluate(record, pending, answer, words)
        self._store.answer_turn(
            record["id"], owner_id, int(pending["sequence"]), evaluation=evaluation
        )

        decision = self._decide_follow_up(
            record=record,
            question=pending,
            answer=answer,
            words=words,
            evaluation=evaluation,
        )

        if decision.action is FollowUpAction.ASK_FOLLOW_UP and decision.prompt:
            self._store.add_turn(
                record["id"],
                owner_id,
                {
                    "sequence": self._next_sequence(record),
                    "kind": "role_depth",
                    "origin": "follow_up",
                    "prompt": decision.prompt,
                    "category": "follow-up",
                    "difficulty": decision.depth.value,
                    "anchor_skill": decision.anchor_skill,
                    "follow_up": decision.to_record(),
                },
            )
            self._store.update(
                record["id"],
                owner_id,
                followups_spent=int(record.get("followups_spent") or 0) + 1,
            )
            record["followups_spent"] = int(record.get("followups_spent") or 0) + 1
            self._transition(record, RoomState.AI_FOLLOW_UP)
        else:
            self._transition(record, RoomState.NEXT_QUESTION)

        return self.to_public(record)

    def _evaluate(
        self, record: dict[str, Any], question: dict[str, Any], answer: str, words: int
    ) -> dict[str, Any]:
        """
        Silent evaluation. Never returned to the candidate mid-interview.

        Two failure modes are handled differently on purpose. A provider failure
        keeps the interview alive with an unevaluated turn — the candidate should
        never lose an interview because a free tier shed a worker. An answer too
        short to assess is *scored* as unevaluated deliberately: it is a real
        outcome, not a system fault.
        """
        if words < 8:
            return {"error": "answer too short to assess", "scores": {}, "evaluated": False}

        profile = self._profile(record)
        history = [
            {
                "text": t.get("answer_text") or "",
                "duration_seconds": float(t.get("duration_seconds") or 0.0),
            }
            for t in self._store.turns(record["id"], record["owner_id"])
            if t.get("answer_text")
        ]
        history.append({"text": answer, "duration_seconds": 0.0})
        speech = rubric.measure_speech(history)

        try:
            result = engine_module.evaluate_turn(
                profile,
                {
                    "kind": question.get("kind"),
                    "prompt": question.get("prompt"),
                    "intent": question.get("intent"),
                },
                answer,
                speech,
                client=self.client,
            )
        except (ProviderError, ProviderUnavailable) as exc:
            llm_logger.warning("turn evaluation failed: %s", exc)
            return {"error": str(exc), "scores": {}, "evaluated": False}
        result["evaluated"] = True
        return result

    def _decide_follow_up(
        self,
        *,
        record: dict[str, Any],
        question: dict[str, Any],
        answer: str,
        words: int,
        evaluation: dict[str, Any],
    ) -> Any:
        profile = self._profile(record)
        plan_total = int(record.get("plan_total") or 0)
        cursor = int(record.get("cursor") or 0)
        return self.followups.decide(
            profile=profile,
            question={
                "kind": question.get("kind"),
                "prompt": question.get("prompt"),
                "intent": question.get("intent"),
            },
            answer=answer,
            scores=dict(evaluation.get("scores") or {}),
            remaining_plan=max(0, plan_total - cursor),
            plan_total=plan_total,
            followups_spent=int(record.get("followups_spent") or 0),
            turn_index=int(question.get("sequence") or 1),
            turn_count=plan_total,
            last_question=cursor >= plan_total,
        )

    def skip(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        Move past a question the candidate cannot answer.

        Recorded honestly as a skipped turn rather than dropped, so coverage in
        the report reflects what was actually asked. Declining to answer is
        information; hiding it is not.
        """
        record = self._owned(session_id, owner_id)
        state = self._state(record)
        if state in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED):
            raise RoomError(
                "interview_finished", "This interview has already finished.", status=409
            )
        if state is RoomState.PAUSED:
            raise RoomError(
                "interview_paused", "Resume the interview before continuing.", status=409
            )

        pending = self._pending_turn(record)
        if pending is None:
            raise RoomError("no_question_pending", "There is no question to skip.", status=409)
        self._transition(record, RoomState.PROCESSING_RESPONSE)
        self._store.answer_turn(
            record["id"],
            owner_id,
            int(pending["sequence"]),
            answer_text="",
            duration_seconds=0.0,
            word_count=0,
            evaluation={"error": "skipped by the candidate", "scores": {}, "evaluated": False},
        )
        self._transition(record, RoomState.NEXT_QUESTION)
        return self.to_public(record)

    def pause(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        Suspend without terminating. Progress is already durable.

        Idempotent: pausing an already-paused session is not an error, because a
        double-click on a slow connection should not show one.
        """
        record = self._owned(session_id, owner_id)
        if self._state(record) is RoomState.PAUSED:
            return self.to_public(record)
        self._transition(record, RoomState.PAUSED)
        self._store.update(record["id"], owner_id, paused_at=datetime.now(timezone.utc).isoformat())
        return self.to_public(record)

    def resume(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        Return to the interview from PAUSED (or ERROR).

        The turn that was on screen is still on screen. Resuming is not a
        rewind: the candidate continues from the sentence they were on.
        """
        record = self._owned(session_id, owner_id)
        state = self._state(record)
        if state is RoomState.PAUSED:
            target = (
                RoomState.WAITING_FOR_RESPONSE if self._pending_turn(record) else RoomState.AI_INTRO
            )
        elif state is RoomState.ERROR:
            target = (
                RoomState.WAITING_FOR_RESPONSE if self._pending_turn(record) else RoomState.AI_INTRO
            )
        elif state in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED):
            raise RoomError(
                "interview_finished", "This interview has already finished.", status=409
            )
        else:
            return self.to_public(record)

        self._store.update(record["id"], owner_id, state_error=None)
        # The in-memory record has to forget it too, or `to_public` re-publishes
        # an error the room has already recovered from.
        record["state_error"] = None
        self._transition(record, target)
        return self.to_public(record)

    def mark_error(self, session_id: str, owner_id: str, message: str) -> dict[str, Any]:
        """
        Put the room in ERROR without losing the transcript.

        Used when the evaluator fails mid-interview. The candidate keeps the
        floor: they can retry the answer, skip the question, or end and still get
        whatever was already scored.
        """
        record = self._owned(session_id, owner_id)
        if self._state(record) in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED):
            return self.to_public(record)
        source = self._state(record)
        if can_transition(source, RoomState.ERROR):
            self._transition(record, RoomState.ERROR)
        self._store.update(record["id"], owner_id, state_error=message[:400])
        record["state_error"] = message[:400]
        return self.to_public(record)

    def finish(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        Write the report and end the session.

        Three outcomes, all honest:

        * ``completed``            the report exists.
        * ``completed_failed``     the transcript is saved and readable, but the
          evaluator could not run, so there is no score. Presenting a partial
          transcript as a finished interview would be a lie.
        * a 503                   the provider is configured but this run failed
          and is worth retrying; the session stays open.
        """
        record = self._owned(session_id, owner_id)
        if self._state(record) is RoomState.COMPLETED:
            assert record.get("report")  # COMPLETED implies a report exists
            return record["report"]  # type: ignore[return-value]
        if self._state(record) is RoomState.COMPLETED_FAILED:
            return self._unscoped_report(record)

        self._transition(record, RoomState.COMPLETING)

        if not self.client.available:
            report = self._unscoped_report(record)
            self._store.update(
                record["id"],
                owner_id,
                outcome="completed_failed",
                report_json=report,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            record["outcome"] = "completed_failed"
            record["report"] = report
            self._transition(record, RoomState.COMPLETED_FAILED)
            return report

        try:
            report = engine_module.build_report(self._as_engine_session(record), client=self.client)
        except (ProviderError, ProviderUnavailable) as exc:
            llm_logger.warning("report generation failed: %s", exc)
            report = self._unscoped_report(record, reason=str(exc))
            self._store.update(
                record["id"],
                owner_id,
                outcome="completed_failed",
                report_json=report,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            record["outcome"] = "completed_failed"
            record["report"] = report
            self._transition(record, RoomState.COMPLETED_FAILED)
            return report

        self._store.update(
            record["id"],
            owner_id,
            outcome="completed",
            report_json=report,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        record["outcome"] = "completed"
        record["report"] = report
        self._transition(record, RoomState.COMPLETED)
        return report

    def _mark_completed_outcome(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Terminal bookkeeping for a session the client is finishing.

        Writes the outcome before the state so a crash in between leaves a
        recoverable row rather than one claiming to be complete with nothing
        behind it.
        """
        if not record.get("outcome"):
            self._store.update(
                record["id"],
                record["owner_id"],
                outcome="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            record["outcome"] = "completed"
        return record

    def _unscoped_report(self, record: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
        """
        A report with no score in it.

        Shape-compatible with the real report so the UI has one code path, but
        every numeric field is null and the summary says plainly that nothing was
        evaluated. A report that *looks* scored but is not is the failure mode
        this avoids.
        """
        profile = self._profile(record)
        turns = self._store.turns(record["id"], record["owner_id"])
        answered = [t for t in turns if (t.get("answer_text") or "").strip()]
        band, band_label = rubric.band_for(profile.experience)
        return {
            "session_id": record["id"],
            "role": profile.to_dict(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scored": False,
            "measurement": {
                "overall": None,
                "band": band,
                "band_label": band_label,
                "verdict": None,
                "verdict_word": "Not scored",
                "dimensions_at_band": "0/0",
                "questions_asked": len(turns),
                "questions_answered": len(answered),
                "turns_evaluated": 0,
                "turns_unevaluated": len(answered),
                "engineered_by": "no evaluator available — transcript only",
            },
            "dimensions": [
                {
                    "key": dim.key,
                    "label": dim.label,
                    "weight": dim.weight,
                    "score": None,
                    "brief": dim.brief,
                }
                for dim in rubric.DIMENSIONS
            ],
            "communication_split": None,
            "speech": rubric.measure_speech(
                [
                    {
                        "text": t.get("answer_text") or "",
                        "duration_seconds": float(t.get("duration_seconds") or 0.0),
                    }
                    for t in answered
                ]
            ).to_dict(),
            "speech_notes": [
                "Delivery was measured from the recorded transcript, but no "
                "evaluator was available to judge content."
            ],
            "strengths": [],
            "gaps": [],
            "role_analysis": {
                "headline": "",
                "role_fit": "",
                "what_they_proved": [],
                "what_they_did_not_show": [],
                "evidence": [],
                "moves_the_rank": [],
                "interviewer_notes": [],
                "cheat_sheet": [],
            },
            "turns": [
                {
                    "kind": t.get("kind"),
                    "prompt": t.get("prompt"),
                    "answer": t.get("answer_text") or "",
                    "words": int(t.get("word_count") or 0),
                    "duration_seconds": float(t.get("duration_seconds") or 0.0),
                    "summary": "",
                    "note": "",
                    "evidence": [],
                    "scores": {},
                    "evaluated": False,
                }
                for t in turns
            ],
            "limitations": [
                "No interview model was configured, so these answers were not "
                "scored. The full transcript is below — it is the part worth "
                "reviewing.",
                *([f"The evaluator failed during this run ({reason})."] if reason else []),
                "Scores are one model's assessment of one conversation, not a hiring decision.",
            ],
        }

    def _as_engine_session(self, record: dict[str, Any]) -> InterviewSession:
        """
        Project a stored room session onto the engine's ``InterviewSession``.

        Reuse rather than reimplement: the report builder already knows how to
        weigh six dimensions, blend measured speech into communication and state
        its own limitations. Projecting the room's flat turn list into its shape
        costs one function and keeps one report implementation.
        """
        turns = [
            {
                "question": {
                    "kind": t.get("kind"),
                    "prompt": t.get("prompt"),
                    "intent": t.get("intent") or "",
                },
                "text": t.get("answer_text") or "",
                "words": int(t.get("word_count") or 0),
                "duration_seconds": float(t.get("duration_seconds") or 0.0),
                "evaluation": _evaluation_for_report(t),
            }
            for t in self._store.turns(record["id"], record["owner_id"])
        ]
        return InterviewSession(
            id=record["id"],
            profile=self._profile(record),
            plan=record.get("plan") or [],
            created_at=datetime.fromisoformat(record["created_at"]),
            cursor=int(record.get("cursor") or 0),
            turns=turns,
            finished=True,
        )

    # ── reads ───────────────────────────────────────────────────────────────

    def snapshot(self, session_id: str, owner_id: str) -> dict[str, Any]:
        return self.to_public(self._owned(session_id, owner_id))

    def feedback(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """The report, for a session that has finished."""
        record = self._owned(session_id, owner_id)
        if record.get("report"):
            return record["report"]  # type: ignore[return-value]
        if self._state(record) in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED):
            return self._unscoped_report(record)
        raise RoomError(
            "interview_not_finished",
            "This interview has not been written up yet.",
            status=409,
        )

    def transcript(self, session_id: str, owner_id: str) -> dict[str, Any]:
        """
        The chronological transcript.

        Deliberately *without* scores and internal notes. A candidate reading
        their own transcript should see the conversation, not the grading of it;
        the report is where assessment lives.
        """
        record = self._owned(session_id, owner_id)
        profile = self._profile(record)
        turns = self._store.turns(session_id, owner_id)
        pending = self._pending_turn(record)
        state = self._state(record)

        entries: list[dict[str, Any]] = []
        for turn in turns:
            entries.append(
                {
                    "sequence": int(turn["sequence"]),
                    "speaker": "interviewer",
                    "text": turn.get("prompt") or "",
                    "category": turn.get("category"),
                    "origin": turn.get("origin"),
                    "at": turn.get("answered_at") or turn.get("created_at"),
                }
            )
            if turn.get("answer_text"):
                entries.append(
                    {
                        "sequence": int(turn["sequence"]),
                        "speaker": "candidate",
                        "text": turn["answer_text"],
                        "input_mode": turn.get("input_mode"),
                        "at": turn.get("answered_at"),
                    }
                )
        if pending is not None and not entries:
            entries.append(
                {
                    "sequence": int(pending["sequence"]),
                    "speaker": "interviewer",
                    "text": pending.get("prompt") or "",
                    "category": pending.get("category"),
                    "origin": pending.get("origin"),
                    "at": pending.get("created_at"),
                    "pending": True,
                }
            )

        return {
            "session_id": session_id,
            "role": profile.to_dict(),
            "state": state.value,
            "interviewer": record.get("intro_text") or "",
            "entries": entries,
            "turns_asked": len(turns),
            "turns_answered": sum(1 for t in turns if t.get("answer_text")),
            "completed": state in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED),
            "audio_note": (
                "No audio was recorded or stored. Speech was transcribed in your "
                "browser and only the text was sent."
            ),
        }

    def history(self, owner_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Past room sessions for this account. No plan, no turns, no report."""
        return [
            {
                "session_id": row["id"],
                "opportunity_id": row["opportunity_id"],
                "role_title": row.get("role_title"),
                "role_company": row.get("role_company"),
                "state": row.get("state"),
                "outcome": row.get("outcome"),
                "interview_type": row.get("mode"),
                "turns_asked": int(row.get("plan_total") or 0),
                "created_at": row.get("created_at"),
                "completed_at": row.get("completed_at"),
                "resumable": row.get("outcome") is None,
            }
            for row in self._store.list_for_owner(owner_id, limit=limit)
        ]

    def recoverable(self, owner_id: str) -> list[dict[str, Any]]:
        """Sessions a reload or a crash left mid-flight."""
        out: list[dict[str, Any]] = []
        for row in self._store.recoverable_for_owner(owner_id):
            turns = self._store.turns(row["id"], owner_id)
            pending = next(
                (t for t in reversed(turns) if not (t.get("answer_text") or "").strip()), None
            )
            out.append(
                {
                    "session_id": row["id"],
                    "opportunity_id": row["opportunity_id"],
                    "role_title": row.get("role_title"),
                    "role_company": row.get("role_company"),
                    "state": row.get("state"),
                    "turns_answered": sum(1 for t in turns if t.get("answer_text")),
                    "turns_asked": len(turns),
                    "created_at": row.get("created_at"),
                    "resumes_at": (
                        pending.get("prompt") if pending else "the interviewer's introduction"
                    ),
                    "completed": False,
                }
            )
        return out

    def discard(self, session_id: str, owner_id: str) -> None:
        self._owned(session_id, owner_id)
        self._store.delete(session_id, owner_id)

    # ── projection ──────────────────────────────────────────────────────────

    def to_public(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        The only shape allowed to leave this module.

        Contains: what role, what type, where we are, what was asked, how far
        through, and honest statements about privacy.

        Does not contain: the plan beyond the current question, any per-turn
        score, any follow-up reason, any depth band, the model name, or the
        rubric. Those are the assessment, and the assessment arrives once, at
        the end, in the report.
        """
        profile = self._profile(record)
        state = self._state(record)
        turns = self._store.turns(record["id"], record["owner_id"])
        pending = self._pending_turn(record)
        answered = sum(1 for t in turns if (t.get("answer_text") or "").strip())
        plan_total = int(record.get("plan_total") or 0)
        cursor = int(record.get("cursor") or 0)
        followups = int(record.get("followups_spent") or 0)
        spec = resolve_type(record.get("interview_type"))

        return {
            "session_id": record["id"],
            "state": state.value,
            "phase": phase_for(state).value,
            "interviewer_state": presence_for(state).value,
            "interviewer": {
                "name": "DEDAN AI Interviewer",
                "role": f"Interviewing for {profile.title}",
                "company": profile.company,
            },
            "opportunity_id": record.get("opportunity_id"),
            "mode": record.get("mode"),
            "interview_type": spec.key,
            "error": record.get("state_error"),
            "role": profile.to_dict(),
            "interview": {
                "type_label": spec.label,
                "kind_label": _kind_label(plan_total),
                "duration_minutes": _estimate_minutes(plan_total),
                "focus": [{"label": a.label, "source": a.source} for a in focus_areas(profile)],
            },
            "progress": {
                # The counter is the *exchange* count, so a follow-up visibly
                # advances it. Hidden planned questions are never counted here.
                "position": len(turns),
                "answered": answered,
                "exchanged": len(turns),
                "planned_total": plan_total,
                "followups": followups,
                # A floor, not a promise: follow-ups may add exchanges, so this
                # never claims the interview is nearly over when it is not.
                "remaining_minimum": max(0, plan_total - cursor),
                "complete": state in (RoomState.COMPLETED, RoomState.COMPLETED_FAILED),
            },
            # Served for every state in which the room is talking and no question
            # has been asked yet. Narrowing this to one enum value left the stage
            # empty in `ai_asking` — the candidate watches the presence with
            # nothing on screen, which reads as a hang rather than a pause.
            "introduction": record.get("intro_text") or "" if state in _INTRO_STATES else "",
            "current": self._public_question(pending, len(turns)),
            "report_available": record.get("outcome") is not None,
            "outcome": record.get("outcome"),
            "privacy": privacy_statement(),
            "elapsed_seconds": _elapsed(record),
        }

    def _public_question(
        self, pending: Optional[dict[str, Any]], exchanged: int
    ) -> Optional[dict[str, Any]]:
        """
        The question as the candidate sees it.

        Note the omission: there is no `difficulty` field. For a planned question
        a difficulty band would be harmless, but for a follow-up it *is* the
        engine's read on the previous answer — publishing "advanced" tells a
        candidate they are doing well, which is the assessment leaking through a
        metadata field. The band stays in the stored record instead.
        """
        if pending is None:
            return None
        prompt = (pending.get("prompt") or "").strip()
        if not prompt:
            return None
        return {
            "id": pending["id"],
            "sequence": int(pending["sequence"]),
            "position": exchanged,
            "kind": pending.get("kind"),
            "category": pending.get("category"),
            "origin": pending.get("origin"),
            "prompt": prompt,
            "focus": question_metadata(
                str(pending.get("kind") or ""),
                str(pending.get("intent") or ""),
                [t.strip() for t in str(pending.get("anchor_skill") or "").split(",") if t.strip()],
            ),
            "suggested_seconds": 120,
        }


# ── module helpers ───────────────────────────────────────────────────────────


def privacy_statement() -> dict[str, Any]:
    """
    What actually happens to a candidate's words and voice.

    Read from settings rather than asserted in prose, so the answer stays true
    if the retention window is configured differently. Nothing here is aspirational:
    `audio_recorded` is False because the browser transcribes and the server
    never receives audio.
    """
    from config.settings import get_settings

    settings = get_settings()
    return {
        "audio_recorded": False,
        "audio_transmitted": False,
        "transcript_stored": True,
        "storage": "Encrypted at rest in this deployment's interview database.",
        "retention_days": settings.INTERVIEW_ROOM_RETENTION_DAYS,
        "third_party_model": (
            "Your answers are sent to the interview model configured by this "
            "deployment to be evaluated."
        ),
        "shared_with_employer": False,
    }


def _kind_label(plan_total: int) -> str:
    if plan_total <= 4:
        return "Focused"
    if plan_total <= 7:
        return "Technical + behavioural"
    return "Full interview"


def _estimate_minutes(plan_total: int) -> int:
    """
    A rough duration for orientation only.

    Rounded up to whole minutes and deliberately generous. It is an estimate the
    preparation screen shows; it is never a countdown imposed on the candidate.
    """
    return max(5, int(round(plan_total * 2.2)))


def _elapsed(record: dict[str, Any]) -> float:
    start = record.get("started_at") or record.get("created_at")
    if not start:
        return 0.0
    try:
        began = datetime.fromisoformat(str(start))
    except ValueError:
        return 0.0
    if began.tzinfo is None:
        began = began.replace(tzinfo=timezone.utc)
    held = float(record.get("duration_seconds") or 0.0)
    end = record.get("completed_at") or record.get("paused_at")
    if end:
        try:
            stop = datetime.fromisoformat(str(end))
            if stop.tzinfo is None:
                stop = stop.replace(tzinfo=timezone.utc)
            return round(max(0.0, (stop - began).total_seconds() + held), 1)
        except ValueError:
            pass
    return round(max(0.0, (datetime.now(timezone.utc) - began).total_seconds()), 1)


def _speech_capability(kind: str) -> str:
    """
    The server's view of voice: browser-only, in both directions.

    There is no speech backend on this deployment. Speech is the browser's own
    `SpeechRecognition` in and `speechSynthesis` out, so the server never
    receives audio and never transcribes. The client overlays its own feature
    detection on top of this.
    """
    return "browser_synthesis" if kind == "speak" else "browser_recognition"


def _evaluation_for_report(turn: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise a stored turn's evaluation for the report builder.

    Three shapes have to collapse to two. A turn written but not yet evaluated
    carries ``{"pending": true}``; a skipped or too-short turn carries an
    ``error``. The builder counts anything with an ``error`` as unevaluated and
    says so in its own limitations, which is exactly right for both.
    """
    evaluation = turn.get("evaluation")
    if not isinstance(evaluation, dict) or not evaluation:
        return {"error": "not evaluated"}
    if evaluation.get("pending"):
        return {"error": "not evaluated"}
    return evaluation


def _default_count() -> int:
    from config.settings import get_settings

    return get_settings().INTERVIEW_MAX_QUESTIONS


def get_room() -> InterviewRoom:
    """Process-wide room, built on the process-wide store."""
    return InterviewRoom(client=get_llm(), store=get_room_store())


__all__ = [
    "DEFAULT_TYPE",
    "FocusArea",
    "INTERVIEW_TYPES",
    "InterviewRoom",
    "InterviewType",
    "InterviewerState",
    "Phase",
    "RoomError",
    "RoomState",
    "focus_areas",
    "get_room",
    "privacy_statement",
    "question_metadata",
    "resolve_type",
]
