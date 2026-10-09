"""
Skill-grounded mock interview and evaluation endpoints.

    GET  /api/interview/skills?slug=…      the role's skill vector (pre-flight)
    POST /api/interview/mocks              open a durable interview + plan
    POST /api/interview/mocks/{id}/answers submit an answer (queued for evaluation)
    GET  /api/interview/mocks/{id}/stream  SSE: progress, then the report
    GET  /api/interview/mocks/{id}         full transcript + report
    GET  /api/interview/mocks              the caller's history
    POST /api/interview/mocks/{id}/finish  force evaluation + completion
    DELETE /api/interview/mocks/{id}       delete the record
    PUT  /api/interview/alerts             set the skill watch / email opt-in
    GET  /api/interview/alerts             read it back

Two things are deliberately different from the live `/sessions` endpoints:

- **This is durable.** Everything lands in `interview_questions` /
  `interview_responses`, so a report survives a restart and can be exported.
- **Evaluation is asynchronous.** Submitting an answer returns immediately with
  `status: "queued"`; the rubric is computed off the request path and pushed over
  SSE. A free-tier model can take 20s per answer, and holding an HTTP request
  open for six sequential evaluations is exactly how gateways return 504.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api import services
from api.deps import (
    ApiError,
    enforce_rate_limit,
    get_current_user,
    get_current_user_optional,
)
from config.settings import get_settings
from interview import evaluator as evaluator_module
from interview.persistence import (
    STATUS_ABANDONED,
    STATUS_ACTIVE,
    STATUS_AWAITING_EVALUATION,
    STATUS_COMPLETED,
    STATUS_FAILED,
    get_repository,
)
from interview.planner import plan_interview
from interview.provider import get_llm
from interview.report_pdf import build_interview_report_pdf, safe_filename

router = APIRouter(prefix="/api/interview", tags=["interview-mock"])

MOCKS_PER_HOUR = 12
ANSWERS_PER_MINUTE = 30
# PDF generation is cheap but not free (it re-reads the whole transcript),
# and a loop of downloads is either a bug or abuse. Same cap the live room
# uses for reports, so both surfaces behave alike.
REPORT_PER_MINUTE = 30

# How long the SSE stream waits for the last evaluation before giving up and
# telling the client to poll instead. Never longer than a typical proxy idle
# timeout, so the stream closes cleanly rather than being severed.
STREAM_BUDGET_SECONDS = 120.0
STREAM_POLL_SECONDS = 0.35


# ── request bodies ──────────────────────────────────────────────────────────


class MockStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=2, max_length=200)
    min_questions: Optional[int] = Field(default=None, ge=3, le=10)
    max_questions: Optional[int] = Field(default=None, ge=3, le=10)
    mode: str = Field(default="live", pattern="^(live|rehearsal)$")


class MockAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=8, max_length=64)
    response_text: str = Field(default="", max_length=20_000)
    input_mode: str = Field(default="text", pattern="^(text|voice)$")
    duration_seconds: float = Field(default=0.0, ge=0.0, le=7_200.0)
    timed_out: bool = False
    #: Browser-recorded audio. There is no media store in this deployment, so
    #: this is stored as a reference and the UI says it was not retained.
    audio_url: Optional[str] = Field(default=None, max_length=500)


class SkillAlertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_skills: list[str] = Field(default_factory=list, max_length=25)
    email_enabled: bool = False
    email_address: Optional[str] = Field(default=None, max_length=254)
    min_overlap: float = Field(default=0.5, ge=0.0, le=1.0)


# ── helpers ─────────────────────────────────────────────────────────────────


def _user_id(user: Optional[dict[str, Any]]) -> Optional[str]:
    return (user or {}).get("id")


def _require_user(user: Optional[dict[str, Any]]) -> str:
    user_id = _user_id(user)
    if not user_id:
        raise ApiError(401, "auth_required", "Sign in to run a mock interview.")
    return user_id


def _load_job(slug: str) -> dict[str, Any]:
    row = services.find_rows_by_slug_or_id(slug)
    if not row:
        raise ApiError(404, "not_found", "Opportunity not found.")
    return services.serialize_job(
        row, registry=services.source_registry(), include_explanation=True
    )


def _owned(interview_id: str, user_id: str) -> dict[str, Any]:
    """Fetch an interview, enforcing ownership.

    Ownership is checked on every read. A 404 rather than a 403 is deliberate:
    a 403 confirms the id exists, which leaks that another user has an interview.
    """
    record = get_repository().get_interview(interview_id)
    if record is None or record["user_id"] != user_id:
        raise ApiError(404, "not_found", "Interview not found.")
    return record


def _sse(event: str, data: Any) -> bytes:
    """One SSE frame. ``event`` gives the browser a typed listener."""
    body = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {body}\n\n".encode()


# ── pre-flight ──────────────────────────────────────────────────────────────


@router.get("/skills")
def role_skills(
    request: Request,
    slug: str = Query(min_length=2, max_length=200),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """
    The role's skill vector, before committing to an interview.

    The proctoring modal shows this so a candidate knows what will be asked.
    Returning it separately means the setup screen can render in parallel with
    device permissions instead of after them.
    """
    enforce_rate_limit(request, "interview_skills", limit=120, window=60)
    job = _load_job(slug)
    plan_preview = plan_interview(job, client=None)
    return {
        "job": {
            "title": job.get("title"),
            "company": job.get("company"),
            "location_label": job.get("location_label"),
            "experience_hint": job.get("experience_hint"),
        },
        "skills": plan_preview.skills.to_dict(),
        "skillless": plan_preview.skillless,
        "estimated_questions": len(plan_preview.questions),
        "origin": plan_preview.origin,
        "interviewer_available": get_llm().available,
        "unavailable_reason": get_llm().unavailable_reason,
    }


# ── lifecycle ───────────────────────────────────────────────────────────────


@router.post("/mocks", status_code=status.HTTP_201_CREATED)
def start_mock(
    request: Request,
    payload: MockStartRequest,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """
    Open a durable interview and persist its question set.

    The whole set is stored, unlike the live session flow, because the export
    needs it and because a report must be reconstructible after the fact.
    """
    user_id = _require_user(user)
    enforce_rate_limit(request, "interview_mock_start", limit=MOCKS_PER_HOUR, window=3600)

    job = _load_job(payload.slug)
    settings = get_settings()
    plan = plan_interview(
        job,
        client=get_llm(),
        min_questions=payload.min_questions or settings.INTERVIEW_MIN_QUESTIONS,
        max_questions=payload.max_questions or settings.INTERVIEW_MAX_QUESTIONS_SKILLED,
    )

    repo = get_repository()
    interview_id = repo.create_interview(
        user_id=user_id,
        job_id=payload.slug,
        target_skills=plan.skills.primary,
        job_title=job.get("title"),
        job_company=job.get("company"),
        mode=payload.mode,
    )
    repo.save_questions(interview_id, [q.to_dict() for q in plan.questions])

    questions = repo.list_questions(interview_id)
    return {
        "interview_id": interview_id,
        "status": repo.get_interview(interview_id)["status"],
        "origin": plan.origin,
        "model": plan.model,
        "skillless": plan.skillless,
        "composition": plan.composition(),
        "skills": plan.skills.to_dict(),
        # The first question only; the rest stay server-side.
        "question": questions[0] if questions else None,
        "question_count": len(questions),
        "note": (
            "Questions were composed from a template."
            if plan.origin == "template"
            else "Questions were generated by the configured model."
        ),
    }


@router.post("/mocks/{interview_id}/answers")
def submit_mock_answer(
    request: Request,
    interview_id: str,
    payload: MockAnswerRequest,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """
    Record an answer and kick off evaluation off the request path.

    Returns as soon as the transcript is durable. The score arrives over SSE or
    on a later fetch — never in this response, because a candidate who can see
    each score while answering adjusts to the scorer instead of thinking.
    """
    user_id = _require_user(user)
    enforce_rate_limit(request, "interview_mock_answer", limit=ANSWERS_PER_MINUTE, window=60)

    record = _owned(interview_id, user_id)
    if record["status"] == STATUS_COMPLETED:
        raise ApiError(409, "interview_closed", "This interview is already finished.")

    repo = get_repository()
    question = repo.get_question(payload.question_id)
    if question is None or question["interview_id"] != interview_id:
        raise ApiError(404, "not_found", "Question not found in this interview.")

    repo.save_response(
        question_id=payload.question_id,
        interview_id=interview_id,
        response_text=payload.response_text,
        input_mode=payload.input_mode,
        audio_url=payload.audio_url,
        duration_seconds=payload.duration_seconds or None,
        timed_out=payload.timed_out,
    )
    repo.set_status(interview_id, STATUS_AWAITING_EVALUATION)

    _enqueue_evaluation(interview_id, payload.question_id, user_id)

    return {
        "question_id": payload.question_id,
        "status": "queued",
        "word_count": len(payload.response_text.split()),
        "note": "Evaluation runs in the background and streams over SSE.",
    }


def _enqueue_evaluation(interview_id: str, question_id: str, user_id: str) -> None:
    """
    Hand one answer to the worker queue.

    Fire-and-forget on the app's task set: a full queue or a disabled worker must
    never fail the candidate's submission, because the transcript is already
    durable and `POST /finish` can evaluate synchronously as a fallback.
    """
    from interview import workers

    try:
        workers.enqueue_evaluation(interview_id, question_id, user_id)
    except Exception:  # noqa: BLE001 - degradation must not break the request
        import logging

        logging.getLogger("dedan.interview.workers").warning(
            "could not queue evaluation for %s/%s; it will run on finish",
            interview_id[:8],
            question_id[:8],
        )


@router.post("/mocks/{interview_id}/finish")
def finish_mock(
    request: Request,
    interview_id: str,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """
    Complete the interview, evaluating anything the worker has not finished.

    This is the guaranteed path: whatever the queue did or did not do, calling
    finish returns a complete report or an explicit failure — never a partial
    one presented as final.
    """
    user_id = _require_user(user)
    record = _owned(interview_id, user_id)

    repo = get_repository()
    from interview import workers

    outcome = workers.evaluate_outstanding(interview_id, user_id, background=False)

    transcript = repo.transcript(interview_id)
    if transcript is None:
        raise ApiError(404, "not_found", "Interview not found.")

    if outcome.get("failed"):
        # Two failures that read alike and mean different things.
        #
        # The provider is not configured at all: no retry will ever succeed, so
        # another 503 would strand the candidate on a screen they cannot leave.
        # The transcript is real work and it is the deliverable — return it,
        # unscored, with the reason stated.
        if not get_llm().available:
            logging.getLogger("dedan.interview.api").warning(
                "Finishing %s with no evaluator configured; returning the transcript unscored.",
                interview_id,
            )
            return _finalise(repo, interview_id, transcript, scored_override=0)

        # The provider is configured but this run failed. That is retryable, and
        # reporting a partial transcript as a finished interview would be a lie.
        repo.set_status(interview_id, STATUS_ACTIVE)
        raise ApiError(
            503,
            "evaluation_failed",
            f"{outcome['failed']} answer(s) could not be evaluated: {outcome['reason']}",
        )

    report = _finalise(repo, interview_id, transcript)
    return report


def _finalise(
    repo,
    interview_id: str,
    transcript: dict[str, Any],
    *,
    scored_override: int | None = None,
) -> dict[str, Any]:
    """
    Score the rollup, persist it, and shape the response.

    ``scored_override`` forces the "nothing was scored" shape, for the case
    where the evaluator is unavailable. Named to avoid shadowing the local
    ``evaluated`` list built below.
    """
    evaluated: list[dict[str, Any]] = []
    for question in transcript["questions"]:
        response = question.get("response") or {}
        evaluated.append(
            {
                "target_skill": question["target_skill"],
                "kind": question["kind"],
                "score": response.get("score"),
                "relevance_score": response.get("relevance_score"),
                "clarity_score": response.get("clarity_score"),
                "accuracy_score": response.get("accuracy_score"),
                "strengths": response.get("strengths") or [],
                "weak_areas": response.get("weak_areas") or [],
                "recommendations": response.get("recommendations") or [],
            }
        )

    overall_text = _overall_commentary(transcript, evaluated)
    summary = evaluator_module.summarise_interview(evaluated, overall=overall_text)
    if scored_override is not None:
        # No evaluator meant nothing was scored. The rollup must say so rather
        # than reporting a mean over zero answers.
        summary["total_score"] = None
        summary["answers_scored"] = 0
        summary["coverage"] = 0.0
        summary["overall_feedback"] = (
            "No interview model was configured, so these answers were not scored. "
            "The full transcript is below — it is the part worth reviewing."
        )
        summary["strengths"] = []
        summary["weak_areas"] = []
        summary["recommendations"] = []

    repo.complete_interview(
        interview_id,
        total_score=summary["total_score"],
        overall_feedback=summary["overall_feedback"],
        strengths=summary["strengths"],
        weak_areas=summary["weak_areas"],
        recommendations=summary["recommendations"],
        skill_radar=summary["skill_radar"],
    )

    return {
        "interview_id": interview_id,
        "status": STATUS_COMPLETED,
        **summary,
        "questions": transcript["questions"],
        "job": {
            "title": transcript["interview"]["job_title"],
            "company": transcript["interview"]["job_company"],
        },
    }


def _overall_commentary(transcript: dict[str, Any], evaluated: list[dict[str, Any]]) -> str:
    """Overall verdict, from the model when possible, from the numbers when not."""
    client = get_llm()
    if client.available:
        try:
            payload = client.complete_json(
                system=(
                    "You write the closing paragraph of an interview debrief. "
                    "Be specific to this role and these answers. Name the strongest "
                    "signal and the clearest gap. No encouragement padding, no "
                    "generic advice. Do not speculate about the person. JSON only."
                ),
                user=(
                    f"Role: {transcript['interview']['job_title']} at "
                    f"{transcript['interview']['job_company']}\n"
                    f"Skills asked: {transcript['interview']['target_skills']}\n\n"
                    "Per-answer scores:\n"
                    + json.dumps(
                        [
                            {
                                "skill": row["target_skill"],
                                "kind": row["kind"],
                                "score": row["score"],
                            }
                            for row in evaluated
                        ]
                    )
                    + '\n\nReturn JSON: {"overall": "..."}'
                ),
                temperature=0.3,
                max_tokens=400,
            )
            text = str(payload.get("overall") or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001 - fall through to the honest summary
            pass

    # No model: state what the numbers are instead of inventing a paragraph.
    scored = [r for r in evaluated if isinstance(r["score"], (int, float))]
    if not scored:
        return (
            "No answer was evaluated, so no interview-level judgement is offered. "
            "The transcript below is the record."
        )
    answered = len(scored)
    total = transcript["questions"].__len__()
    best = max(scored, key=lambda r: r["score"] or 0)
    worst = min(scored, key=lambda r: r["score"] or 0)
    return (
        f"{answered} of {total} questions were answered and scored. "
        f"Strongest area: {best['target_skill']} ({best['score']}/100). "
        f"Weakest area: {worst['target_skill']} ({worst['score']}/100). "
        "This summary is computed from the rubric scores because no evaluator "
        "model was available to write commentary."
    )


@router.get("/mocks/{interview_id}")
def get_mock(
    interview_id: str,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """Full transcript and, once finished, the report."""
    user_id = _require_user(user)
    _owned(interview_id, user_id)
    transcript = get_repository().transcript(interview_id)
    if transcript is None:
        raise ApiError(404, "not_found", "Interview not found.")
    return transcript


@router.get("/mocks")
def list_mocks(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """The caller's interview history, newest first."""
    user_id = _require_user(user)
    repo = get_repository()
    return {
        "items": repo.list_interviews(user_id, limit=limit, offset=offset),
        "total": repo.count_interviews(user_id),
    }


