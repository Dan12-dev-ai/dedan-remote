"""
Background workers — evaluation and skill-match alerts.

A queue abstraction with two backends and no new dependency:

    AsyncioQueue   default. An in-process task set with bounded concurrency.
                   Zero infrastructure, survives a single-process deployment,
                   and is honest about being in-process.

    RedisQueue     used when ``REDIS_URL`` is set and reachable. Work lands in a
                   Redis list and any API replica can drain it, which is what you
                   need the moment you run more than one.

Why not Celery: it needs a broker, a result backend and a separate worker
process, none of which this deployment has, and adding a hard dependency for a
workload that is a few model calls per minute would be a poor trade. The
interface here is deliberately small enough that swapping in Celery later is a
new backend class, not a rewrite.

Reliability rules that apply to both backends:

- **Evaluation never blocks the request.** Submitting an answer returns once the
  transcript is durable; the rubric is computed here.
- **A failure is recorded, never swallowed.** ``evaluate_outstanding`` reports
  what failed and why, so ``POST /finish`` can refuse to present a partial
  report as final.
- **Email is opt-in and deduplicated.** A skill watch only mails a listing it has
  not already mailed, so an hourly crawl cannot spam a user every cycle.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from interview import evaluator as evaluator_module
from interview.persistence import get_repository

logger = logging.getLogger("dedan.interview.workers")


# ── queue abstraction ───────────────────────────────────────────────────────


@dataclass
class Job:
    """One unit of background work."""

    kind: str
    interview_id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.kind}:{self.interview_id}:{self.payload.get('question_id', '')}"


class AsyncioQueue:
    """
    In-process task queue.

    Two things it gets right that a bare ``create_task`` does not:

    - **Bounded concurrency.** A semaphore caps in-flight work, so six answers
      submitted at once do not open six simultaneous model calls and trip a
      free tier's rate limit — which is the failure this whole module exists to
      prevent.
    - **Failures are captured.** A task that raises is recorded on the job rather
      than vanishing into an unretrieved task exception.
    """

    def __init__(
        self, concurrency: int = 2, loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> None:
        self.concurrency = max(1, concurrency)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._loop = loop
        self.failures: list[tuple[str, str]] = []
        # Sync endpoints run in the anyio threadpool, where there is no running
        # loop. Blocking *that* thread on a 20s model call would defeat the whole
        # point of the queue, so the fallback is a bounded worker thread pool.
        self._threads = ThreadPoolExecutor(
            max_workers=self.concurrency,
            thread_name_prefix="interview-eval",
        )

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        return self._loop

    def submit(self, handler: Callable[[Job], Any], job: Job) -> None:
        """Schedule ``job``; returns immediately."""
        try:
            loop = self._ensure_loop()
        except RuntimeError:
            # No event loop (a sync endpoint). Hand off to the thread pool so the
            # request returns before the model call starts.
            self._threads.submit(self._run_safely, handler, job)
            return

        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)

        async def guarded() -> None:
            async with self._semaphore:
                self._run_safely(handler, job)

        task = loop.create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _run_safely(self, handler: Callable[[Job], Any], job: Job) -> None:
        """Run one job, recording rather than raising on failure."""
        try:
            result = handler(job)
            if asyncio.iscoroutine(result):
                # A coroutine handler from the thread-pool path would need a
                # loop of its own; none of ours are async, so this is a guard.
                result.close()
                raise TypeError("async handlers require the asyncio queue path")
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            self.failures.append((job.key(), str(exc)))
            logger.warning("job %s failed: %s", job.key(), exc)

    def pending(self) -> int:
        return len([t for t in self._tasks if not t.done()])

    def shutdown(self) -> None:
        """Drain the thread pool. Called on app shutdown and by tests."""
        self._threads.shutdown(wait=True)

    async def drain(self, timeout: float = 10.0) -> None:
        """Await outstanding work. Used by tests and graceful shutdown."""
        if not self._tasks:
            return
        await asyncio.wait(set(self._tasks), timeout=timeout)


class RedisQueue:
    """
    Redis-list-backed queue for multi-process deployments.

    Kept deliberately small and dependency-free (the project already requires
    ``redis``). Work is pushed with ``RPUSH`` and taken with ``BLPOP``, which is
    the smallest thing that gives at-least-once delivery across replicas.
    """

    def __init__(self, url: str, queue: str = "dedan:interview:eval", concurrency: int = 2) -> None:
        self.url = url
        self.queue = queue
        self.concurrency = max(1, concurrency)
        self._consumers: list[threading.Thread] = []
        self._stopping = False

    def _client(self):  # pragma: no cover - needs a live Redis
        import redis

        return redis.Redis.from_url(self.url, decode_responses=True)

    def submit(self, handler: Callable[[Job], Any], job: Job) -> None:  # pragma: no cover
        import json as _json

        try:
            self._client().rpush(self.queue, _json.dumps({"job": job.__dict__}))
        except Exception:
            # Redis being down must not fail the candidate's submission.
            logger.warning("could not enqueue %s in Redis; running inline", job.key())
            handler(job)
            return
        # Without a consumer nothing would ever pop the job. Start the consumer
        # lazily on the first push rather than at import, so importing this
        # module never opens a socket.
        self._ensure_consumers(handler)

    def _ensure_consumers(self, handler: Callable[[Job], Any]) -> None:  # pragma: no cover
        if self._stopping or self._consumers:
            return
        for index in range(self.concurrency):
            thread = threading.Thread(
                target=self._consume,
                args=(handler, index),
                name=f"interview-redis-{index}",
                daemon=True,
            )
            thread.start()
            self._consumers.append(thread)
        logger.info("started %d Redis consumer(s) on %s", self.concurrency, self.queue)

    def _consume(self, handler: Callable[[Job], Any], index: int) -> None:  # pragma: no cover
        """Block on the queue, running one job at a time, until stopped."""
        while not self._stopping:
            try:
                if not self.drain_once(handler):
                    continue
            except Exception:
                # A Redis blip must not kill the consumer; back off briefly so a
                # persistent outage does not spin the log.
                logger.exception("Redis consumer %d failed; retrying", index)
                time.sleep(1.0)

    def stop(self, timeout: float = 5.0) -> None:  # pragma: no cover
        """Signal consumers to exit and give them a moment to finish a job."""
        self._stopping = True
        for thread in self._consumers:
            thread.join(timeout=timeout)
        self._consumers.clear()

    def drain_once(self, handler: Callable[[Job], Any]) -> bool:  # pragma: no cover
        """Pop and run one job. Returns False when the queue is empty."""
        import json as _json

        client = self._client()
        popped = client.blpop(self.queue, timeout=1)
        if not popped:
            return False
        data = _json.loads(popped[1])
        raw = data["job"]
        handler(
            Job(kind=raw["kind"], interview_id=raw["interview_id"], payload=raw.get("payload", {}))
        )
        return True


def _build_queue():
    """Pick a backend from the environment, degrading to in-process."""
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        logger.info("interview worker queue: redis (%s)", url.split("@")[-1])
        return RedisQueue(url)
    logger.info("interview worker queue: in-process (no REDIS_URL set)")
    return AsyncioQueue()


_queue: Any = None


def get_queue():
    global _queue
    if _queue is None:
        from config.settings import get_settings

        settings = get_settings()
        _queue = _build_queue() if settings.WORKER_ENABLED else None
    return _queue


def reset_queue() -> None:
    """Swap the queue, shutting the old pool down so threads are not leaked."""
    global _queue
    if isinstance(_queue, AsyncioQueue):
        _queue.shutdown()
    elif isinstance(_queue, RedisQueue):
        # Stop the blocking consumers, or they outlive the process teardown.
        _queue.stop()
    _queue = None


def enqueue_evaluation(interview_id: str, question_id: str, user_id: str) -> bool:
    """Queue one answer for evaluation. Returns False when no queue is active."""
    queue = get_queue()
    if queue is None:
        return False
    queue.submit(
        run_evaluation,
        Job(
            kind="evaluate_response",
            interview_id=interview_id,
            payload={"question_id": question_id, "user_id": user_id},
        ),
    )
    return True


# ── the evaluation handler ──────────────────────────────────────────────────


def run_evaluation(job: Job) -> Optional[dict[str, Any]]:
    """
    Score one answer and persist the rubric.

    Returns the evaluation on success, ``None`` on failure. The failure is logged
    and left visible (``score IS NULL``) so ``finish`` can report it rather than
    quietly publishing a report with a hole in it.
    """
    question_id = job.payload.get("question_id")
    user_id = job.payload.get("user_id")
    if not question_id:
        return None

    repo = get_repository()
    question = repo.get_question(question_id)
    if question is None or (user_id and repo.get_interview(job.interview_id)["user_id"] != user_id):
        logger.warning("evaluation job for unknown question %s", question_id[:8])
        return None

    response = repo.get_response(question_id)
    if response is None:
        logger.warning("no stored response for %s", question_id[:8])
        return None
    if response.get("score") is not None:
        # Already scored — a duplicate queue delivery, not new work.
        return response

    from interview.provider import get_llm

    job_row = repo.get_interview(job.interview_id)
    # Named `role` rather than `job`: `job` is already the queue Job, and
    # reassigning it to a dict shadowed the parameter mid-function.
    role = {"title": job_row["job_title"], "company": job_row["job_company"]}
    # The timer flag travels with the question so the evaluator can account for
    # an answer that was cut off mid-sentence.
    question = {**question, "timed_out": response.get("timed_out")}

    try:
        evaluation = evaluator_module.evaluate_answer(
            question=question,
            response_text=response.get("response_text") or "",
            job=role,
            client=get_llm(),
        )
    except evaluator_module.EvaluationError as exc:
        logger.warning("evaluation failed for %s: %s", question_id[:8], exc)
        return None
    except Exception as exc:  # noqa: BLE001 - provider/network failures
        logger.warning("evaluation errored for %s: %s", question_id[:8], exc)
        return None

    repo.record_evaluation(
        question_id=question_id,
        score=evaluation.score,
        clarity_score=evaluation.clarity_score,
        # NULL, not 0.0, when the dimension does not apply. Storing a zero for a
        # behavioural answer would drag the interview-level accuracy mean down as
        # though the candidate were wrong about something never asked.
        accuracy_score=evaluation.accuracy_score,
        relevance_score=evaluation.relevance_score,
        detailed_feedback=evaluation.detailed_feedback,
        strengths=evaluation.strengths,
        weak_areas=evaluation.weak_areas,
        recommendations=evaluation.recommendations,
        evaluator_model=evaluation.model,
    )
    return evaluation.to_dict()


def evaluate_outstanding(
    interview_id: str,
    user_id: str,
    *,
    background: bool = False,
) -> dict[str, Any]:
    """
    Evaluate every response still missing a score.

    ``background=True`` queues them and returns immediately (the SSE path).
    ``background=False`` runs them inline and reports precisely what failed —
    that is the guaranteed completion path used by ``POST /finish``.
    """
    repo = get_repository()
    record = repo.get_interview(interview_id)
    if record is None or record["user_id"] != user_id:
        return {"evaluated": 0, "failed": 0, "reason": "interview not found"}

    pending = repo.unevaluated_responses(interview_id)
    if not pending:
        return {"evaluated": 0, "failed": 0, "reason": "nothing pending"}

    if background:
        queued = 0
        for response in pending:
            if enqueue_evaluation(interview_id, response["question_id"], user_id):
                queued += 1
        return {"evaluated": 0, "queued": queued, "failed": 0, "reason": "queued"}

    evaluated = 0
    failed = 0
    reason = ""
    for response in pending:
        result = run_evaluation(
            Job(
                kind="evaluate_response",
                interview_id=interview_id,
                payload={"question_id": response["question_id"], "user_id": user_id},
            )
        )
        if result is None:
            failed += 1
            if not reason:
                reason = "the evaluator model is unavailable or returned no usable rubric"
        else:
            evaluated += 1

    return {"evaluated": evaluated, "failed": failed, "reason": reason}


# ── skill-match alerts ──────────────────────────────────────────────────────


def match_and_alert(job_row: dict[str, Any], *, send_email: bool = True) -> dict[str, Any]:
    """
    Check one newly ingested listing against every user's skill watch.

    Returns a summary rather than sending silently, so the CLI and the tests can
    inspect what would have gone out.

    Email rules:
      - opt-in must be set and an address must exist
      - the listing must not have already been mailed to that user
      - overlap must meet the user's own threshold
    """
    from interview.skills import extract_skills, overlap_ratio

    repo = get_repository()
    vector = extract_skills(job_row)
    if vector.empty:
        return {
            "matched": 0,
            "considered": 0,
            "emails": 0,
            "reason": "listing has no recognisable skills",
        }

    job_id = job_row.get("slug") or job_row.get("id") or ""
    matched = 0
    emails = 0
    considered = 0

    for alert in repo.list_skill_alerts():
        considered += 1
        ratio = overlap_ratio(vector, alert["target_skills"])
        if ratio < alert["min_overlap"]:
            continue
        matched += 1
        if repo.already_matched(alert["user_id"], job_id):
            continue
        if send_email and alert["email_enabled"] and alert["email_address"]:
            if _send_match_email(alert, job_row, vector, ratio):
                emails += 1
        repo.mark_skill_alert_matched(alert["user_id"], job_id)

    return {
        "matched": matched,
        "considered": considered,
        "emails": emails,
        "job_id": job_id,
        "skills": vector.primary,
    }


def _send_match_email(
    alert: dict[str, Any],
    job_row: dict[str, Any],
    vector,
    ratio: float,
) -> bool:
    """
    Send one match notice over SMTP.

    Returns False rather than raising: a mail failure must not abort the sweep
    over the remaining users. The alternative — an exception on the first bad
    address — silently starves everyone after it.
    """
    import smtplib
    from email.message import EmailMessage

    from config.settings import get_settings

    settings = get_settings()
    if not settings.EMAIL or not settings.EMAIL_PASSWORD:
        logger.info("email not configured; match notice not sent")
        return False

    matched = ", ".join(vector.primary[:4])
    message = EmailMessage()
    message["Subject"] = (
        f"New remote role matching your skills: {job_row.get('title', 'opportunity')}"
    )
    message["From"] = settings.EMAIL
    message["To"] = alert["email_address"]
    message.set_content(
        "A new listing matched the skills you are watching.\n\n"
        f"Role: {job_row.get('title', 'Untitled')}\n"
        f"Employer: {job_row.get('company') or job_row.get('source_info', {}).get('name', 'Unknown')}\n"
        f"Location: {job_row.get('location_label') or 'Not stated'}\n"
        f"Matched skills: {matched}\n"
        f"Skill overlap: {int(ratio * 100)}%\n"
        f"Apply: {job_row.get('apply_url') or job_row.get('url', '')}\n\n"
        "You are receiving this because you opted in to skill alerts. "
        "Turn them off in Settings → Notification Preferences."
    )

    try:
        with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=15) as server:
            # 587 is submission; implicit TLS on 465 is not offered by the
            # notifier this mirrors, so STARTTLS matches existing behaviour.
            server.starttls()
            server.login(settings.EMAIL, settings.EMAIL_PASSWORD)
            server.send_message(message)
        return True
    except smtplib.SMTPException as exc:
        logger.warning("skill-alert email failed: %s", exc)
        return False
    except OSError as exc:
        logger.warning("skill-alert email unreachable: %s", exc)
        return False
