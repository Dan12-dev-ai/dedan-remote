"""Pydantic request/response schemas for the DEDAN Remote API."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── Enums ────────────────────────────────────────────────────────────────────

ApplicationStatus = Literal[
    "viewed",
    "saved",
    "application_started",
    "applied",
    "rejected",
    "interview",
    "offer",
]

SortOption = Literal["newest", "best_match", "score", "salary", "freshness"]

ExperienceLevel = Literal["beginner", "intermediate", "advanced"]


# ── Job responses ────────────────────────────────────────────────────────────


class ScoreDimension(BaseModel):
    """One ranking dimension with its score, weight and contribution."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(description="Dimension score 0-100")
    weight: float = Field(description="Configured weight (sums to 1.0)")
    contribution: float = Field(description="score * weight")


class MatchReason(BaseModel):
    """A human-readable reason the system ranked this opportunity highly."""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    label: str
    detail: str


class ScoreExplanation(BaseModel):
    """System ranking breakdown — produced by RankingAgent (always active)."""

    model_config = ConfigDict(extra="forbid")

    basis: Literal["system_ranking"] = "system_ranking"
    label: str
    total: float = Field(description="Same value as the job's score field")
    dimensions: dict[str, ScoreDimension]
    reasons: list[MatchReason]


class PreferenceMatch(BaseModel):
    """User-specific match computed from stored preferences (when signed in)."""

    model_config = ConfigDict(extra="forbid")

    basis: Literal["user_preferences"] = "user_preferences"
    label: str
    score: float = Field(ge=0, le=100)
    reasons: list[str]
    note: str


class Freshness(BaseModel):
    """Honest freshness derived from stored timestamps only."""

    model_config = ConfigDict(extra="forbid")

    label: str
    discovered_at: Optional[str] = None
    posted_date: Optional[str] = None
    age_days: Optional[int] = None
    is_stale: bool = False


class SourceInfo(BaseModel):
    """Provenance of an opportunity (from scraper registry / DB)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    monitored: bool = True
    homepage: Optional[str] = None


class JobSummary(BaseModel):
    """Opportunity as shown in lists/cards. Only real backend fields."""

    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    title: str
    company: str
    source: str
    source_info: SourceInfo
    url: str
    apply_url: Optional[str] = Field(
        default=None, description="Validated external URL, null if blocked"
    )
    apply_host: Optional[str] = None
    salary: Optional[str] = None
    salary_disclosed: bool
    country: Optional[str] = None
    location_label: str
    remote: bool
    posted_date: Optional[str] = None
    description: Optional[str] = None
    tags: list[str]
    category: Optional[str] = None
    is_ai_related: bool
    experience_hint: Optional[str] = None
    discovered_at: Optional[str] = None
    freshness: Freshness
    score: float
    score_explanation: Optional[ScoreExplanation] = None
    preference_match: Optional[PreferenceMatch] = None


class JobDetail(JobSummary):
    """Full opportunity detail for the /opportunities/:id page."""

    model_config = ConfigDict(extra="forbid")

    intelligence: Optional[dict] = Field(
        default=None,
        description=(
            "Experimental Ethiopia-focused analysis from intelligence/ "
            "(not part of system ranking). Null if unavailable."
        ),
    )
    intelligence_note: Optional[str] = None
    source_checked_at: Optional[str] = None


# ── Pagination ───────────────────────────────────────────────────────────────


class Page(BaseModel):
    """Page-based pagination envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[JobSummary]
    page: int
    page_size: int
    total: int
    pages: int
    has_next: bool
    has_prev: bool


# ── Meta ─────────────────────────────────────────────────────────────────────


class SourceStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    job_count: int
    monitored: bool
    last_success: Optional[str] = None
    circuit_open: bool = False


class CategoryStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    count: int


class StatsResponse(BaseModel):
    """Truthful statistics aggregated from the SQLite store."""

    model_config = ConfigDict(extra="forbid")

    total_opportunities: int
    new_last_7_days: int
    sources_monitored: int
    discovery_interval_minutes: int
    last_discovery_at: Optional[str] = None
    last_cycle_status: Optional[str] = None
    total_cycles: int = 0


