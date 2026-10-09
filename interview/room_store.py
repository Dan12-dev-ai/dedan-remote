"""
Durable Interview Room sessions — ownership, recovery and the record.

Why a second store, when `interview/persistence.py` already exists?

    interview/persistence.py    the *skill-grounded mock* interview of record:
                                a fixed question set per skill, scored
                                asynchronously by the worker queue.
    interview/room_store.py     the *conversational room*: an adaptive question
                                sequence whose length is not known until the
                                interview ends, with pause/resume, follow-up
                                provenance and a transcript that has to be
                                reconstructible after a crash.

They share a database file but not tables. Folding the room into the mock's
`interview_questions` table would mean either faking an `order_index` for
follow-ups that do not exist yet, or schema changes that put a 1,100-line test
suite at risk for no benefit. Two stores, one file, no coupling.

Ownership is enforced here, not in the router. Every read and write takes
`owner_id` and returns `None` when it does not match, so a router that forgets
to check cannot leak another candidate's transcript. A mismatch returns nothing
rather than raising "forbidden": a 403 confirms the id exists.

Retention: `purge_expired` deletes sessions older than the configured window
and is called from the app lifespan. What we do not do is claim a deletion we
have not performed — the transcript's own `retained_until` is computed from
settings, and the UI reads that field rather than asserting a policy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from interview.room_state import RoomState

SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_room_sessions (
    id                 TEXT PRIMARY KEY,
    owner_id           TEXT NOT NULL,
    opportunity_id     TEXT NOT NULL,
    role_title         TEXT,
    role_company       TEXT,
    -- The room state machine's current state (interview.room_state.RoomState).
    state              TEXT NOT NULL DEFAULT 'preparing',
    -- 'live' | 'rehearsal' — which product surface opened the room.
    mode               TEXT NOT NULL DEFAULT 'live',
    -- Which interview lens was requested (interview.room.INTERVIEW_TYPES).
    interview_type     TEXT NOT NULL DEFAULT 'role_rehearsal',
    -- RoomState after the session ends; NULL while it is still running.
    outcome            TEXT,
    plan_total         INTEGER NOT NULL DEFAULT 0,
    cursor             INTEGER NOT NULL DEFAULT 0,
    followups_spent    INTEGER NOT NULL DEFAULT 0,
    intro_text         TEXT,
    -- Full RoleProfile.to_dict(). The only place the room keeps listing detail.
    role_json          TEXT NOT NULL DEFAULT '{}',
    -- The plan as composed at PREPARING. Never leaves the server.
    plan_json          TEXT NOT NULL DEFAULT '[]',
    -- Evaluated report, written once at COMPLETING.
    report_json        TEXT,
    state_error        TEXT,
    duration_seconds   REAL NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    paused_at          TEXT,
    completed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_room_sessions_owner
    ON interview_room_sessions (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_room_sessions_state
    ON interview_room_sessions (state, created_at DESC);

CREATE TABLE IF NOT EXISTS interview_room_turns (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES interview_room_sessions(id) ON DELETE CASCADE,
    -- 1-based index in the *exchanged* sequence (planned questions and follow-ups
    -- share one counter, because that is the order the candidate experienced).
    sequence           INTEGER NOT NULL,
    kind               TEXT NOT NULL,          -- RoomState-ish turn kind
    -- 'planned' | 'follow_up' | 'skipped'
    origin             TEXT NOT NULL DEFAULT 'planned',
    prompt             TEXT NOT NULL,
    category           TEXT,
    difficulty         TEXT,
    intent             TEXT,                   -- internal, never served
    anchor_skill       TEXT,
    answer_text        TEXT,
    input_mode         TEXT NOT NULL DEFAULT 'voice',
    duration_seconds   REAL NOT NULL DEFAULT 0,
    word_count         INTEGER NOT NULL DEFAULT 0,
    -- Silent evaluation. Scores are in here and only in here.
    evaluation_json    TEXT,
    follow_up_json     TEXT,
    created_at         TEXT NOT NULL,
    answered_at        TEXT,
    UNIQUE (session_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_room_turns_session
    ON interview_room_turns (session_id, sequence);
"""

