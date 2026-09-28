"""Command search and the in-product activity feed."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from api import activity, services
from api.deps import enforce_rate_limit, get_current_user
from api.schemas import (
    CategoryStat,
    NotificationOut,
    NotificationsResponse,
    SearchResponse,
    SourceStat,
)

router = APIRouter(prefix="/api", tags=["discovery"])


@router.get("/search", response_model=SearchResponse)
def command_search(
    request: Request,
    q: str = Query(default="", max_length=200, description="Search term"),
    limit: int = Query(default=6, ge=1, le=20, description="Opportunities returned per query"),
) -> SearchResponse:
    """
    Grouped search for the command interface.

    Returns real opportunity matches plus source and category matches, and a
    handful of runnable refinements. An empty query returns an empty payload
    rather than a padded one.
    """
    enforce_rate_limit(request, "search", limit=180, window=60)
    result = services.search_all(q, limit=limit)
    return SearchResponse(
        query=result["query"],
        total=result["total"],
        opportunities=result["opportunities"],
        categories=[CategoryStat(**c) for c in result["categories"]],
        sources=[SourceStat(**s) for s in result["sources"]],
        suggestions=result["suggestions"],
        note=result["note"],
    )


@router.get("/notifications", response_model=NotificationsResponse)
def notifications(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    user: dict = Depends(get_current_user),
) -> NotificationsResponse:
    """
    Activity feed for the signed-in user.

    Each item maps to a stored timestamp (save, application update, or a real
    discovery time on a matching listing). No synthetic events are produced.
    """
    enforce_rate_limit(request, "notifications", limit=120, window=60)
    items = activity.build_notifications(user["id"], limit=limit)
    return NotificationsResponse(
        items=[NotificationOut(**item) for item in items],
        note=activity.note_for(items),
    )
