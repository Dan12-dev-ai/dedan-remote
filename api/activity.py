"""
Activity signals for DEDAN Remote.

The discovery engine's notification infrastructure (Email / Telegram /
Discord) notifies the *operator* when a cycle finds new listings. This module
is different: it builds the in-product activity feed for one signed-in user.

Data-integrity rule for this module: every item is derived from a timestamp
that the database actually stores — `saved_jobs.saved_at`,
`applications.updated_at` / `created_at`, or `jobs.discovered_at`. Nothing is
simulated, no counters are invented, and no event is emitted speculatively.
When a user has done nothing yet, this returns an empty list on purpose so the
UI can show a teaching empty state instead of fake activity.
"""

from __future__ import annotations

from typing import Any

from api import services
from api.store import get_user_store

# Human labels for the stored application statuses.
APPLICATION_LABELS: dict[str, str] = {
    "viewed": "Viewed",
    "saved": "Shortlisted",
    "application_started": "Application started",
    "applied": "Applied",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Closed",
}

# Discovery signals are capped so a large backfill of newly discovered rows
# cannot bury the user's own actions under machine events.
_MAX_DISCOVERY_SIGNALS = 6

NOTE = (
    "Built from your stored saves, application updates and the discovery "
    "timestamps of opportunities matching your preferences."
)


def _signal_id(prefix: str, *parts: str) -> str:
    return ":".join((prefix, *parts))


def build_notifications(user_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """Reverse-chronological activity feed for one user (real events only)."""
    store = get_user_store()
    registry = services.source_registry()
    items: list[dict[str, Any]] = []

    # ── 1. Saves ────────────────────────────────────────────────────────────
    for entry in store.list_saved(user_id):
        row = services.get_row(entry["job_id"])
        if not row:
            continue  # listing no longer exists — say nothing rather than lie
        job = services.serialize_job(row, registry=registry)
        items.append(
            {
                "id": _signal_id("saved", entry["job_id"]),
                "kind": "saved",
                "title": f"Saved {job['title']}",
                "detail": (f"{job['source_info']['name']} · {job['location_label']}"),
                "at": entry["saved_at"],
                "job": job,
            }
        )

    # ── 2. Application pipeline movement ───────────────────────────────────
    for entry in store.list_applications(user_id):
        row = services.get_row(entry["job_id"])
        if not row:
            continue
        job = services.serialize_job(row, registry=registry)
        status = entry["status"]
        label = APPLICATION_LABELS.get(status, status.replace("_", " ").title())
        items.append(
            {
                "id": _signal_id("application", entry["id"]),
                "kind": "application",
                "title": f"{label} — {job['title']}",
                "detail": (
                    f"Tracking updated · {job['source_info']['name']}"
                    if status != "application_started"
                    else f"Tracking started · {job['source_info']['name']}"
                ),
                "at": entry["updated_at"] or entry["created_at"],
                "job": job,
            }
        )

    # ── 3. Fresh opportunities matching stored preferences ─────────────────
    prefs = store.get_preferences(user_id)
    if any((prefs["categories"], prefs["regions"], prefs["experience"])):
        pool = services.query_jobs(sort="newest", page=1, page_size=60)
        matched = []
        for row in pool["items"]:
            if prefs["categories"] and not any(
                services._matches_category(row, c) for c in prefs["categories"]
            ):
                continue
            if prefs["regions"] and not services.matches_region(row, prefs["regions"]):
                continue
            if not row.get("discovered_at"):
                continue  # cannot date the event honestly
            matched.append(row)
            if len(matched) >= _MAX_DISCOVERY_SIGNALS:
                break
        for row in matched:
            job = services.serialize_job(row, registry=registry, prefs=prefs)
            items.append(
                {
                    "id": _signal_id("discovery", row["id"]),
                    "kind": "discovery",
                    "title": f"New match — {job['title']}",
                    "detail": (
                        f"Discovered {job['freshness']['label'].lower()} from "
                        f"{job['source_info']['name']}"
                    ),
                    "at": row["discovered_at"],
                    "job": job,
                }
            )

    # Newest first; rows without a usable timestamp sink to the bottom.
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return items[:limit]


def note_for(items: list[dict[str, Any]]) -> str:
    """Honest framing for the feed, adapted to what actually exists."""
    if not items:
        return (
            "No recorded activity yet. Saving an opportunity or starting an "
            "application creates a signal here."
        )
    return NOTE
