"""Meta endpoints: stats, engine status, sources, categories."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api import services
from api.deps import enforce_rate_limit, require_system_token
from api.schemas import CategoryStat, SourceStat, StatsResponse, StatusResponse

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/stats", response_model=StatsResponse)
def stats(request: Request) -> StatsResponse:
    """Truthful aggregate statistics from the discovery database."""
    enforce_rate_limit(request, "stats", limit=120, window=60)
    return StatsResponse(**services.get_stats())


@router.get("/status", response_model=StatusResponse)
def status_endpoint(request: Request) -> StatusResponse:
    """Real discovery-engine status (no simulated live activity)."""
    enforce_rate_limit(request, "status", limit=120, window=60)
    return StatusResponse(**services.get_status())


@router.get("/sources", response_model=list[SourceStat])
def sources(request: Request) -> list[SourceStat]:
    """Monitored sources with real job counts (no endorsement implied)."""
    enforce_rate_limit(request, "sources", limit=120, window=60)
    return [SourceStat(**s) for s in services.get_sources()]


@router.get("/categories", response_model=list[CategoryStat])
def categories(request: Request) -> list[CategoryStat]:
    """Tag frequencies computed from real stored jobs."""
    enforce_rate_limit(request, "categories", limit=120, window=60)
    return [CategoryStat(**c) for c in services.get_categories()]
