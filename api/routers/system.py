"""Internal system dashboard (token-protected). Never exposes secrets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from api import services
from api.deps import enforce_rate_limit, require_system_token

router = APIRouter(prefix="/api/system", tags=["system"],
                   dependencies=[Depends(require_system_token)])


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@router.get("/overview")
def overview(request: Request) -> dict[str, Any]:
    """Operational metrics for the discovery engine (no credentials)."""
    enforce_rate_limit(request, "system", limit=60, window=60)
    conn = services.connect_jobs_db()
    try:
        totals = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN discovered_at >= ? THEN 1 ELSE 0 END) AS new_24h"
            " FROM jobs",
            ((datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )).isoformat(),),
        ).fetchone()
        cycles = conn.execute(
            "SELECT COUNT(*) AS n, AVG(duration_seconds) AS avg_dur"
            " FROM execution_history"
        ).fetchone()
        last_cycle = conn.execute(
            "SELECT * FROM execution_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_cycle = conn.execute(
            "SELECT * FROM execution_history ORDER BY id DESC LIMIT 1"
            " OFFSET 1"
        ).fetchone()
        sources = conn.execute(
            "SELECT source, last_checked, last_success, last_error,"
            " consecutive_failures, circuit_open FROM website_status"
            " ORDER BY source"
        ).fetchall()
    finally:
        conn.close()

    duplicates = None
    if last_cycle:
        found = last_cycle["jobs_found"] or 0
        new = last_cycle["jobs_new"] or 0
        duplicates = max(0, found - new)

    def _clip(msg: Optional[str]) -> Optional[str]:
        if not msg:
            return None
        # Never surface anything that looks like a credential or DSN.
        lowered = msg.lower()
        for marker in ("password", "token", "secret", "postgres://",
                       "redis://", "bolt://", "api_key", "apikey"):
            if marker in lowered:
                return "[redacted error detail]"
        return msg[:300]

    return {
        "jobs_total": totals["total"] if totals else 0,
        "jobs_new_today": totals["new_24h"] if totals else 0,
        "cycles_total": cycles["n"] if cycles else 0,
        "avg_cycle_seconds": (
            round(cycles["avg_dur"], 2)
            if cycles and cycles["avg_dur"] else None
        ),
        "last_cycle": dict(last_cycle) if last_cycle else None,
        "previous_cycle": dict(prev_cycle) if prev_cycle else None,
        "duplicates_last_cycle": duplicates,
        "sources": [
            {
                "source": s["source"],
                "last_checked": s["last_checked"],
                "last_success": s["last_success"],
                "last_error": _clip(s["last_error"]),
                "consecutive_failures": s["consecutive_failures"] or 0,
                "circuit_open": bool(s["circuit_open"]),
            }
            for s in sources
        ],
    }
