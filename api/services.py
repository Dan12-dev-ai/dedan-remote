"""Service layer: job queries, serialization, intelligence, stats.

Reads the discovery engine's SQLite database read-only. Never writes to it.
"""

from __future__ import annotations

import copy
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Sequence, TypeVar, Union

from agents.ranking_agent import RankingAgent
from config.settings import get_settings

T = TypeVar("T")

# ── Read-only aggregate cache ────────────────────────────────────────────────
# /api/stats, /api/status, /api/sources and /api/categories each scan the whole
# jobs table. Their content only changes when the discovery engine finishes a
# cycle, so a short TTL cache removes redundant full-table work under load
# without ever inventing data — every cached value came from the database.
#
# Invariants:
#   * results are deep-copied in and out, so callers can never corrupt a cache
#     entry (route handlers build response models from the returned objects);
#   * the cache is keyed by name and invalidated wholesale;
#   * DEDAN_CACHE_TTL_SECONDS <= 0 disables caching entirely.
_MISS: Any = object()


class TTLCache:
    """Small thread-safe TTL cache for read-only derived results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        """Return the cached value, or `_MISS` when absent/expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _MISS
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return _MISS
            return copy.deepcopy(value)

    def set(self, key: str, value: Any, ttl: float) -> None:
        """Store a defensive copy of `value` for `ttl` seconds."""
        with self._lock:
            self._entries[key] = (time.monotonic() + ttl, copy.deepcopy(value))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_AGGREGATE_CACHE = TTLCache()


def _cache_ttl_seconds() -> float:
    """Configured aggregate cache TTL, falling back to a safe default."""
    try:
        return float(get_settings().DEDAN_CACHE_TTL_SECONDS)
    except Exception:
        return 60.0


def invalidate_caches() -> None:
    """
    Drop every cached aggregate.

    Call after the discovery engine writes a completed cycle (or from tests)
    so the next read reflects the new data instead of waiting out the TTL.
    """
    _AGGREGATE_CACHE.clear()


def _cached(key: str, producer: Callable[[], T]) -> T:
    """Return the cached result of `producer`, computing it on a miss."""
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        return producer()
    hit = _AGGREGATE_CACHE.get(key)
    if hit is not _MISS:
        return hit
    value = producer()
    _AGGREGATE_CACHE.set(key, value, ttl)
    return value


# Freshness threshold (days) beyond which an opportunity is flagged stale.
STALE_AFTER_DAYS = 30

# Canonical category taxonomy — maps UI categories to real tag/title keywords.
# Used ONLY to filter/sort real text fields; never invents data.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "ai-ml": ["ai", "ml", "machine learning", "llm", "rlhf", "gpt", "prompt", "nlp", "model"],
    "software": [
        "software",
        "engineer",
        "developer",
        "programming",
        "python",
        "javascript",
        "full stack",
        "backend",
        "frontend",
        "devops",
    ],
    "data": [
        "data",
        "annotation",
        "labeling",
        "labelling",
        "tagging",
        "categorization",
        "classification",
        "transcription",
        "dataset",
    ],
    "ai-training": [
        "training",
        "evaluation",
        "evaluator",
        "rating",
        "reviewer",
        "ai training",
        "quality",
    ],
    "research": ["research", "scientist", "analysis", "analyst"],
    "general-remote": ["remote", "freelance", "contract", "part-time", "worldwide", "virtual"],
}

REGION_KEYWORDS: dict[str, list[str]] = {
    "worldwide": ["worldwide", "global", "anywhere", "remote"],
    "africa": ["africa", "nigeria", "kenya", "ethiopia", "ghana", "south africa", "egypt"],
    "europe": ["europe", "uk", "united kingdom", "germany", "france", "spain", "netherlands"],
    "north-america": ["usa", "united states", "canada", "north america"],
    "latin-america": ["latin america", "brazil", "mexico", "argentina"],
    "asia": ["asia", "india", "japan", "singapore", "philippines"],
    "middle-east": ["middle east", "uae", "saudi", "israel"],
}