class StatusResponse(BaseModel):
    """Discovery engine status — only real facts from execution_history."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["operational", "degraded", "idle"]
    last_discovery_at: Optional[str] = None
    last_discovery_label: str
    last_cycle_status: Optional[str] = None
    sources_monitored: int
    sources_failing: int = 0
    total_cycles: int = 0
    note: Optional[str] = None


# ── Auth ─────────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=80)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: EmailStr
    display_name: Optional[str] = None
    created_at: str


class AuthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


# ── Saved / Applications ─────────────────────────────────────────────────────


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = Field(default=None, max_length=2000)


class SavedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobSummary
    note: Optional[str] = None
    saved_at: str


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=64)
    status: ApplicationStatus = "application_started"
    note: Optional[str] = Field(default=None, max_length=4000)


class ApplicationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[ApplicationStatus] = None
    note: Optional[str] = Field(default=None, max_length=4000)


class ApplicationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    job: JobSummary
    status: ApplicationStatus
    note: Optional[str] = None
    created_at: str
    updated_at: str


# ── Preferences / profile ────────────────────────────────────────────────────


class PreferencesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[str] = Field(default_factory=list)
    experience: Optional[ExperienceLevel] = None
    regions: list[str] = Field(default_factory=list)
    remote_only: bool = False
    beginner_friendly: bool = False
    updated_at: Optional[str] = None


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = Field(default=None, max_length=80)
    categories: Optional[list[str]] = Field(default=None, max_length=20)
    experience: Optional[ExperienceLevel] = None
    regions: Optional[list[str]] = Field(default=None, max_length=20)
    remote_only: Optional[bool] = None
    beginner_friendly: Optional[bool] = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserOut
    preferences: PreferencesOut


class RecommendationPage(BaseModel):
    """Preference-ranked opportunities. Explicitly labeled as calculated."""

    model_config = ConfigDict(extra="forbid")

    items: list[JobSummary]
    basis: str
    method: str
    preferences: PreferencesOut


# ── Command search ───────────────────────────────────────────────────────────


class SearchSuggestion(BaseModel):
    """A runnable search refinement derived from real stored data."""

    model_config = ConfigDict(extra="forbid")

    label: str
    kind: Literal["tag", "source", "category", "keyword"]
    count: Optional[int] = Field(
        default=None, description="Real match count when the kind supports it"
    )


class SearchResponse(BaseModel):
    """
    Grouped command-search result.

    Every group is computed from stored rows, the source registry and the
    filter taxonomy — nothing here is generated or predicted.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    total: int = Field(description="Real count of opportunities matching the query")
    opportunities: list[JobSummary] = Field(default_factory=list)
    categories: list[CategoryStat] = Field(default_factory=list)
    sources: list[SourceStat] = Field(default_factory=list)
    suggestions: list[SearchSuggestion] = Field(default_factory=list)
    note: str


# ── Activity signals ─────────────────────────────────────────────────────────


class NotificationOut(BaseModel):
    """
    One event derived from a stored timestamp.

    `kind` states where the event came from so the UI never implies more
    activity than actually happened.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["saved", "application", "discovery"]
    title: str
    detail: str
    at: str
    job: Optional[JobSummary] = None


class NotificationsResponse(BaseModel):
    """Reverse-chronological activity feed for the signed-in user."""

    model_config = ConfigDict(extra="forbid")

    items: list[NotificationOut]
    note: str


# ── AI Interview System ─────────────────────────────────────────────────────
# Response contracts for the live interview rooms (see api/routers/interview.py).
# The interview room returns rich nested payloads (role/interview/progress/
# privacy blocks); these models pin the fields the client and tests rely on and
# allow extra keys so the domain can evolve without a schema change here.

InterviewTypeValue = Literal[
    "role_rehearsal",
    "technical",
    "behavioral",
    "system_design",
    "ai_engineering",
    "coding",
    "scenario",
    "general_rehearsal",
]


class RoomAvailability(BaseModel):
    """Whether an AI interview can run on this deployment, and its shape.

    Public and secret-free: capability and shape, never the model name,
    provider or any credential.
    """

    model_config = ConfigDict(extra="allow")

    live: bool
    mode: str
    detail: str = ""
    default_questions: int
    interview_types: list[str]
    privacy: dict[str, object]


class RoomSessionOut(BaseModel):
    """The public state of one interview room (the resume path payload)."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    state: str
    phase: str
    interviewer_state: str
    interviewer: dict[str, object]
    opportunity_id: Optional[str] = None
    mode: Optional[str] = None
    interview_type: str
    error: Optional[str] = None
    role: dict[str, object]
    interview: dict[str, object]
    progress: dict[str, object]
    introduction: str = ""
    current: Optional[dict[str, object]] = None
    report_available: bool = False
    outcome: Optional[str] = None
    privacy: dict[str, object]
    elapsed_seconds: float = 0.0


class RoomHistoryEntry(BaseModel):
    """One row in the candidate's interview history. No plan, turns or report."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    opportunity_id: Optional[str] = None
    role_title: Optional[str] = None
    role_company: Optional[str] = None
    state: Optional[str] = None
    outcome: Optional[str] = None
    interview_type: Optional[str] = None
    turns_asked: int = 0
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    resumable: bool = False


class RoomRecoverableOut(BaseModel):
    """A session a reload or crash left mid-flight."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    opportunity_id: Optional[str] = None
    role_title: Optional[str] = None
    role_company: Optional[str] = None
    state: Optional[str] = None
    turns_answered: int = 0
    turns_asked: int = 0
    created_at: Optional[str] = None
    resumes_at: str
    completed: bool = False


class RoomTranscriptOut(BaseModel):
    """The chronological transcript, without scores or internal notes."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    role: dict[str, object]
    state: str
    interviewer: str = ""
    entries: list[dict[str, object]]
    turns_asked: int = 0
    turns_answered: int = 0
    completed: bool = False
    audio_note: str = ""


class RoomStreamTicketOut(BaseModel):
    """A one-use, short-lived ticket for the SSE stream (EventSource auth)."""

    model_config = ConfigDict(extra="allow")

    ticket: str
    session_id: str
    expires_in: int


class RoomDiscardOut(BaseModel):
    """Acknowledgement that an interview and its data were deleted."""

    model_config = ConfigDict(extra="allow")

    status: str