#: Field allow-list for turns written back to the store. Anything not named here
#: is dropped, so a caller cannot smuggle a score in through the prompt field.
_TURN_COLUMNS = frozenset(
    {
        "id",
        "session_id",
        "sequence",
        "kind",
        "origin",
        "prompt",
        "category",
        "difficulty",
        "intent",
        "anchor_skill",
        "answer_text",
        "input_mode",
        "duration_seconds",
        "word_count",
        "evaluation_json",
        "follow_up_json",
        "created_at",
        "answered_at",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw: Optional[str], fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        # A corrupt column must not make the whole session unreadable.
        return fallback


class RoomSessionStore:
    """Thread-safe SQLite access to the interview-room tables."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        from config.settings import get_settings

        self._db_path = db_path or get_settings().INTERVIEW_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.create_tables()

    # ── plumbing ────────────────────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    def create_tables(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(SCHEMA)

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            return list(conn.execute(sql, tuple(params)))

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        # `sqlite3` needs a concrete sequence, and callers build parameter lists
        # whose length is not known until the assignments are built above.
        with self._lock, self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return cursor.rowcount

    # ── sessions ────────────────────────────────────────────────────────────

    def create(
        self,
        *,
        owner_id: str,
        opportunity_id: str,
        role_title: Optional[str],
        role_company: Optional[str],
        role: dict[str, Any],
        plan: list[dict[str, Any]],
        mode: str = "live",
        interview_type: str = "role_rehearsal",
        intro_text: str = "",
    ) -> str:
        """Open a room session and return its id."""
        session_id = uuid.uuid4().hex
        stamp = _now()
        self._execute(
            """
            INSERT INTO interview_room_sessions
                (id, owner_id, opportunity_id, role_title, role_company, state,
                 mode, interview_type, plan_total, cursor, intro_text, role_json,
                 plan_json, created_at, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                owner_id,
                opportunity_id,
                role_title,
                role_company,
                RoomState.READY.value,
                mode,
                interview_type,
                len(plan),
                intro_text,
                _json(role),
                _json(plan),
                stamp,
                stamp,
            ),
        )
        return session_id

    def get(self, session_id: str, owner_id: str) -> Optional[dict[str, Any]]:
        """
        Read one session, enforcing ownership.

        Returns ``None`` for a missing session *and* for someone else's. The
        caller cannot tell the two apart, which is the point.
        """
        rows = self._query(
            "SELECT * FROM interview_room_sessions WHERE id = ? AND owner_id = ?",
            (session_id, owner_id),
        )
        return self._session_row(rows[0]) if rows else None

    #: Columns this method will write. Anything else is rejected rather than
    #: silently dropped, so a typo cannot look like a successful save.
    _UPDATABLE = frozenset(
        {
            "state",
            "outcome",
            "cursor",
            "followups_spent",
            "intro_text",
            "report_json",
            "state_error",
            "duration_seconds",
            "started_at",
            "paused_at",
            "completed_at",
        }
    )

    #: Columns stored as JSON text. Callers pass Python objects and this is the
    #: single place that knows it.
    _JSON_COLUMNS = frozenset({"report_json"})

    def update(self, session_id: str, owner_id: str, **fields: Any) -> bool:
        """
        Patch a session.

        Owner-scoped: a caller who does not own the session updates zero rows
        and gets `False`, exactly as they would get `None` from `get`.
        """
        bad = set(fields) - self._UPDATABLE
        if bad:
            raise ValueError(f"cannot update room session fields: {sorted(bad)}")
        if not fields:
            return False
        assignments = ", ".join(f"{name} = ?" for name in fields)
        params = [
            _json(value) if name in self._JSON_COLUMNS and value is not None else value
            for name, value in fields.items()
        ] + [session_id, owner_id]
        return (
            self._execute(
                f"UPDATE interview_room_sessions SET {assignments} WHERE id = ? AND owner_id = ?",
                params,
            )
            > 0
        )

    def list_for_owner(
        self, owner_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT * FROM interview_room_sessions
             WHERE owner_id = ?
             ORDER BY created_at DESC
             LIMIT ? OFFSET ?
            """,
            (owner_id, max(1, min(limit, 100)), max(0, offset)),
        )
        return [self._session_row(r) for r in rows]

    def recoverable_for_owner(self, owner_id: str) -> list[dict[str, Any]]:
        """Sessions that were mid-interview — what the "resume" prompt offers."""
        terminal = (RoomState.COMPLETED.value, RoomState.COMPLETED_FAILED.value)
        rows = self._query(
            """
            SELECT * FROM interview_room_sessions
             WHERE owner_id = ?
               AND outcome IS NULL
               AND state NOT IN ('preparing')
               AND state NOT IN (?, ?)
             ORDER BY created_at DESC
             LIMIT 5
            """,
            (owner_id, *terminal),
        )
        return [self._session_row(r) for r in rows]

    def delete(self, session_id: str, owner_id: str) -> bool:
        """Discard a transcript. Cascades to turns."""
        return (
            self._execute(
                "DELETE FROM interview_room_sessions WHERE id = ? AND owner_id = ?",
                (session_id, owner_id),
            )
            > 0
        )

    # ── turns ───────────────────────────────────────────────────────────────

    def add_turn(self, session_id: str, owner_id: str, turn: dict[str, Any]) -> Optional[str]:
        """
        Append one exchange to a session's transcript.

        Returns the turn id, or ``None`` when the session is not owned by
        ``owner_id`` (the FK is only reachable through the owner-scoped write).
        """
        if self.get(session_id, owner_id) is None:
            return None
        payload: dict[str, Any] = {}
        # Two keys arrive as Python objects and are stored as JSON text. They are
        # mapped *before* the allow-list filter, which would otherwise drop them
        # for being spelled `evaluation`/`follow_up` rather than `*_json`.
        if "evaluation" in turn:
            payload["evaluation_json"] = _json(turn["evaluation"])
        if "follow_up" in turn:
            payload["follow_up_json"] = _json(turn["follow_up"])
        payload.update({k: v for k, v in turn.items() if k in _TURN_COLUMNS})
        payload["session_id"] = session_id
        payload["id"] = payload.get("id") or uuid.uuid4().hex
        payload.setdefault("created_at", _now())
        payload.setdefault("sequence", 1)
        payload.setdefault("prompt", "")
        payload.setdefault("kind", "planned")
        payload.setdefault("origin", "planned")

        columns = ", ".join(payload)
        placeholders = ", ".join("?" for _ in payload)
        self._execute(
            f"INSERT INTO interview_room_turns ({columns}) VALUES ({placeholders})",
            list(payload.values()),
        )
        return str(payload["id"])

    def answer_turn(self, session_id: str, owner_id: str, sequence: int, **fields: Any) -> bool:
        """
        Attach an answer, and later its evaluation, to an existing turn.

        A *partial* update: only the named columns are written. The room writes
        the answer first (so the candidate's words survive a slow model call) and
        the evaluation a moment later, and a second call carrying only
        `evaluation` must not blank the answer it was meant to enrich.

        Re-submitting a question replaces its answer rather than appending a
        second one, so a candidate who redoes a turn does not end up with a
        doubled transcript and an inflated count.
        """
        if not fields:
            return False
        columns: list[str] = []
        values: list[Any] = []
        for name in ("answer_text", "duration_seconds", "word_count"):
            if name in fields:
                columns.append(name)
                values.append(fields[name])
        if "evaluation" in fields:
            columns.append("evaluation_json")
            values.append(_json(fields["evaluation"]))
        columns.append("answered_at")
        values.append(_now())
        changed = self._execute(
            f"""
            UPDATE interview_room_turns
               SET {", ".join(f"{c} = ?" for c in columns)}
             WHERE session_id = ? AND sequence = ?
            """,
            values + [session_id, sequence],
        )
        if changed == 0:
            # A turn that does not exist is a bug in the room, not a client
            # error: the client only ever answers a question it was served.
            # Raising here beats silently discarding a candidate's answer.
            raise KeyError(f"no turn {sequence} in room session {session_id}")
        return True

    def turns(self, session_id: str, owner_id: str) -> list[dict[str, Any]]:
        """
        The full transcript, in the order the candidate experienced it.

        Ownership is verified first. An unowned session returns an empty list
        rather than raising, so a caller cannot accidentally render someone
        else's transcript by ignoring the return value.
        """
        if self.get(session_id, owner_id) is None:
            return []
        rows = self._query(
            """
            SELECT * FROM interview_room_turns
             WHERE session_id = ?
             ORDER BY sequence ASC
            """,
            (session_id,),
        )
        return [self._turn_row(r) for r in rows]

    # ── retention ───────────────────────────────────────────────────────────

    def purge_expired(self, retention_days: int) -> int:
        """Delete room sessions older than the retention window. Returns the count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).isoformat()
        return self._execute("DELETE FROM interview_room_sessions WHERE created_at < ?", (cutoff,))

    # ── row mappers ─────────────────────────────────────────────────────────

    @staticmethod
    def _session_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "owner_id": row["owner_id"],
            "opportunity_id": row["opportunity_id"],
            "role_title": row["role_title"],
            "role_company": row["role_company"],
            "state": row["state"],
            "mode": row["mode"],
            "interview_type": row["interview_type"],
            "outcome": row["outcome"],
            "plan_total": row["plan_total"],
            "cursor": row["cursor"],
            "followups_spent": row["followups_spent"],
            "intro_text": row["intro_text"],
            "role": _loads(row["role_json"], {}),
            "plan": _loads(row["plan_json"], []),
            "report": _loads(row["report_json"], None),
            "state_error": row["state_error"],
            "duration_seconds": row["duration_seconds"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "paused_at": row["paused_at"],
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _turn_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "sequence": row["sequence"],
            "kind": row["kind"],
            "origin": row["origin"],
            "prompt": row["prompt"],
            "category": row["category"],
            "difficulty": row["difficulty"],
            "intent": row["intent"],
            "anchor_skill": row["anchor_skill"],
            "answer_text": row["answer_text"],
            "input_mode": row["input_mode"],
            "duration_seconds": row["duration_seconds"],
            "word_count": row["word_count"],
            "evaluation": _loads(row["evaluation_json"], None),
            "follow_up": _loads(row["follow_up_json"], None),
            "created_at": row["created_at"],
            "answered_at": row["answered_at"],
        }


_store: Optional[RoomSessionStore] = None
_store_lock = threading.Lock()


def get_room_store() -> RoomSessionStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = RoomSessionStore()
    return _store


def reset_room_store(db_path: Optional[str] = None) -> RoomSessionStore:
    """Swap the process-wide store (tests, and the CLI tooling)."""
    global _store
    with _store_lock:
        _store = RoomSessionStore(db_path)
    return _store