BEGINNER_KEYWORDS = [
    "no experience",
    "no experience required",
    "beginner",
    "entry level",
    "entry-level",
    "no degree",
    "no prior",
    "training provided",
    "welcome to apply",
    "open to all",
    "start immediately",
    "any background",
]

AI_KEYWORDS = [
    "ai",
    "ml",
    "machine learning",
    "llm",
    "rlhf",
    "gpt",
    "annotation",
    "labeling",
    "labelling",
    "prompt",
    "model",
    "nlp",
    "evaluator",
    "chatbot",
    "training data",
]

# Human labels for ranking dimensions (must match RankingAgent keys).
DIMENSION_LABELS: dict[str, tuple[str, str]] = {
    "remote": ("Remote-friendly", "This role is listed as remote work."),
    "worldwide": ("Open worldwide", "No single-country restriction detected in the listing."),
    "salary": ("Compensation listed", "A salary or pay rate is published in the source listing."),
    "beginner": ("Beginner-friendly", "Listing text suggests low or no prior-experience barriers."),
    "ai_related": ("AI-related work", "Title, tags or description reference AI/data work."),
    "english": ("English-language role", "Listing appears to be in English."),
    "simplicity": (
        "Low application barrier",
        "Signals like flexible/online/anywhere reduce friction.",
    ),
    "freshness": ("Recently posted", "The listing's posted date is recent relative to today."),
}


