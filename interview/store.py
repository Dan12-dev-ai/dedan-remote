"""
In-memory store for live interview sessions.

Sessions hold a full transcript, so they are deliberately ephemeral: bounded
count, bounded TTL, never written to disk. A restart clears them, which is the
honest behaviour for a conversational interview — there is nothing durable to
recover.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from config.settings import get_settings

if TYPE_CHECKING:  # avoids a circular import: engine imports the store
    from interview.engine import InterviewSession


class InterviewStore:
    def __init__(
        self,
        ttl_seconds: Optional[int] = None,
        max_sessions: Optional[int] = None,
    ) -> None:
        self._sessions: dict[str, InterviewSession] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max = max_sessions

    # ── config (lazy so tests can construct the store before settings load) ──

    @property
    def _ttl_seconds(self) -> int:
        if self._ttl is not None:
            return self._ttl
        return get_settings().INTERVIEW_SESSION_TTL_SECONDS

    @property
    def _max_sessions(self) -> int:
        if self._max is not None:
            return self._max
        return get_settings().INTERVIEW_MAX_SESSIONS

    # ── helpers ─────────────────────────────────────────────────────────────

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def _expired(self, session: "InterviewSession", now: datetime) -> bool:
        age = now - session.created_at
        return age > timedelta(seconds=self._ttl_seconds)

    def _evict(self, now: datetime) -> None:
        for key in [k for k, v in self._sessions.items() if self._expired(v, now)]:
            self._sessions.pop(key, None)
        # Oldest-first eviction once the cap is exceeded.
        overflow = len(self._sessions) - self._max_sessions
        if overflow > 0:
            ordered = sorted(self._sessions.values(), key=lambda s: s.created_at)
            for session in ordered[:overflow]:
                self._sessions.pop(session.id, None)

    # ── api ─────────────────────────────────────────────────────────────────

    def put(self, session: "InterviewSession") -> "InterviewSession":
        with self._lock:
            now = datetime.now(timezone.utc)
            self._evict(now)
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> "InterviewSession":
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError("interview session not found")
            if self._expired(session, datetime.now(timezone.utc)):
                self._sessions.pop(session_id, None)
                raise KeyError("interview session expired")
            return session

    def pop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


_store: Optional[InterviewStore] = None


def get_store() -> InterviewStore:
    global _store
    if _store is None:
        _store = InterviewStore()
    return _store


def reset_store() -> None:
    global _store
    _store = None
