"""User endpoints: applications, profile/preferences, recommendations."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status

from api import services
from api.deps import ApiError, enforce_rate_limit, get_current_user
from api.schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationPatch,
    PreferencesOut,
    ProfileOut,
    ProfilePatch,
    RecommendationPage,
    UserOut,
)
from api.store import get_user_store

router = APIRouter(prefix="/api", tags=["user"])

# Kanban columns shown by the frontend (ordered).
BOARD_COLUMNS = ["saved", "applied", "interview", "offer"]


def _app_out(entry: dict, registry: dict) -> ApplicationOut:
    row = services.get_row(entry["job_id"])
    if not row:
        raise ApiError(404, "job_missing",
                       "The underlying opportunity no longer exists.")
    return ApplicationOut(
        id=entry["id"],
        job=services.serialize_job(row, registry=registry),
        status=entry["status"],
        note=entry["note"],
        created_at=entry["created_at"],
        updated_at=entry["updated_at"],
    )


# ── Applications ─────────────────────────────────────────────────────────────

@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    request: Request,
    user: dict = Depends(get_current_user),
) -> list[ApplicationOut]:
    """All tracked applications for the signed-in user."""
    enforce_rate_limit(request, "applications", limit=120, window=60)
    store = get_user_store()
    registry = services.source_registry()
    out = []
    for entry in store.list_applications(user["id"]):
        out.append(_app_out(entry, registry))
    return out


@router.post("/applications", response_model=ApplicationOut,
             status_code=status.HTTP_201_CREATED)
def create_application(
    request: Request,
    payload: ApplicationCreate,
    user: dict = Depends(get_current_user),
) -> ApplicationOut:
    """Start (or update) application tracking for an opportunity."""
    enforce_rate_limit(request, "applications", limit=60, window=60)
    if not services.get_row(payload.job_id):
        raise ApiError(404, "not_found", "Opportunity not found.")
    store = get_user_store()
    entry = store.create_application(
        user["id"], payload.job_id, payload.status, payload.note,
    )
    return _app_out(entry, services.source_registry())


@router.patch("/applications/{app_id}", response_model=ApplicationOut)
def patch_application(
    request: Request,
    app_id: str,
    payload: ApplicationPatch,
    user: dict = Depends(get_current_user),
) -> ApplicationOut:
    """Update status/note — only statuses the user explicitly records."""
    enforce_rate_limit(request, "applications", limit=120, window=60)
    store = get_user_store()
    entry = store.patch_application(
        user["id"], app_id, payload.status, payload.note,
    )
    if entry is None:
        raise ApiError(404, "not_found", "Application not found.")
    return _app_out(entry, services.source_registry())


# ── Profile & preferences ───────────────────────────────────────────────────

@router.get("/profile", response_model=ProfileOut)
def get_profile(user: dict = Depends(get_current_user)) -> ProfileOut:
    store = get_user_store()
    prefs = store.get_preferences(user["id"])
    return ProfileOut(
        user=UserOut(id=user["id"], email=user["email"],
                     display_name=user.get("display_name"),
                     created_at=user["created_at"]),
        preferences=PreferencesOut(**prefs),
    )


@router.patch("/profile", response_model=ProfileOut)
def patch_profile(
    payload: ProfilePatch,
    user: dict = Depends(get_current_user),
) -> ProfileOut:
    """Update display name and/or discovery preferences."""
    store = get_user_store()
    if payload.display_name is not None:
        store.update_display_name(user["id"], payload.display_name)
        user["display_name"] = payload.display_name
    if (payload.categories is not None or payload.experience is not None
            or payload.regions is not None):
        store.update_preferences(
            user["id"], payload.categories, payload.experience,
            payload.regions,
        )
    prefs = store.get_preferences(user["id"])
    return ProfileOut(
        user=UserOut(id=user["id"], email=user["email"],
                     display_name=user.get("display_name"),
                     created_at=user["created_at"]),
        preferences=PreferencesOut(**prefs),
    )


# ── Recommendations ─────────────────────────────────────────────────────────

@router.get("/recommendations", response_model=RecommendationPage)
def recommendations(
    request: Request,
    limit: int = Query(default=8, ge=1, le=20),
    user: dict = Depends(get_current_user),
) -> RecommendationPage:
    """
    Preference-ranked opportunities.

    Pipeline: stored preferences → region/category/experience eligibility
    filters → system score ordering. Deterministic rules — labeled as
    calculated, not as generative AI.
    """
    enforce_rate_limit(request, "recommendations", limit=60, window=60)
    store = get_user_store()
    prefs = store.get_preferences(user["id"])
    registry = services.source_registry()

    if not (prefs["categories"] or prefs["regions"] or prefs["experience"]):
        result = services.query_jobs(sort="score", page=1, page_size=limit)
        items = [services.serialize_job(r, registry=registry)
                 for r in result["items"]]
        return RecommendationPage(
            items=items,
            basis="top_ranked",
            method=(
                "No preferences saved yet — showing top-ranked opportunities "
                "by system score. Set preferences for personalization."
            ),
            preferences=PreferencesOut(**prefs),
        )

    # Fetch a wider pool; category + region are hard eligibility filters,
    # experience level is a ranking bonus (hard-filtering it would often
    # empty the list on sparse scraper data).
    result = services.query_jobs(sort="score", page=1, page_size=100)
    pool = result["items"]
    if prefs["categories"]:
        pool = [r for r in pool if any(
            services._matches_category(r, c) for c in prefs["categories"]
        )]
    if prefs["regions"]:
        pool = [r for r in pool if services.matches_region(r, prefs["regions"])]

    def _rank(r: dict) -> float:
        score = float(r.get("score") or 0)
        if prefs["experience"] == "beginner" and services.is_beginner_friendly(r):
            score += 10.0  # transparent bonus, surfaced in match reasons
        elif prefs["experience"] == "advanced" and services.experience_hint(r) == "advanced":
            score += 10.0
        return score

    pool.sort(key=_rank, reverse=True)

    items = [
        services.serialize_job(r, registry=registry, prefs=prefs)
        for r in pool[:limit]
    ]
    return RecommendationPage(
        items=items,
        basis="user_preferences",
        method=(
            "Filtered by your preference categories, region and experience "
            "settings, then ordered by system ranking score. Deterministic "
            "rules — not an AI language model."
        ),
        preferences=PreferencesOut(**prefs),
    )
