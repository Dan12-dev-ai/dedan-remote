"""
Interview Room endpoints.

    GET    /api/interview/status                      can an AI interview run?
    GET    /api/interview/sessions                    this account's room history
    GET    /api/interview/sessions/recoverable        sessions a crash left open
    POST   /api/interview/sessions                    open a room (no question returned)
    GET    /api/interview/sessions/{id}               full public room state (resume)
    POST   /api/interview/sessions/{id}/begin         READY → AI_INTRO
    POST   /api/interview/sessions/{id}/question      advance to the next question
    POST   /api/interview/sessions/{id}/answers       submit an answer (NO score returned)
    POST   /api/interview/sessions/{id}/skip          decline the current question
    POST   /api/interview/sessions/{id}/pause         suspend, keeping progress
    POST   /api/interview/sessions/{id}/resume        continue where the candidate was
    POST   /api/interview/sessions/{id}/finish        the ranked report
    GET    /api/interview/sessions/{id}/feedback      the stored report
    GET    /api/interview/sessions/{id}/transcript    chronological transcript
    POST   /api/interview/sessions/{id}/ticket        one-use SSE ticket
    GET    /api/interview/sessions/{id}/events        SSE: typed room events
    DELETE /api/interview/sessions/{id}               delete the interview

Three contracts hold on every one of these routes:

1. **Sign-in required.** A live interview is a record about a person: it has a
   transcript, a report and a retention policy. All of that needs an owner. The
   browser-only rehearsal mode stays anonymous — it is a different product, and
   it stores nothing.

2. **Ownership is enforced server-side, on every read and every write.** The
   session id in the URL is untrusted input. `RoomSessionStore` returns nothing
   for a session belonging to someone else, and the router turns that into a 404
   rather than a 403 — a 403 confirms the id exists.

3. **The plan and the scoring never cross the wire.** The response model
   (`RoomSessionOut`) has no field for either, so a future edit that adds one
   fails schema validation rather than leaking an interview's question list to a
   candidate who could then prepare from it.

Transport: SSE for events, plain POST for commands. The candidate never streams
audio over the event channel; SSE is one-directional server→client, which is
exactly its strength and the reason it was chosen over WebSockets here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api import services
from api.deps import ApiError, enforce_rate_limit, get_current_user
from api.schemas import (
    InterviewTypeValue,
    RoomAvailability,
    RoomDiscardOut,
    RoomHistoryEntry,
    RoomRecoverableOut,
    RoomSessionOut,
    RoomStreamTicketOut,
    RoomTranscriptOut,
)
from interview.provider import ProviderError, ProviderUnavailable
from interview.room import INTERVIEW_TYPES, RoomError, get_room, privacy_statement
from interview.room_state import ConnectionState, RoomState
from interview.stream_ticket import TICKET_TTL_SECONDS, get_ticket_store

logger = logging.getLogger("dedan.api.interview")

router = APIRouter(prefix="/api/interview", tags=["interview"])

# A live interview costs model calls, so the caps stay hard even for signed-in
# callers: one account can only burn so many model calls an hour.
SESSIONS_PER_HOUR = 6
ANSWERS_PER_HOUR = 120
FINISH_PER_HOUR = 12
REPORT_PER_MINUTE = 30

#: How long one SSE connection stays open before the server closes it cleanly.
#: Longer than a typical proxy idle timeout, so the stream ends on our terms
#: rather than being severed mid-report.
STREAM_BUDGET_SECONDS = 900.0
#: Room-state poll interval inside the stream. The room only changes on a POST
#: from the candidate, so this is a cheap read against one SQLite row, not a
#: broadcast fan-out — and it means the stream cannot miss an event.
STREAM_POLL_SECONDS = 0.25
#: Keep-alive comment interval. Proxies drop an idle connection; a comment every
#: 20s is invisible to `EventSource` and keeps the pipe warm.
STREAM_KEEPALIVE_TICKS = 80
#: Cap on state transitions replayed by one stream. A turn that keeps flapping
#: should not hold a connection open for fifteen minutes.
STREAM_MAX_TRANSITIONS = 240


# ── request bodies ───────────────────────────────────────────────────────────


class OpenRoomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Opportunity slug or id. Resolved server-side; never trusted as an id.
    opportunity: str = Field(min_length=2, max_length=200)
    max_questions: Optional[int] = Field(default=None, ge=3, le=12)
    interview_type: InterviewTypeValue = "role_rehearsal"
    mode: str = Field(default="live", pattern="^(live|rehearsal)$")


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=20_000)
    duration_seconds: float = Field(default=0.0, ge=0.0, le=7_200.0)
    input_mode: str = Field(default="voice", pattern="^(text|voice)$")


# ── helpers ──────────────────────────────────────────────────────────────────


def _owner_id(user: dict[str, Any]) -> str:
    owner = str(user.get("id") or "").strip()
    if not owner:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "auth_required", "Sign in to use this interview."
        )
    return owner


def _call(fn, /, *args, **kwargs):
    """Run a room operation, converting domain errors into API errors."""
    try:
        return fn(*args, **kwargs)
    except RoomError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    except ProviderUnavailable as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE, "interview_not_configured", str(exc)
        ) from exc
    except ProviderError as exc:
        raise ApiError(status.HTTP_502_BAD_GATEWAY, "interview_model_failed", str(exc)) from exc


def _sse(event: str, data: Any) -> bytes:
    """One SSE frame. The event name gives the browser a typed listener."""
    body = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()


def _sse_comment(text: str = "keep-alive") -> bytes:
    return f": {text}\n\n".encode()


def _require_owned(session_id: str, owner_id: str) -> dict[str, Any]:
    """Settle ownership before doing any work, including opening a stream."""
    return _call(get_room().snapshot, session_id, owner_id)


def _event_for(state: str) -> tuple[str, dict[str, Any]]:
    """
    Translate a room state into the event name and payload the client subscribes to.

    The event vocabulary is the client's only subscription surface, so it is
    stated here in one place rather than emitted ad hoc from five handlers.
    """
    table: dict[str, tuple[str, dict[str, Any]]] = {
        RoomState.PREPARING.value: ("interview.preparing", {}),
        RoomState.READY.value: ("interview.ready", {}),
        RoomState.AI_INTRO.value: (
            "interviewer.started_speaking",
            {"phase": "introduction"},
        ),
        RoomState.AI_ASKING.value: ("question.presented", {"phase": "question"}),
        RoomState.WAITING_FOR_RESPONSE.value: ("question.presented", {"phase": "answer"}),
        RoomState.USER_RESPONDING.value: ("candidate.started_speaking", {}),
        RoomState.PROCESSING_RESPONSE.value: ("response.processing", {}),
        RoomState.AI_FOLLOW_UP.value: ("followup.generated", {}),
        RoomState.NEXT_QUESTION.value: ("question.completed", {}),
        RoomState.PAUSED.value: ("interview.paused", {}),
        RoomState.COMPLETING.value: ("interview.completing", {}),
        RoomState.COMPLETED.value: ("interview.completed", {"outcome": "completed"}),
        RoomState.COMPLETED_FAILED.value: (
            "interview.completed",
            {"outcome": "completed_failed"},
        ),
        RoomState.ERROR.value: ("interview.error", {}),
    }
    return table.get(state, ("interview.ready", {}))


# ── capability ───────────────────────────────────────────────────────────────


@router.get("/status", response_model=RoomAvailability)
def interview_status(request: Request) -> dict[str, Any]:
    """
    Whether an AI interview can run right now, and what it would look like.

    Public and secret-free: capability and shape, never the model name, the
    provider or any credential. The frontend calls this before offering the mode,
    so it can tell "this deployment has no model" apart from "we could not reach
    the server" — the two need different messages to the candidate.
    """
    from config.settings import get_settings

    enforce_rate_limit(request, "interview_status", limit=60, window=60)
    ok, reason = get_room().available()
    settings = get_settings()
    return {
        "live": ok,
        "mode": "live" if ok else "unavailable",
        "detail": "" if ok else reason,
        "default_questions": settings.INTERVIEW_MAX_QUESTIONS,
        "interview_types": sorted(INTERVIEW_TYPES),
        "privacy": privacy_statement(),
    }


# ── history + recovery ───────────────────────────────────────────────────────


@router.get("/sessions", response_model=list[RoomHistoryEntry])
def list_sessions(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """The signed-in candidate's interviews, newest first."""
    enforce_rate_limit(request, "interview_list", limit=60, window=60)
    return _call(get_room().history, _owner_id(user), limit=20)