@router.delete("/mocks/{interview_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mock(
    interview_id: str,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> None:
    """Delete the record and everything under it."""
    user_id = _require_user(user)
    _owned(interview_id, user_id)
    get_repository().delete_interview(interview_id)


# ── PDF report ─────────────────────────────────────────────────────────────


def _report_payload(repo, interview_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """
    Merge the transcript with the stored rollup into one report dict.

    The rollup fields live on the interview row; the per-question work lives
    in the transcript. Both are read, never recomputed, so the PDF always
    reflects what is actually persisted.
    """
    interview = data.get("interview") or {}
    questions = data.get("questions") or []

    evaluated: list[dict[str, Any]] = []
    for question in questions:
        response = question.get("response") or {}
        evaluated.append(
            {
                "target_skill": question.get("target_skill"),
                "kind": question.get("kind"),
                "score": response.get("score"),
                "strengths": response.get("strengths") or [],
                "weak_areas": response.get("weak_areas") or [],
                "recommendations": response.get("recommendations") or [],
            }
        )

    radar = interview.get("skill_radar")
    if isinstance(radar, str):
        try:
            radar = json.loads(radar)
        except (TypeError, ValueError):
            radar = None

    return {
        "interview_id": interview_id,
        "status": interview.get("status"),
        "generated_at": _now_iso(),
        "interview": {
            "id": interview.get("id"),
            "job_title": interview.get("job_title"),
            "job_company": interview.get("job_company"),
            "interview_type": interview.get("interview_type"),
            "mode": interview.get("mode"),
            "target_skills": interview.get("target_skills"),
            "started_at": interview.get("created_at"),
            "completed_at": interview.get("completed_at"),
        },
        "total_score": interview.get("total_score"),
        "answers_scored": sum(1 for e in evaluated if e["score"] is not None),
        "overall_feedback": interview.get("overall_feedback"),
        "strengths": interview.get("strengths"),
        "weak_areas": interview.get("weak_areas"),
        "recommendations": interview.get("recommendations"),
        "skill_radar": radar or [],
        "questions": questions,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@router.get("/mocks/{interview_id}/report.pdf")
def download_report(
    request: Request,
    interview_id: str,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> StreamingResponse:
    """
    Download the interview report as a real PDF.

    Only a completed interview gets a "completed" report. An interview that
    is still running, was abandoned, or failed evaluation returns 409 with a
    plain reason — generating a finished-looking report for work that did not
    finish is the one thing this endpoint must never do.
    """
    user_id = _require_user(user)
    _owned(interview_id, user_id)
    enforce_rate_limit(request, "report", limit=REPORT_PER_MINUTE, window=60)

    repo = get_repository()
    transcript = repo.transcript(interview_id)
    if transcript is None:
        raise ApiError(404, "not_found", "Interview not found.")

    interview = transcript.get("interview") or {}
    status_value = str(interview.get("status") or "")
    if status_value != STATUS_COMPLETED:
        reason = {
            STATUS_ACTIVE: "This interview is still in progress.",
            STATUS_AWAITING_EVALUATION: "This interview is still being evaluated.",
            STATUS_FAILED: "Evaluation for this interview failed, so no report was produced.",
            STATUS_ABANDONED: "This interview was ended before it finished.",
        }.get(status_value, "This interview has not been completed.")
        raise ApiError(409, "not_completed", reason + " A PDF report is only available for completed interviews.")

    payload = _report_payload(repo, interview_id, transcript)
    pdf = build_interview_report_pdf(payload)

    filename = safe_filename(interview)
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={
            # `attachment` + a filename so the browser saves rather than opens.
            # The name is derived from the role, and it is quoted because role
            # titles contain spaces.
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf)),
            # A report is immutable once generated; safe to cache privately.
            "Cache-Control": "private, max-age=60",
        },
    )


# ── SSE ─────────────────────────────────────────────────────────────────────


@router.get("/mocks/{interview_id}/stream")
async def stream_mock(
    interview_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    """
    Server-Sent Events: evaluation progress, then the final report.

    SSE rather than WebSockets because this is one-directional — the candidate
    never pushes over this channel, they POST answers. SSE gets automatic
    browser reconnect, survives proxies that mangle upgrades, and needs no extra
    dependency. The alternative that does not work is holding one HTTP request
    open across six sequential model calls.
    """
    user_id = _require_user(user)
    _owned(interview_id, user_id)
    repo = get_repository()

    async def events() -> AsyncIterator[bytes]:
        started = time.monotonic()
        yield _sse("open", {"interview_id": interview_id, "status": "streaming"})

        final: Optional[dict[str, Any]] = None

        # No evaluator configured means the pending answers will never be scored.
        # Polling for them would burn the whole stream budget and then time out,
        # when the transcript is available right now.
        evaluator_up = get_llm().available
        if not evaluator_up:
            logging.getLogger("dedan.interview.api").warning(
                "Streaming %s with no evaluator configured; returning the transcript unscored.",
                interview_id,
            )

        while time.monotonic() - started < STREAM_BUDGET_SECONDS:
            pending = repo.unevaluated_responses(interview_id)
            done = len(repo.list_responses(interview_id)) - len(pending)

            if not pending or not evaluator_up:
                # Everything scored, or nothing can be scored — produce the report.
                transcript = repo.transcript(interview_id)
                if transcript is None:
                    yield _sse("error", {"message": "Interview not found."})
                    return
                loop = asyncio.get_running_loop()
                final = await loop.run_in_executor(
                    None,
                    lambda: _finalise(
                        repo,
                        interview_id,
                        transcript,
                        scored_override=0 if not evaluator_up else None,
                    ),
                )
                yield _sse("report", final)
                return

            yield _sse(
                "progress",
                {
                    "evaluated": done,
                    "pending": len(pending),
                    "message": "Scoring your answers…",
                },
            )
            await asyncio.sleep(STREAM_POLL_SECONDS)

        # Budget exhausted. Say so and tell the client how to finish safely,
        # rather than closing the stream and leaving the UI waiting forever.
        yield _sse(
            "timeout",
            {
                "message": (
                    "Evaluation is taking longer than this connection can hold. "
                    "Reconnect to the stream, or call the finish endpoint to "
                    "complete it synchronously."
                ),
                "remaining": len(repo.unevaluated_responses(interview_id)),
            },
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Tell nginx not to buffer, which would defeat the point.
            "X-Accel-Buffering": "no",
        },
    )


# ── skill alerts ────────────────────────────────────────────────────────────


@router.get("/alerts")
def get_alert(user: Optional[dict] = Depends(get_current_user_optional)) -> dict[str, Any]:
    """The caller's skill watch, or an empty record if they have none."""
    user_id = _require_user(user)
    alert = get_repository().get_skill_alert(user_id)
    return {"alert": alert or {"target_skills": [], "email_enabled": False, "min_overlap": 0.5}}


@router.put("/alerts")
def put_alert(
    payload: SkillAlertRequest,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """
    Save the skill watch. One per user — it is a setting, not a log.

    Email opt-in is refused unless an address is on file: silently accepting
    `email_enabled: true` with nowhere to send would create a promise the
    system cannot keep.
    """
    user_id = _require_user(user)
    if payload.email_enabled and not (payload.email_address or (user or {}).get("email")):
        raise ApiError(
            400,
            "email_address_required",
            "Email alerts need an address to send to.",
        )
    address = payload.email_address or (user or {}).get("email")
    alert = get_repository().upsert_skill_alert(
        user_id=user_id,
        target_skills=[s.strip() for s in payload.target_skills if s.strip()],
        email_enabled=payload.email_enabled,
        email_address=address,
        min_overlap=payload.min_overlap,
    )
    return {"alert": alert}
