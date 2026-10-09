"""
Short-lived stream tickets for the Interview Room event channel.

`EventSource` cannot set an `Authorization` header. The usual workarounds are
both worse than they look:

- **Put the session token in the query string.** It works, and it writes a
  bearer credential into every access log, proxy log and browser history entry
  that ever touches the URL. A leaked log becomes a leaked account.
- **Fall back to cookies.** This API is bearer-only by design (`allow_credentials`
  is false), and introducing a cookie would add CSRF surface to every other route
  to solve one route's problem.

So the browser exchanges its bearer token once, over an authenticated POST, for a
ticket that is:

  * bound to one session id and one user id,
  * valid for :data:`TICKET_TTL_SECONDS`,
  * single-use — consumed on first connection,
  * bounded in count, so a flood cannot grow the store.

A ticket in a URL that expires in 60 seconds and dies on use is a materially
smaller thing to leak than a session token. This is the standard mitigation and it
is cheap enough to be worth doing properly.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

#: Long enough to survive a slow page load and a proxy handshake, short enough
#: that a leaked URL is worthless by the time anyone reads a log.
TICKET_TTL_SECONDS = 60.0

#: Upper bound on outstanding tickets. Above this the oldest expire first.
MAX_TICKETS = 512


@dataclass(frozen=True)
class Ticket:
    value: str
    owner_id: str
    session_id: str
    expires_at: float


class StreamTicketStore:
    """In-memory, single-use, TTL-bounded ticket issuer."""

    def __init__(self, *, ttl_seconds: float = TICKET_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, *, owner_id: str, session_id: str) -> Ticket:
        now = time.monotonic()
        with self._lock:
            # Evict before inserting, leaving one slot free — otherwise the store
            # settles at `MAX_TICKETS + 1` and the bound in the name is a lie.
            self._evict(now, headroom=1)
            ticket = Ticket(
                value=secrets.token_urlsafe(24),
                owner_id=owner_id,
                session_id=session_id,
                expires_at=now + self._ttl,
            )
            self._tickets[ticket.value] = ticket
            return ticket

    def redeem(self, value: str, *, session_id: str) -> Optional[str]:
        """
        Consume a ticket and return its owner id, or ``None``.

        Single-use and session-bound: redeeming twice fails, and a ticket issued
        for one interview cannot open the stream for another. Both checks matter
        — a replayable ticket is just a session token with extra steps.
        """
        now = time.monotonic()
        with self._lock:
            ticket = self._tickets.pop(value, None)
        if ticket is None:
            return None
        if ticket.expires_at < now:
            return None
        if ticket.session_id != session_id:
            return None
        return ticket.owner_id

    def _evict(self, now: float, *, headroom: int = 0) -> None:
        """Drop expired tickets, then the oldest until `headroom` slots are free."""
        for key in [k for k, v in self._tickets.items() if v.expires_at < now]:
            self._tickets.pop(key, None)
        overflow = len(self._tickets) - (MAX_TICKETS - headroom)
        if overflow > 0:
            for key in list(self._tickets)[:overflow]:
                self._tickets.pop(key, None)

    def count(self) -> int:
        with self._lock:
            return len(self._tickets)


_store: Optional[StreamTicketStore] = None
_store_lock = threading.Lock()


def get_ticket_store() -> StreamTicketStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = StreamTicketStore()
    return _store


def reset_ticket_store() -> None:
    global _store
    with _store_lock:
        _store = None
