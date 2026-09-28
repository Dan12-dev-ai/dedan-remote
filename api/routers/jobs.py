"""Public job endpoints: list/search, detail, save/unsave, saved list."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status

from api import services
from api.deps import (
    ApiError,
    enforce_rate_limit,
    get_current_user,
    get_current_user_optional,
)
from api.schemas import (
    JobDetail,
    JobSummary,
    Page,
    SavedItem,
    SaveRequest,
    SortOption,
)
from api.store import get_user_store

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs", response_model=Page)
def list_jobs(
    request: Request,
    q: Optional[str] = Query(default=None, max_length=200, description="Keyword search"),
    source: Optional[str] = Query(default=None, max_length=50),
    category: Optional[str] = Query(default=None, max_length=50),
    tag: Optional[str] = Query(default=None, max_length=50),
    country: Optional[str] = Query(default=None, max_length=100),
    worldwide: Optional[bool] = Query(default=None),
    remote_only: Optional[bool] = Query(default=None, alias="remote"),
    min_score: Optional[float] = Query(default=None, ge=0, le=100),
    max_age_days: Optional[int] = Query(default=None, ge=0, le=3650),
    has_salary: Optional[bool] = Query(default=None),
    beginner_only: Optional[bool] = Query(default=None, alias="beginner"),
    ai_only: Optional[bool] = Query(default=None, alias="ai"),
    sort: SortOption = Query(default="score"),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=20, ge=1, le=50),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> Page:
    """
    Search & filter opportunities.

    Every filter maps to a real SQL condition over stored fields.
    ``sort=best_match`` ranks title/company/tag hits above score when a
    query is present; otherwise it equals ``sort=score`` (system ranking).
    """
    enforce_rate_limit(request, "jobs", limit=240, window=60)
    result = services.query_jobs(
        q=q,
        source=source,
        category=category,
        tag=tag,
        country=country,
        worldwide=worldwide,
        remote_only=remote_only,
        min_score=min_score,
        max_age_days=max_age_days,
        has_salary=has_salary,
        beginner_only=beginner_only,
        ai_only=ai_only,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    registry = services.source_registry()

    prefs = None
    if user:
        store = get_user_store()
        prefs = store.get_preferences(user["id"])

    # serialize_job returns a JobSummary-shaped dict; validate it into the
    # declared model so the response payload and the type contract agree.
    items = [
        JobSummary.model_validate(services.serialize_job(row, registry=registry, prefs=prefs))
        for row in result["items"]
    ]
    return Page(
        items=items,
        page=result["page"],
        page_size=result["page_size"],
        total=result["total"],
        pages=result["pages"],
        has_next=result["has_next"],
        has_prev=result["has_prev"],
    )


def _get_job_or_404(ident: str) -> dict:
    row = services.find_rows_by_slug_or_id(ident)
    if not row:
        raise ApiError(404, "not_found", "Opportunity not found.")
    return row


@router.get("/jobs/{ident}", response_model=JobDetail)
def job_detail(
    request: Request,
    ident: str,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> JobDetail:
    """Full opportunity detail with ranking breakdown + intelligence."""
    enforce_rate_limit(request, "job_detail", limit=300, window=60)
    row = _get_job_or_404(ident)
    registry = services.source_registry()
    prefs = None
    if user:
        store = get_user_store()
        prefs = store.get_preferences(user["id"])

    base = services.serialize_job(
        row,
        registry=registry,
        include_explanation=True,
        prefs=prefs,
    )
    intelligence, note = services.get_job_intelligence(row)

    # Source trust metadata from website_status.
    source_checked = None
    conn = services.connect_jobs_db()
    try:
        st = conn.execute(
            "SELECT last_success FROM website_status WHERE source = ?",
            (row.get("source"),),
        ).fetchone()
        if st:
            source_checked = st["last_success"]
    finally:
        conn.close()

    base.update(
        {
            "intelligence": intelligence,
            "intelligence_note": note,
            "source_checked_at": source_checked,
        }
    )
    return JobDetail(**base)


@router.post("/jobs/{ident}/save", status_code=status.HTTP_201_CREATED)
def save_job(
    request: Request,
    ident: str,
    payload: Optional[SaveRequest] = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Save an opportunity to the signed-in user's list."""
    enforce_rate_limit(request, "save", limit=60, window=60)
    row = _get_job_or_404(ident)
    store = get_user_store()
    saved_at = store.save_job(
        user["id"],
        row["id"],
        payload.note if payload else None,
    )
    return {"job_id": row["id"], "saved": True, "saved_at": saved_at}


@router.delete("/jobs/{ident}/save", status_code=status.HTTP_200_OK)
def unsave_job(
    request: Request,
    ident: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Remove an opportunity from the saved list."""
    enforce_rate_limit(request, "save", limit=60, window=60)
    row = _get_job_or_404(ident)
    store = get_user_store()
    removed = store.unsave_job(user["id"], row["id"])
    if not removed:
        raise ApiError(404, "not_saved", "Opportunity was not saved.")
    return {"job_id": row["id"], "saved": False}


@router.get("/saved", response_model=list[SavedItem])
def list_saved(
    request: Request,
    user: dict = Depends(get_current_user),
) -> list[SavedItem]:
    """All saved opportunities for the signed-in user."""
    enforce_rate_limit(request, "saved", limit=120, window=60)
    store = get_user_store()
    entries = store.list_saved(user["id"])
    items: list[SavedItem] = []
    registry = services.source_registry()
    prefs = store.get_preferences(user["id"])
    for entry in entries:
        row = services.get_row(entry["job_id"])
        if not row:
            continue  # job removed from discovery DB — skip honestly
        items.append(
            SavedItem(
                job=JobSummary.model_validate(
                    services.serialize_job(row, registry=registry, prefs=prefs)
                ),
                note=entry["note"],
                saved_at=entry["saved_at"],
            )
        )
    return items