@router.get("/sessions/recoverable", response_model=list[RoomRecoverableOut])
def recoverable_sessions(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """
    Sessions left mid-interview, so no candidate loses one to a reload.

    The strongest guarantee this product can make about an interview in progress:
    the work is on the server, not in the tab. Declared before
    `/sessions/{session_id}` so the literal path wins the route match.
    """
    enforce_rate_limit(request, "interview_recoverable", limit=60, window=60)
    return _call(get_room().recoverable, _owner_id(user))


# ── lifecycle ────────────────────────────────────────────────────────────────


@router.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=RoomSessionOut)
def open_room(
    request: Request,
    payload: OpenRoomRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Open an interview room for one opportunity.

    Returns the preparation state, not the first question. The candidate reads
    the brief, picks their interview type, and presses Begin — only then does the
    interviewer introduce itself and the first question arrive. That order is the
    difference between walking into a room and being handed a form.
    """
    enforce_rate_limit(request, "interview_start", limit=SESSIONS_PER_HOUR, window=3600)
    ok, reason = get_room().available()
    if not ok:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "interview_not_configured",
            reason or "AI interviews are not configured on this deployment.",
        )

    row = services.find_rows_by_slug_or_id(payload.opportunity)
    if not row:
        raise ApiError(404, "not_found", "Opportunity not found.")
    job = services.serialize_job(row, registry=services.source_registry(), include_explanation=True)

    return _call(
        get_room().open,
        owner_id=_owner_id(user),
        job=job,
        opportunity_id=payload.opportunity,
        count=payload.max_questions,
        interview_type=payload.interview_type,
        mode=payload.mode,
    )


@router.get("/sessions/{session_id}", response_model=RoomSessionOut)
def get_room_state(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    The full public state of a room — the resume path.

    After a reload the client fetches this and renders whatever the server says.
    It never restores from local state, because local state is exactly what a
    reload lost, and a stale local question is worse than a half-second fetch.
    """
    enforce_rate_limit(request, "interview_get", limit=240, window=60)
    return _call(get_room().snapshot, session_id, _owner_id(user))


@router.post("/sessions/{session_id}/begin", response_model=RoomSessionOut)
def begin_room(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """READY → AI_INTRO. Returns the interviewer's opening words."""
    enforce_rate_limit(request, "interview_begin", limit=SESSIONS_PER_HOUR, window=3600)
    return _call(get_room().begin, session_id, _owner_id(user))


@router.post("/sessions/{session_id}/question", response_model=RoomSessionOut)
def present_question(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Put the next question on screen.

    Also the idempotent re-present path: asking again for the question that is
    already showing returns that question rather than duplicating it, so a
    double-click or a retried request cannot skip a turn.
    """
    enforce_rate_limit(request, "interview_question", limit=ANSWERS_PER_HOUR, window=3600)
    return _call(get_room().present_question, session_id, _owner_id(user))


@router.post("/sessions/{session_id}/answers", response_model=RoomSessionOut)
def submit_answer(
    request: Request,
    session_id: str,
    payload: AnswerRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Record one answer and let the room decide what the interviewer asks next.

    No score comes back — not because the client hides one, but because the
    response model has no field for it. A candidate who can watch each answer
    being graded adjusts to the grader instead of thinking, which destroys the
    only thing a rehearsal can offer.
    """
    enforce_rate_limit(request, "interview_answer", limit=ANSWERS_PER_HOUR, window=3600)
    return _call(
        get_room().respond,
        session_id,
        _owner_id(user),
        text=payload.text,
        duration_seconds=payload.duration_seconds,
        input_mode=payload.input_mode,
    )


@router.post("/sessions/{session_id}/skip", response_model=RoomSessionOut)
def skip_question(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Decline the current question and move on.

    Recorded as a skipped turn, not dropped. Coverage in the report then reflects
    what was actually asked, and declining to answer stays visible as the answer
    it is.
    """
    enforce_rate_limit(request, "interview_skip", limit=ANSWERS_PER_HOUR, window=3600)
    return _call(get_room().skip, session_id, _owner_id(user))


@router.post("/sessions/{session_id}/pause", response_model=RoomSessionOut)
def pause_room(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Suspend the interview without ending it.

    Safe to call twice: pausing an already-paused room is not an error, because a
    double-click on a slow connection should not present one.
    """
    enforce_rate_limit(request, "interview_pause", limit=SESSIONS_PER_HOUR, window=3600)
    return _call(get_room().pause, session_id, _owner_id(user))


@router.post("/sessions/{session_id}/resume", response_model=RoomSessionOut)
def resume_room(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Continue from PAUSED, or from ERROR after a failure.

    The question that was on screen is the question that comes back. Resuming is
    not a rewind.
    """
    enforce_rate_limit(request, "interview_resume", limit=ANSWERS_PER_HOUR, window=3600)
    return _call(get_room().resume, session_id, _owner_id(user))


@router.post("/sessions/{session_id}/finish")
def finish_room(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Write the report and end the interview.

    Untyped on purpose: the report has a deliberately wide shape (a scored and an
    unscored variant), and declaring it here would mean restating
    `interview.engine.build_report` in pydantic. It is validated where it is
    produced, and every room-state response on this router is typed.

    Never returns a partial report presented as final. If the evaluator could not
    run, the transcript comes back unscored and says so in its own text.
    """
    enforce_rate_limit(request, "interview_finish", limit=FINISH_PER_HOUR, window=3600)
    return _call(get_room().finish, session_id, _owner_id(user))


@router.get("/sessions/{session_id}/feedback")
def room_feedback(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    The report for a finished interview.

    Separate from `finish` so a refresh on the feedback screen re-reads the
    stored report instead of re-running the model. Regenerating a debrief on every
    reload produces a different verdict each time, which is not a report — it is a
    coin toss.
    """
    enforce_rate_limit(request, "interview_feedback", limit=REPORT_PER_MINUTE, window=60)
    return _call(get_room().feedback, session_id, _owner_id(user))


@router.get("/sessions/{session_id}/transcript", response_model=RoomTranscriptOut)
def room_transcript(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    The chronological transcript, for the "View transcript" drawer.

    Fetched on demand rather than shipped with the room state: it is the largest
    payload in the product and almost nobody opens it mid-interview.
    """
    enforce_rate_limit(request, "interview_transcript", limit=60, window=60)
    return _call(get_room().transcript, session_id, _owner_id(user))


@router.delete("/sessions/{session_id}", response_model=RoomDiscardOut)
def discard_room(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Delete an interview and everything attached to it.

    A real deletion, not a soft flag: the row goes, the cascade takes the
    transcript with it. The UI says "deleted", so this has to be true.
    """
    enforce_rate_limit(request, "interview_cancel", limit=SESSIONS_PER_HOUR, window=3600)
    _call(get_room().discard, session_id, _owner_id(user))
    return {"status": "discarded"}


# ── real-time ────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/ticket", response_model=RoomStreamTicketOut)
def issue_stream_ticket(
    request: Request,
    session_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Exchange the caller's bearer token for a one-use, 60-second stream ticket.

    Exists because `EventSource` cannot set an `Authorization` header, and the
    alternatives — a session token in the query string, or a cookie — both leak
    the account credential into logs or add CSRF surface to the whole API. See
    `interview/stream_ticket.py`.
    """
    enforce_rate_limit(request, "interview_ticket", limit=240, window=60)
    owner = _owner_id(user)
    _require_owned(session_id, owner)
    ticket = get_ticket_store().issue(owner_id=owner, session_id=session_id)
    return {
        "ticket": ticket.value,
        "session_id": session_id,
        "expires_in": int(TICKET_TTL_SECONDS),
    }


@router.get("/sessions/{session_id}/events")
async def room_events(
    session_id: str,
    request: Request,
    ticket: str = Query(min_length=8, max_length=200),
) -> StreamingResponse:
    """
    Server-Sent Events: the room's state, pushed as it changes.

    SSE rather than WebSockets because the traffic is one-directional. The
    candidate's own actions go over ordinary POSTs, which give them retry
    semantics, idempotency and a request id per action; `EventSource` adds
    automatic reconnection, survives proxies that mangle upgrade headers, and
    needs no extra dependency. The one thing that would justify a WebSocket here —
    bidirectional audio — does not exist on this deployment, and designing for it
    anyway would mean building for a feature we do not have.

    Events:

        connection.established      the first frame, carrying the full room state
        interview.ready             the room is open and the candidate has the floor
        interviewer.started_speaking the introduction is being read aloud
        question.presented          a question is on screen
        candidate.started_speaking  the floor is with the candidate
        response.processing         the answer is being evaluated
        followup.generated          the interviewer is bridging to a follow-up
        question.completed          the answer was recorded and the room advanced
        interview.paused            suspended; progress is safe
        interview.completing        the report is being written
        interview.completed         finished
        interview.error             recoverable failure; the transcript is intact

    The stream polls the room state rather than being pushed to from the POST
    handlers. That is a deliberate trade: the room only changes when this same
    candidate POSTs, so the poll is one indexed SQLite read per quarter-second
    against a single row, and in exchange a handler can never deadlock by
    emitting into a full queue, and no transition can be missed.
    """
    owner = get_ticket_store().redeem(ticket, session_id=session_id)
    if not owner:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "stream_ticket_invalid",
            "That live connection ticket has expired. Reconnect to continue.",
        )
    # Ownership is settled before the stream opens, so an unowned session gets a
    # 404 rather than an open connection that errors on every frame.
    _require_owned(session_id, owner)

    room = get_room()

    async def events() -> AsyncIterator[bytes]:
        opened = time.monotonic()
        last_state: Optional[str] = None
        transitions = 0
        tick = 0

        yield _sse(
            "connection.established",
            {
                "session_id": session_id,
                "connection": ConnectionState.CONNECTED.value,
                "transport": "sse",
                "room": room.snapshot(session_id, owner),
            },
        )

        while time.monotonic() - opened < STREAM_BUDGET_SECONDS:
            if await request.is_disconnected():
                return

            try:
                snapshot = await asyncio.get_running_loop().run_in_executor(
                    None, room.snapshot, session_id, owner
                )
            except RoomError as exc:
                yield _sse("interview.error", {"code": exc.code, "message": exc.message})
                return
            except Exception:  # noqa: BLE001 - a bad poll must not kill the app
                logger.exception("interview event stream failed for %s", session_id[:8])
                yield _sse(
                    "interview.error",
                    {
                        "code": "stream_failed",
                        "message": "The live connection dropped. Reconnecting is safe.",
                        "connection": ConnectionState.RECONNECTING.value,
                    },
                )
                return

            state = str(snapshot.get("state") or "")
            if state != last_state:
                transitions += 1
                if transitions > STREAM_MAX_TRANSITIONS:
                    yield _sse(
                        "interview.error",
                        {
                            "code": "stream_flapping",
                            "message": "This connection is flapping. Reconnect to continue.",
                        },
                    )
                    return
                last_state = state
                name, extra = _event_for(state)
                yield _sse(name, {**extra, "room": snapshot})

                if state in (RoomState.COMPLETED.value, RoomState.COMPLETED_FAILED.value):
                    yield _sse(
                        "interview.completed",
                        {
                            "outcome": snapshot.get("outcome"),
                            "report_available": True,
                            "final": True,
                        },
                    )
                    return

            tick += 1
            if tick % STREAM_KEEPALIVE_TICKS == 0:
                yield _sse_comment()
            await asyncio.sleep(STREAM_POLL_SECONDS)

        # Budget exhausted. Say so rather than closing silently.
        yield _sse(
            "interview.error",
            {
                "code": "stream_budget",
                "message": (
                    "This live connection reached its time limit. Reconnect to "
                    "continue — the interview is saved."
                ),
                "connection": ConnectionState.RECONNECTING.value,
            },
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Without this nginx buffers the stream and the client sees nothing
            # until the interview is already over.
            "X-Accel-Buffering": "no",
        },
    )