def slugify(text: str) -> str:
    """URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")[:60]


def job_slug(job_id: str, title: str) -> str:
    base = slugify(title) if title.strip() else "opportunity"
    return f"{base}-{job_id[:8]}"


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def freshness_info(row: dict[str, Any]) -> dict[str, Any]:
    """Compute honest freshness from discovered_at / posted_date."""
    now = datetime.now(timezone.utc)
    basis = _parse_dt(row.get("posted_date")) or _parse_dt(row.get("discovered_at"))
    age_days = None
    label = "Unknown"
    if basis:
        age_days = max(0, (now - basis).days)
        hours = (now - basis).total_seconds() / 3600
        if hours < 1:
            label = "Just discovered"
        elif hours < 24:
            label = f"{int(hours)}h ago"
        elif age_days <= 7:
            label = "Yesterday" if age_days == 1 else f"{age_days} days ago"
        else:
            label = f"{age_days} days ago"
    is_stale = age_days is not None and age_days > STALE_AFTER_DAYS
    return {
        "label": label,
        "discovered_at": row.get("discovered_at"),
        "posted_date": row.get("posted_date"),
        "age_days": age_days,
        "is_stale": is_stale,
    }


def _split_tags(row: dict[str, Any]) -> list[str]:
    tags = row.get("tags") or ""
    return [t.strip() for t in tags.split(",") if t.strip()]


def _classify_category(row: dict[str, Any]) -> Optional[str]:
    """First matching canonical category from tags + title text."""
    text = " ".join([row.get("title") or "", " ".join(_split_tags(row))]).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(k in text for k in keywords):
            return cat
    return None


def _matches_category(row: dict[str, Any], category: str) -> bool:
    keywords = CATEGORY_KEYWORDS.get(category)
    if not keywords:
        return category in _split_tags(row)
    text = " ".join(
        [row.get("title") or "", row.get("description") or "", " ".join(_split_tags(row))]
    ).lower()
    return any(k in text for k in keywords)


def is_ai_related(row: dict[str, Any]) -> bool:
    text = " ".join(
        [row.get("title") or "", row.get("description") or "", " ".join(_split_tags(row))]
    ).lower()
    return any(k in text for k in AI_KEYWORDS)


def is_beginner_friendly(row: dict[str, Any]) -> bool:
    text = " ".join(
        [row.get("title") or "", row.get("description") or "", " ".join(_split_tags(row))]
    ).lower()
    return any(k in text for k in BEGINNER_KEYWORDS)


def experience_hint(row: dict[str, Any]) -> Optional[str]:
    if is_beginner_friendly(row):
        return "beginner"
    text = " ".join(
        [row.get("title") or "", row.get("description") or "", " ".join(_split_tags(row))]
    ).lower()
    senior = ["senior", "lead", "phd", "5+ years", "10 years", "expert", "doctorate", "specialist"]
    if any(k in text for k in senior):
        return "advanced"
    return None


def location_label(row: dict[str, Any]) -> str:
    country = (row.get("country") or "").strip()
    if not country:
        return "Remote · Worldwide"
    if country.lower() in ("worldwide", "global", "remote", "anywhere"):
        return "Remote · Worldwide"
    if row.get("remote"):
        return f"Remote · {country}"
    return country


def matches_region(row: dict[str, Any], regions: list[str]) -> bool:
    if not regions:
        return True
    if "worldwide" in regions:
        return True
    text = " ".join(
        [row.get("country") or "", row.get("title") or "", " ".join(_split_tags(row))]
    ).lower()
    for region in regions:
        keywords = REGION_KEYWORDS.get(region, [region])
        if any(k in text for k in keywords):
            return True
    return False


# ── Discovery database (read-only) ────────────────────────────────────────────


def connect_jobs_db() -> sqlite3.Connection:
    """
    Open a short-lived **read-only** connection to the discovery SQLite DB.

    The API is a read layer over the engine's database. `PRAGMA query_only`
    makes any accidental write — today or in a future refactor — fail loudly
    instead of silently corrupting the discovery engine's data. `busy_timeout`
    keeps reads from erroring out while the scheduler holds a write lock.
    """
    settings = get_settings()
    conn = sqlite3.connect(settings.DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # The engine owns journal_mode. On an already-WAL database this is a
        # no-op; on a read-only filesystem it raises and is safe to ignore.
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.Error:
        pass
    conn.execute("PRAGMA query_only=ON;")
    conn.execute("PRAGMA busy_timeout=10000;")
    return conn


def probe_jobs_db() -> tuple[bool, str]:
    """
    Liveness/readiness probe for the discovery database.

    Returns (healthy, detail) and never raises, so /api/ready can report the
    truth about whether this instance can actually serve data.
    """
    try:
        conn = connect_jobs_db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            return True, f"{total} opportunities available"
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - depends on deployment
        return False, type(exc).__name__


def get_row(job_id: str) -> Optional[dict[str, Any]]:
    conn = connect_jobs_db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_rows_by_slug_or_id(ident: str) -> Optional[dict[str, Any]]:
    """Look up a job by full id or by slug suffix (slug ends with id[:8])."""
    row = get_row(ident)
    if row:
        return row
    suffix = ident.rsplit("-", 1)[-1] if "-" in ident else ident
    if len(suffix) < 6 or not re.fullmatch(r"[0-9a-f]+", suffix):
        return None
    conn = connect_jobs_db()
    try:
        rows = conn.execute("SELECT * FROM jobs WHERE id LIKE ?", (suffix + "%",)).fetchall()
        for r in rows:
            d = dict(r)
            if job_slug(d["id"], d.get("title") or "") == ident:
                return d
        return dict(rows[0]) if len(rows) == 1 else None
    finally:
        conn.close()


def source_registry() -> dict[str, dict[str, str]]:
    """
    Map source id -> {name, homepage} from the scraper registry.

    Cached: the registry is static metadata, but importing every scraper
    module is expensive enough to matter on a hot read path.
    """
    return _cached("source_registry", _load_source_registry)


def _load_source_registry() -> dict[str, dict[str, str]]:
    try:
        from scrapers.scraper_registry import get_registry

        registry = get_registry()
        out: dict[str, dict[str, str]] = {}
        for scraper in registry.get_all():
            out[scraper.source] = {
                "name": scraper.name,
                "homepage": scraper.base_url,
            }
        return out
    except Exception:
        return {}


# ── Query / search / filter / sort / paginate ────────────────────────────────


def query_jobs(
    *,
    q: Optional[str] = None,
    # `source` and `category` accept a repeated value, like `country`.
    source: Optional[Union[str, Sequence[str]]] = None,
    category: Optional[Union[str, Sequence[str]]] = None,
    tag: Optional[str] = None,
    country: Optional[Union[str, Sequence[str]]] = None,
    worldwide: Optional[bool] = None,
    remote_only: Optional[bool] = None,
    min_score: Optional[float] = None,
    max_age_days: Optional[int] = None,
    has_salary: Optional[bool] = None,
    beginner_only: Optional[bool] = None,
    ai_only: Optional[bool] = None,
    sort: str = "score",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """
    Search the jobs table with real SQL filters.

    Returns {items, total, page, page_size, pages, has_next, has_prev}.
    All filters map to real columns or real text (tags/title/description).
    """
    conn = connect_jobs_db()
    try:
        where: list[str] = []
        params: list[Any] = []

        if q:
            like = f"%{q.lower()}%"
            where.append(
                "(lower(title) LIKE ? OR lower(company) LIKE ?"
                " OR lower(COALESCE(description, '')) LIKE ?"
                " OR lower(COALESCE(tags, '')) LIKE ? OR lower(source) LIKE ?)"
            )
            params.extend([like, like, like, like, like])

        # `source` accepts one value or several (the explorer sends a repeated
        # param per selected source). Matching only the first value would make
        # a two-source selection silently drop the others: selecting TELUS and
        # OneForma returned just OneForma's 13 rows instead of the union's 14.
        # Each entry is an exact (case-insensitive) source id, OR-combined so
        # picking more sources widens the result set.
        if source:
            wanted = [source] if isinstance(source, str) else list(source)
            clauses = []
            for value in wanted:
                value = str(value).strip().lower()
                if not value:
                    continue
                clauses.append("lower(source) = lower(?)")
                params.append(value)
            if clauses:
                where.append("(" + " OR ".join(clauses) + ")")

        if tag:
            where.append("lower(COALESCE(tags, '')) LIKE ?")
            params.append(f"%{tag.lower()}%")

        if country:
            # `country` accepts one value or several (the explorer sends a
            # repeated query param per selected country). Each entry is a
            # substring match, combined with OR so picking three countries
            # widens the result set rather than narrowing it.
            wanted = [country] if isinstance(country, str) else list(country)
            clauses = []
            for value in wanted:
                value = value.strip()
                if not value:
                    continue
                clauses.append("lower(COALESCE(country, '')) LIKE ?")
                params.append(f"%{value.lower()}%")
            if clauses:
                where.append("(" + " OR ".join(clauses) + ")")

        if worldwide:
            where.append(
                "(country IS NULL OR lower(country) IN"
                " ('worldwide', 'global', 'remote', '', 'selected locations'))"
            )

        if remote_only:
            where.append("remote = 1")

        if min_score is not None:
            where.append("score >= ?")
            params.append(min_score)

        if max_age_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            where.append("COALESCE(NULLIF(posted_date, ''), discovered_at) >= ?")
            params.append(cutoff)

        if has_salary:
            where.append("salary IS NOT NULL AND trim(salary) != ''")

        def _text_match_clauses(keywords: list[str]) -> tuple[str, list[Any]]:
            """OR-clauses matching keywords against title/description/tags."""
            clauses: list[str] = []
            kw_params: list[Any] = []
            for kw in keywords:
                clauses.append(
                    "(lower(title) LIKE ?"
                    " OR lower(COALESCE(description, '')) LIKE ?"
                    " OR lower(COALESCE(tags, '')) LIKE ?)"
                )
                like_kw = f"%{kw}%"
                kw_params.extend([like_kw, like_kw, like_kw])
            return "(" + " OR ".join(clauses) + ")", kw_params

        if category:
            # Several categories OR together, so a broad selection widens the
            # feed the same way several sources do.
            wanted = [category] if isinstance(category, str) else list(category)
            clauses = []
            for value in wanted:
                value = str(value).strip().lower()
                if not value:
                    continue
                keywords = CATEGORY_KEYWORDS.get(value)
                if keywords:
                    clause, kw_params = _text_match_clauses(keywords)
                    clauses.append(clause)
                    params.extend(kw_params)
                else:
                    clauses.append("lower(COALESCE(tags, '')) LIKE ?")
                    params.append(f"%{value}%")
            if clauses:
                where.append("(" + " OR ".join(clauses) + ")")

        if beginner_only:
            clause, kw_params = _text_match_clauses(BEGINNER_KEYWORDS)
            where.append(clause)
            params.extend(kw_params)

        if ai_only:
            clause, kw_params = _text_match_clauses(AI_KEYWORDS)
            where.append(clause)
            params.extend(kw_params)

        base_sql = "FROM jobs"
        if where:
            base_sql += " WHERE " + " AND ".join(where)

        total = conn.execute(f"SELECT COUNT(*) {base_sql}", params).fetchone()[0]

        order = {
            "newest": "discovered_at DESC, id ASC",
            "freshness": "COALESCE(NULLIF(posted_date, ''), discovered_at) DESC, id ASC",
            "score": "score DESC, discovered_at DESC, id ASC",
            "salary": "(salary IS NULL OR trim(salary) = ''), score DESC, discovered_at DESC",
        }
        if sort == "best_match":
            if q:
                like = f"%{q.lower()}%"
                order_sql = (
                    "(CASE WHEN lower(title) LIKE ? THEN 3 ELSE 0 END +"
                    " CASE WHEN lower(company) LIKE ? THEN 2 ELSE 0 END +"
                    " CASE WHEN lower(COALESCE(tags, '')) LIKE ? THEN 2"
                    " ELSE 0 END) DESC, score DESC, discovered_at DESC"
                )
                order_params = [like, like, like]
            else:
                order_sql = order["score"]
                order_params = []
        else:
            order_sql = order.get(sort, order["score"])
            order_params = []

        offset = (max(1, page) - 1) * page_size
        rows = conn.execute(
            f"SELECT * {base_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*order_params, *params, page_size, offset],
        ).fetchall()
        items = [dict(r) for r in rows]

        pages = max(1, (total + page_size - 1) // page_size) if total else 0
        return {
            "items": items,
            "total": total,
            "page": max(1, page),
            "page_size": page_size,
            "pages": pages,
            "has_next": offset + len(items) < total,
            "has_prev": page > 1,
        }
    finally:
        conn.close()


# ── Serialization ────────────────────────────────────────────────────────────


def build_score_explanation(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Recompute RankingAgent dimensions for this row and build reasons."""
    try:
        from models.job import Job

        job = Job.from_dict(row)
        agent = RankingAgent()
        result = agent.explain(job)
        dims = result["dimensions"]
        assert isinstance(dims, dict)
        reasons = []
        # Reasonable thresholds: dimension must meaningfully contribute.
        for key, data in dims.items():
            if not isinstance(data, dict):
                continue
            if float(data.get("score", 0)) < 70:
                continue
            # Never claim freshness without an actual posted date —
            # RankingAgent defaults missing dates to a neutral 70.
            if key == "freshness" and not row.get("posted_date"):
                continue
            label, detail = DIMENSION_LABELS.get(key, (key, ""))
            reasons.append(
                {
                    "dimension": key,
                    "label": label,
                    "detail": detail,
                }
            )
        reasons.sort(
            key=lambda r: float(dims[r["dimension"]]["contribution"]),
            reverse=True,
        )
        total_score: Any = row.get("score") or result["total"]
        return {
            "basis": "system_ranking",
            "label": "System ranking",
            "total": float(total_score),
            "dimensions": dims,
            "reasons": reasons[:6],
        }
    except Exception:
        return None


