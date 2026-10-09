"""
Interview Room state machine.

The room is one authoritative state, not a pile of booleans in the browser and a
pile of booleans on the server. Both sides name the same states and both refuse
illegal moves, which is what makes a reload, a reconnect and a double-click
resolve to the same place.

    PREPARING            plan is being composed (server-side, one model call)
    READY                plan exists, the interviewer has not spoken yet
    AI_INTRO             the interviewer is introducing itself
    AI_ASKING            a question is being read aloud
    WAITING_FOR_RESPONSE the question is on screen; the candidate has the floor
    USER_RESPONDING      the candidate is speaking (or typing)
    PROCESSING_RESPONSE  the answer is submitted and being evaluated
    AI_FOLLOW_UP         the interviewer is bridging to a follow-up
    NEXT_QUESTION        moving to the next item in the plan
    PAUSED               suspended by the candidate; progress is durable
    COMPLETING           the final report is being written
    COMPLETED            the report exists
    ERROR                a recoverable failure; the transcript is intact

`InterviewerState` is the *presentation* of that state — what the presence
graphic is doing. It is deliberately separate: the room can be in
`WAITING_FOR_RESPONSE` while the presence is `IDLE`, and the two must not be
conflated into one enum.

Two rules are enforced here rather than in prose:

1. **Terminal states are terminal.** `COMPLETED`, `COMPLETED_FAILED` and
   `DISCARDED` have no outgoing edges. A finished interview cannot be reopened
   by a late request, which is the bug a reconnecting candidate would otherwise
   hit every time.
2. **`ERROR` is recoverable.** Every other non-terminal state can reach `ERROR`,
   and `ERROR` can reach back into the live states. Losing an interview to a
   dropped connection is the one unacceptable failure mode here.

Pure module: no I/O, no provider, no store. The transitions are unit-testable
without a running service, which is the point.
"""

from __future__ import annotations

from enum import StrEnum


class RoomState(StrEnum):
    """Authoritative session state. Wire values are the lowercase names."""

    PREPARING = "preparing"
    READY = "ready"
    AI_INTRO = "ai_intro"
    AI_ASKING = "ai_asking"
    WAITING_FOR_RESPONSE = "waiting_for_response"
    USER_RESPONDING = "user_responding"
    PROCESSING_RESPONSE = "processing_response"
    AI_FOLLOW_UP = "ai_follow_up"
    NEXT_QUESTION = "next_question"
    PAUSED = "paused"
    COMPLETING = "completing"
    COMPLETED = "completed"
    #: The transcript is durable but the report could not be produced. Distinct
    #: from `COMPLETED` so the UI can say exactly that instead of implying a
    #: score exists, or implying nothing was saved.
    COMPLETED_FAILED = "completed_failed"
    ERROR = "error"


#: Outcomes that end the session.
TERMINAL_STATES: frozenset[RoomState] = frozenset({RoomState.COMPLETED, RoomState.COMPLETED_FAILED})


class TerminalOutcome(StrEnum):
    COMPLETED = "completed"
    COMPLETED_FAILED = "completed_failed"
    DISCARDED = "discarded"


#: The live states a session passes through. Kept as a set so the "is this
#: interview still running?" check is one membership test.
LIVE_STATES: frozenset[RoomState] = frozenset(
    {
        RoomState.AI_INTRO,
        RoomState.AI_ASKING,
        RoomState.WAITING_FOR_RESPONSE,
        RoomState.USER_RESPONDING,
        RoomState.PROCESSING_RESPONSE,
        RoomState.AI_FOLLOW_UP,
        RoomState.NEXT_QUESTION,
    }
)


class InterviewerState(StrEnum):
    """What the interviewer presence is doing. Presentation only."""

    IDLE = "idle"
    INTRODUCING = "introducing"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    USER_SPEAKING = "user_speaking"
    PROCESSING = "processing"
    TRANSITIONING = "transitioning"
    COMPLETE = "complete"


class ConnectionState(StrEnum):
    """Transport health, reported by the client, never asserted by the server."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


#: Which presence animation belongs to which room state. A single mapping keeps
#: the "what should the room look like right now" question in one testable place
#: instead of scattered `if (state === ...)` chains in the view.
PRESENCE_BY_ROOM_STATE: dict[RoomState, InterviewerState] = {
    RoomState.PREPARING: InterviewerState.THINKING,
    RoomState.READY: InterviewerState.IDLE,
    RoomState.AI_INTRO: InterviewerState.INTRODUCING,
    RoomState.AI_ASKING: InterviewerState.SPEAKING,
    RoomState.WAITING_FOR_RESPONSE: InterviewerState.LISTENING,
    RoomState.USER_RESPONDING: InterviewerState.USER_SPEAKING,
    RoomState.PROCESSING_RESPONSE: InterviewerState.PROCESSING,
    RoomState.AI_FOLLOW_UP: InterviewerState.SPEAKING,
    RoomState.NEXT_QUESTION: InterviewerState.TRANSITIONING,
    RoomState.PAUSED: InterviewerState.IDLE,
    RoomState.COMPLETING: InterviewerState.THINKING,
    RoomState.COMPLETED: InterviewerState.COMPLETE,
    RoomState.ERROR: InterviewerState.IDLE,
}


#: Client-observable phase names. These are what the API reports and what the
#: preparation animation steps through. They are a *view* of the machine, kept
#: separate so a UI step never has to invent its own lifecycle vocabulary.
class Phase(StrEnum):
    BRIEF = "brief"
    PREPARING = "preparing"
    READY = "ready"
    INTRO = "intro"
    QUESTION = "question"
    RESPONDING = "responding"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETING = "completing"
    FEEDBACK = "feedback"
    ERROR = "error"


PHASE_BY_ROOM_STATE: dict[RoomState, Phase] = {
    RoomState.PREPARING: Phase.PREPARING,
    RoomState.READY: Phase.READY,
    RoomState.AI_INTRO: Phase.INTRO,
    RoomState.AI_ASKING: Phase.QUESTION,
    RoomState.WAITING_FOR_RESPONSE: Phase.QUESTION,
    RoomState.USER_RESPONDING: Phase.RESPONDING,
    RoomState.PROCESSING_RESPONSE: Phase.PROCESSING,
    RoomState.AI_FOLLOW_UP: Phase.QUESTION,
    RoomState.NEXT_QUESTION: Phase.QUESTION,
    RoomState.PAUSED: Phase.PAUSED,
    RoomState.COMPLETING: Phase.COMPLETING,
    RoomState.COMPLETED: Phase.FEEDBACK,
    RoomState.COMPLETED_FAILED: Phase.FEEDBACK,
    RoomState.ERROR: Phase.ERROR,
}


class IllegalTransition(RuntimeError):
    """A caller asked for a move the machine does not allow."""

    def __init__(self, source: RoomState, target: RoomState) -> None:
        super().__init__(f"cannot move an interview from {source.value} to {target.value}")
        self.source = source
        self.target = target


# Every legal edge, stated once. Anything not in here raises.
TRANSITIONS: dict[RoomState, frozenset[RoomState]] = {
    RoomState.PREPARING: frozenset({RoomState.READY, RoomState.ERROR}),
    RoomState.READY: frozenset({RoomState.AI_INTRO, RoomState.ERROR, RoomState.PAUSED}),
    RoomState.AI_INTRO: frozenset(
        {RoomState.AI_ASKING, RoomState.WAITING_FOR_RESPONSE, RoomState.ERROR, RoomState.PAUSED}
    ),
    RoomState.AI_ASKING: frozenset(
        {RoomState.WAITING_FOR_RESPONSE, RoomState.ERROR, RoomState.PAUSED}
    ),
    RoomState.WAITING_FOR_RESPONSE: frozenset(
        {
            RoomState.USER_RESPONDING,
            RoomState.PROCESSING_RESPONSE,
            # A candidate may skip a question they cannot answer; that is a real
            # outcome and must not deadlock the session.
            RoomState.NEXT_QUESTION,
            # Ending the interview mid-question is allowed. It has to be: the
            # candidate decides when they are done, not the question counter.
            RoomState.COMPLETING,
            RoomState.ERROR,
            RoomState.PAUSED,
        }
    ),
    RoomState.USER_RESPONDING: frozenset(
        {
            RoomState.PROCESSING_RESPONSE,
            # Abandoning mid-answer keeps the room usable.
            RoomState.WAITING_FOR_RESPONSE,
            RoomState.ERROR,
            RoomState.PAUSED,
        }
    ),
    RoomState.PROCESSING_RESPONSE: frozenset(
        {
            RoomState.AI_FOLLOW_UP,
            RoomState.NEXT_QUESTION,
            RoomState.AI_ASKING,
            RoomState.ERROR,
            RoomState.PAUSED,
        }
    ),
    RoomState.AI_FOLLOW_UP: frozenset(
        {
            RoomState.AI_ASKING,
            RoomState.WAITING_FOR_RESPONSE,
            # A client that reconnects while the follow-up is on screen has the
            # question already and answers it directly, without re-presenting.
            RoomState.PROCESSING_RESPONSE,
            RoomState.ERROR,
            RoomState.PAUSED,
        }
    ),
    RoomState.NEXT_QUESTION: frozenset(
        {
            RoomState.AI_ASKING,
            RoomState.WAITING_FOR_RESPONSE,
            RoomState.COMPLETING,
            RoomState.ERROR,
            RoomState.PAUSED,
        }
    ),
    RoomState.PAUSED: frozenset(
        {
            RoomState.AI_INTRO,
            RoomState.AI_ASKING,
            RoomState.WAITING_FOR_RESPONSE,
            RoomState.USER_RESPONDING,
            RoomState.PROCESSING_RESPONSE,
            RoomState.AI_FOLLOW_UP,
            RoomState.NEXT_QUESTION,
            RoomState.COMPLETING,
            RoomState.COMPLETED,
            RoomState.COMPLETED_FAILED,
            RoomState.ERROR,
        }
    ),
    # `COMPLETING` reaching a terminal state is what makes a report retrievable
    # again after a refresh, without re-running the model.
    RoomState.COMPLETING: frozenset(
        {RoomState.COMPLETED, RoomState.COMPLETED_FAILED, RoomState.ERROR, RoomState.PAUSED}
    ),
    RoomState.ERROR: frozenset(
        {
            RoomState.AI_INTRO,
            RoomState.AI_ASKING,
            RoomState.WAITING_FOR_RESPONSE,
            RoomState.USER_RESPONDING,
            RoomState.PROCESSING_RESPONSE,
            RoomState.AI_FOLLOW_UP,
            RoomState.NEXT_QUESTION,
            RoomState.COMPLETING,
            RoomState.PAUSED,
        }
    ),
    # Terminal. Present with empty sets so `TRANSITIONS[state]` never KeyErrors.
    RoomState.COMPLETED: frozenset(),
    RoomState.COMPLETED_FAILED: frozenset(),
}


def can_transition(source: RoomState, target: RoomState) -> bool:
    """
    Whether `source -> target` is a legal move.

    A self-transition is always legal and is a no-op. That is not leniency: the
    room writes its state before answering, so a retried `finish` arrives while
    the room is already `COMPLETING`, and a re-present arrives while it is already
    `WAITING_FOR_RESPONSE`. Refusing those would turn every network retry into an
    error the candidate has to think about.
    """
    if source is target:
        return True
    return target in TRANSITIONS.get(source, frozenset())


def require_transition(source: RoomState, target: RoomState) -> RoomState:
    """Assert a move and return it, or raise `IllegalTransition`."""
    if not can_transition(source, target):
        raise IllegalTransition(source, target)
    return target


def is_terminal(state: RoomState) -> bool:
    return state in TERMINAL_STATES


def is_live(state: RoomState) -> bool:
    return state in LIVE_STATES


def presence_for(state: RoomState) -> InterviewerState:
    """The presence animation that belongs to a room state."""
    return PRESENCE_BY_ROOM_STATE.get(state, InterviewerState.IDLE)


def phase_for(state: RoomState) -> Phase:
    """The client-facing phase name for a room state."""
    return PHASE_BY_ROOM_STATE.get(state, Phase.ERROR)