def build_preference_match(row: dict[str, Any], prefs: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Deterministic, transparent preference match (not an AI claim).

    Formula: 60% system score + 20% category overlap + 20% region fit.
    Only present when the user has stored preferences.
    """
    categories = prefs.get("categories") or []
    regions = prefs.get("regions") or []
    experience = prefs.get("experience")
    if not categories and not regions and not experience:
        return None

    reasons: list[str] = []
    system_score = float(row.get("score") or 0)

    category_points = 0.0
    if categories:
        matched = [c for c in categories if _matches_category(row, c)]
        if matched:
            category_points = 100.0
            reasons.append("Matches your preferred categories: " + ", ".join(matched))
        else:
            reasons.append("No overlap with your preferred categories")
    else:
        category_points = 50.0  # neutral when unspecified

    region_points = 0.0
    if regions:
        if matches_region(row, regions):
            region_points = 100.0
            reasons.append("Available in your preferred work region")
        else:
            region_points = 20.0
            reasons.append("Region not confirmed for your preferred areas — check listing")
    else:
        region_points = 50.0

    if experience == "beginner":
        if is_beginner_friendly(row):
            reasons.append("Listing shows beginner-friendly signals")
        else:
            reasons.append("No explicit beginner-friendly signals found")

    score = round(0.6 * system_score + 0.2 * category_points + 0.2 * region_points, 1)
    return {
        "basis": "user_preferences",
        "label": "Preference match",
        "score": min(100.0, score),
        "reasons": reasons,
        "note": "Calculated from your saved preferences and the system score."
        " Not an AI prediction of hiring success.",
    }


def serialize_job(
    row: dict[str, Any],
    *,
    registry: Optional[dict[str, dict[str, str]]] = None,
    include_explanation: bool = False,
    prefs: Optional[dict[str, Any]] = None,
    saved_ids: Optional[set[str]] = None,
    app_status: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Turn a DB row into a JobSummary-compatible dict (real fields only)."""
    from api.security import apply_host, validate_external_url

    registry = registry if registry is not None else source_registry()
    src = registry.get(row.get("source") or "", {})
    valid, clean_url = validate_external_url(row.get("url"))
    salary = (row.get("salary") or "").strip() or None
    summary: dict[str, Any] = {
        "id": row["id"],
        "slug": job_slug(row["id"], row.get("title") or ""),
        "title": (row.get("title") or "").strip() or "Untitled opportunity",
        "company": row.get("company") or "Unknown",
        "source": row.get("source") or "unknown",
        "source_info": {
            "id": row.get("source") or "unknown",
            "name": src.get("name") or (row.get("source") or "unknown").title(),
            "monitored": True,
            "homepage": src.get("homepage"),
        },
        "url": row.get("url") or "",
        "apply_url": clean_url if valid else None,
        "apply_host": apply_host(clean_url) if valid else None,
        "salary": salary,
        "salary_disclosed": salary is not None,
        "country": row.get("country") or None,
        "location_label": location_label(row),
        "remote": bool(row.get("remote", 1)),
        "posted_date": row.get("posted_date") or None,
        "description": row.get("description") or None,
        "tags": _split_tags(row),
        "category": _classify_category(row),
        "is_ai_related": is_ai_related(row),
        "experience_hint": experience_hint(row),
        "discovered_at": row.get("discovered_at") or None,
        "freshness": freshness_info(row),
        "score": float(row.get("score") or 0),
        "score_explanation": None,
        "preference_match": None,
    }
    if include_explanation:
        summary["score_explanation"] = build_score_explanation(row)
    if prefs is not None:
        summary["preference_match"] = build_preference_match(row, prefs)
    return summary


# ── Stats / status / meta ────────────────────────────────────────────────────


def _relative_label(iso: Optional[str]) -> str:
    dt = _parse_dt(iso)
    if not dt:
        return "Never"
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    return f"{int(seconds // 86400)} days ago"


def get_stats() -> dict[str, Any]:
    """Truthful aggregate statistics from the discovery database (cached)."""
    return _cached("stats", _load_stats)


def _load_stats() -> dict[str, Any]:
    settings = get_settings()
    conn = connect_jobs_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        new_7 = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE discovered_at >= ?", (cutoff,)
        ).fetchone()[0]
        last_cycle = conn.execute(
            "SELECT completed_at, status FROM execution_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        total_cycles = conn.execute("SELECT COUNT(*) FROM execution_history").fetchone()[0]
        sources_count = conn.execute("SELECT COUNT(*) FROM website_status").fetchone()[0]
        if sources_count == 0:
            try:
                sources_count = len(source_registry())
            except Exception:
                sources_count = 0
        return {
            "total_opportunities": total,
            "new_last_7_days": new_7,
            "sources_monitored": sources_count,
            "discovery_interval_minutes": settings.CHECK_INTERVAL,
            "last_discovery_at": (last_cycle["completed_at"] if last_cycle else None),
            "last_cycle_status": last_cycle["status"] if last_cycle else None,
            "total_cycles": total_cycles,
        }
    finally:
        conn.close()


def get_status() -> dict[str, Any]:
    """Real discovery-engine status from execution_history + website_status."""
    return _cached("status", _load_status)


def _load_status() -> dict[str, Any]:
    conn = connect_jobs_db()
    try:
        last_cycle = conn.execute(
            "SELECT * FROM execution_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        total_cycles = conn.execute("SELECT COUNT(*) FROM execution_history").fetchone()[0]
        sources = conn.execute(
            "SELECT source, consecutive_failures, circuit_open, last_success FROM website_status"
        ).fetchall()
        failing = sum(1 for s in sources if (s["consecutive_failures"] or 0) > 0)
        monitored = len(sources)
        if total_cycles == 0:
            engine = "idle"
            note = "No discovery cycles have run yet."
        elif last_cycle and last_cycle["status"] == "completed" and failing == 0:
            engine = "operational"
            note = None
        elif last_cycle and last_cycle["status"] == "completed":
            engine = "operational"
            note = f"{failing} source(s) reporting recent failures."
        else:
            engine = "degraded"
            note = "Last discovery cycle did not complete successfully."
        completed_at = last_cycle["completed_at"] if last_cycle else None
        return {
            "engine": engine,
            "last_discovery_at": completed_at,
            "last_discovery_label": _relative_label(completed_at),
            "last_cycle_status": last_cycle["status"] if last_cycle else None,
            "sources_monitored": monitored,
            "sources_failing": failing,
            "total_cycles": total_cycles,
            "note": note,
        }
    finally:
        conn.close()


def get_sources() -> list[dict[str, Any]]:
    """Monitored sources: registry metadata + real per-source job counts."""
    return _cached("sources", _load_sources)


def _load_sources() -> list[dict[str, Any]]:
    registry = source_registry()
    conn = connect_jobs_db()
    try:
        counts = dict(
            conn.execute(
                "SELECT lower(source), COUNT(*) FROM jobs GROUP BY lower(source)"
            ).fetchall()
        )
        status_rows = conn.execute(
            "SELECT source, last_success, circuit_open FROM website_status"
        ).fetchall()
        status_map = {s["source"]: s for s in status_rows}
    finally:
        conn.close()

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for src_id, meta in registry.items():
        seen.add(src_id)
        st = status_map.get(src_id)
        result.append(
            {
                "id": src_id,
                "name": meta["name"],
                "job_count": int(counts.get(src_id, 0)),
                "monitored": True,
                "last_success": st["last_success"] if st else None,
                "circuit_open": bool(st["circuit_open"]) if st else False,
            }
        )
    for src_id, st in status_map.items():
        if src_id not in seen:
            result.append(
                {
                    "id": src_id,
                    "name": src_id.title(),
                    "job_count": int(counts.get(src_id, 0)),
                    "monitored": True,
                    "last_success": st["last_success"],
                    "circuit_open": bool(st["circuit_open"]),
                }
            )
    result.sort(key=lambda s: (-s["job_count"], s["name"]))
    return result


def get_categories() -> list[dict[str, Any]]:
    """Real tag frequencies from the jobs table (cached)."""
    return _cached("categories", _load_categories)


def _load_categories() -> list[dict[str, Any]]:
    conn = connect_jobs_db()
    try:
        rows = conn.execute("SELECT tags FROM jobs WHERE tags IS NOT NULL").fetchall()
    finally:
        conn.close()
    counts: dict[str, int] = {}
    for r in rows:
        for t in (r["tags"] or "").split(","):
            t = t.strip().lower()
            if t:
                counts[t] = counts.get(t, 0) + 1
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    ]


def get_job_intelligence(row: dict[str, Any]) -> tuple[Optional[dict], str]:
    """
    Run the existing Ethiopia-focused ComprehensiveScorer on one job.

    Clearly labeled as experimental and region-specific — NOT the system
    ranking used across discovery.
    """
    note = (
        "Experimental regional analysis (Ethiopia-focused model). "
        "Separate from the system ranking score; treat as an estimate, "
        "not a fact."
    )
    try:
        from intelligence.scorer import ComprehensiveScorer
        from models.job import Job

        job = Job.from_dict(row)
        result = ComprehensiveScorer().evaluate(job)
        return result.to_dict(), note
    except Exception:
        return None, note


# ── Command search ───────────────────────────────────────────────────────────

# Human labels for the canonical category taxonomy, so search suggestions
# read like product language rather than raw slugs.
CATEGORY_LABELS: dict[str, str] = {
    "ai-ml": "AI / ML",
    "software": "Software engineering",
    "data": "Data & annotation",
    "ai-training": "AI training",
    "research": "Research",
    "general-remote": "Remote digital work",
}

# Suggestion ceiling so the command palette never becomes a wall of links.
_MAX_SUGGESTIONS = 8


def search_all(query: str, limit: int = 6) -> dict[str, Any]:
    """
    Grouped search over *stored* data only.

    Returns matching opportunities (via the same SQL path the explorer uses),
    plus real source and category matches and a small set of runnable
    refinements. When a query is empty, the response is empty too — the UI
    decides what to show before the user has typed anything.

    Nothing in this function invents a result: suggestions are drawn from tag
    frequencies, the source registry and the filter taxonomy.
    """
    from api.schemas import SearchSuggestion

    term = (query or "").strip()
    if not term:
        return {
            "query": "",
            "total": 0,
            "opportunities": [],
            "categories": [],
            "sources": [],
            "suggestions": [],
            "note": "Type to search opportunities, sources and categories.",
        }

    pool = _cached(
        "search:" + term.lower(),
        lambda: query_jobs(q=term, sort="best_match", page=1, page_size=limit),
    )
    registry = source_registry()
    opportunities = [serialize_job(row, registry=registry) for row in pool["items"]]

    lowered = term.lower()
    categories = [c for c in get_categories() if lowered in c["tag"]][:limit]
    sources = [s for s in get_sources() if lowered in s["name"].lower() or lowered in s["id"]][
        :limit
    ]

    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(label: str, kind: str, count: Optional[int] = None) -> None:
        key = f"{kind}:{label.lower()}"
        if key in seen or len(suggestions) >= _MAX_SUGGESTIONS:
            return
        seen.add(key)
        suggestions.append({"label": label, "kind": kind, "count": count})

    # 1. Real tags containing the term (highest-signal refinement).
    for tag in get_categories():
        if lowered in tag["tag"]:
            push(tag["tag"], "tag", tag["count"])

    # 2. Canonical categories whose label or keywords match.
    for slug, label in CATEGORY_LABELS.items():
        keywords = CATEGORY_KEYWORDS.get(slug, [])
        if lowered in label.lower() or any(lowered in k for k in keywords):
            push(slug, "category")

    # 3. Monitored sources whose name matches.
    for src in get_sources():
        if lowered in src["name"].lower() or lowered in src["id"]:
            push(src["id"], "source", src["job_count"])

    # 4. If nothing refined the search, offer the real highest-frequency tags
    #    so the palette always gives the user a next step.
    if not suggestions:
        for tag in get_categories()[:4]:
            push(tag["tag"], "tag", tag["count"])

    return {
        "query": term,
        "total": int(pool["total"]),
        "opportunities": opportunities,
        "categories": categories,
        "sources": sources,
        "suggestions": [SearchSuggestion(**s) for s in suggestions],
        "note": (
            "Results are matched against stored opportunity listings, the "
            "source registry and the category taxonomy. Deterministic text "
            "matching — not a language model."
        ),
    }
