"""
Durable interview persistence.

The live engine deliberately keeps sessions in memory (`interview/store.py`)
because a conversational session is ephemeral. That is the wrong shape for a
*record*: a candidate's transcript and report must survive a restart, a network
drop and a support request weeks later. So this store is separate and durable,
and the two coexist:

    interview/store.py        in-memory, TTL'd, the live turn-by-turn session
    interview/persistence.py  SQLite, the interview of record

Schema follows the brief:

    interviews            one attempt at one job
    interview_questions   the generated set, per skill, with a time limit
    interview_responses   one answer per question, with its evaluation
    job_skill_alerts      a saved skill watch + its email opt-in

Two decisions worth stating:

- **`audio_url` is not populated by this module.** Recording is a browser
  concern and there is no media store in this deployment; the column exists
  because the brief specifies it and it is always NULL rather than holding a
  path to a file that was never written. Claiming audio was retained when it
  was not would be the worst possible lie in an interview product.
- **Evaluation results are stored verbatim, scores and prose together.** A bare
  number with no reasoning is not reviewable, and this product's whole claim is
  that every score is explainable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# Statuses an interview moves through. Kept as literals (not free text) so a
# stuck interview is detectable by a query rather than by reading rows.
STATUS_ACTIVE = "active"
STATUS_AWAITING_EVALUATION = "awaiting_evaluation"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_ABANDONED = "abandoned"

ALL_STATUSES = (
    STATUS_ACTIVE,
    STATUS_AWAITING_EVALUATION,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_ABANDONED,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS interviews (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    job_id            TEXT NOT NULL,
    job_title         TEXT,
    job_company       TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    target_skills     TEXT NOT NULL DEFAULT '[]',
    total_score       REAL,
    overall_feedback  TEXT,
    strengths         TEXT NOT NULL DEFAULT '[]',
    weak_areas        TEXT NOT NULL DEFAULT '[]',
    recommendations   TEXT NOT NULL DEFAULT '[]',
    skill_radar       TEXT NOT NULL DEFAULT '[]',
    mode              TEXT NOT NULL DEFAULT 'live',
    created_at        TEXT NOT NULL,
    completed_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_interviews_user
    ON interviews (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_interviews_status
    ON interviews (status, created_at DESC);

CREATE TABLE IF NOT EXISTS interview_questions (
    id                TEXT PRIMARY KEY,
    interview_id      TEXT NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    target_skill      TEXT NOT NULL,
    skill_family      TEXT,
    kind              TEXT NOT NULL,
    difficulty        TEXT NOT NULL DEFAULT 'moderate',
    question_text     TEXT NOT NULL,
    rubric_focus      TEXT NOT NULL DEFAULT '[]',
    time_limit_sec    INTEGER NOT NULL DEFAULT 180,
    order_index       INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE (interview_id, order_index)
);
CREATE INDEX IF NOT EXISTS idx_questions_interview
    ON interview_questions (interview_id, order_index);

CREATE TABLE IF NOT EXISTS interview_responses (
    id                 TEXT PRIMARY KEY,
    question_id        TEXT NOT NULL REFERENCES interview_questions(id) ON DELETE CASCADE,
    interview_id       TEXT NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    response_text      TEXT,
    input_mode         TEXT NOT NULL DEFAULT 'text',
    audio_url          TEXT,
    duration_seconds   REAL,
    timed_out          INTEGER NOT NULL DEFAULT 0,
    word_count         INTEGER,
    score              REAL,
    clarity_score      REAL,
    accuracy_score     REAL,
    relevance_score    REAL,
    detailed_feedback  TEXT,
    strengths          TEXT NOT NULL DEFAULT '[]',
    weak_areas         TEXT NOT NULL DEFAULT '[]',
    recommendations    TEXT NOT NULL DEFAULT '[]',
    evaluator_model    TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_responses_interview
    ON interview_responses (interview_id);
CREATE INDEX IF NOT EXISTS idx_responses_question
    ON interview_responses (question_id);
-- At most one answer per question: re-submitting replaces, never duplicates.
CREATE UNIQUE INDEX IF NOT EXISTS idx_responses_question_unique
    ON interview_responses (question_id);

CREATE TABLE IF NOT EXISTS job_skill_alerts (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    target_skills_json   TEXT NOT NULL DEFAULT '[]',
    email_enabled        INTEGER NOT NULL DEFAULT 0,
    email_address        TEXT,
    min_overlap          REAL NOT NULL DEFAULT 0.5,
    last_matched_job_id  TEXT,
    last_matched_at      TEXT,
    created_at           TEXT NOT NULL,
    UNIQUE (user_id)
);
CREATE INDEX IF NOT EXISTS idx_skill_alerts_user
    ON job_skill_alerts (user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: Optional[str], fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        # A corrupt column must not make the whole record unreadable.
        return fallback


class InterviewRepository:
    """Thread-safe SQLite access to the interview tables."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        from config.settings import get_settings

        self._db_path = db_path or get_settings().INTERVIEW_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.create_tables()

    # ── plumbing ─────────────────────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    def create_tables(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(SCHEMA)

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock, self.connect() as conn:
            return list(conn.execute(sql, tuple(params)))

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock, self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            conn.commit()
            return cursor.rowcount

    # ── interviews ───────────────────────────────────────────────────────────

    def create_interview(
        self,
        *,
        user_id: str,
        job_id: str,
        target_skills: list[str],
        job_title: Optional[str] = None,
        job_company: Optional[str] = None,
        mode: str = "live",
        interview_id: Optional[str] = None,
    ) -> str:
        """Open an interview row and return its id."""
        new_id = interview_id or uuid.uuid4().hex
        self._execute(
            """
            INSERT INTO interviews
                (id, user_id, job_id, job_title, job_company, status,
                 target_skills, mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                user_id,
                job_id,
                job_title,
                job_company,
                STATUS_ACTIVE,
                _json(target_skills),
                mode,
                _now(),
            ),
        )
        return new_id

    def set_status(self, interview_id: str, status: str) -> None:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown interview status: {status!r}")
        self._execute(
            "UPDATE interviews SET status = ? WHERE id = ?",
            (status, interview_id),
        )

    def complete_interview(
        self,
        interview_id: str,
        *,
        total_score: Optional[float],
        overall_feedback: str,
        strengths: Optional[list[str]] = None,
        weak_areas: Optional[list[str]] = None,
        recommendations: Optional[list[str]] = None,
        skill_radar: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._execute(
            """
            UPDATE interviews
               SET status = ?, total_score = ?, overall_feedback = ?,
                   strengths = ?, weak_areas = ?, recommendations = ?,
                   skill_radar = ?, completed_at = ?
             WHERE id = ?
            """,
            (
                STATUS_COMPLETED,
                total_score,
                overall_feedback,
                _json(strengths or []),
                _json(weak_areas or []),
                _json(recommendations or []),
                _json(skill_radar or []),
                _now(),
                interview_id,
            ),
        )

    def get_interview(self, interview_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM interviews WHERE id = ?", (interview_id,))
        return self._interview_row(rows[0]) if rows else None

    def list_interviews(
        self, user_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT * FROM interviews
             WHERE user_id = ?
             ORDER BY created_at DESC
             LIMIT ? OFFSET ?
            """,
            (user_id, max(1, min(limit, 100)), max(0, offset)),
        )
        return [self._interview_row(r) for r in rows]

    def count_interviews(self, user_id: str) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM interviews WHERE user_id = ?", (user_id,))
        return int(rows[0]["n"]) if rows else 0

    def delete_interview(self, interview_id: str) -> None:
        """Cascades to questions and responses (FK ON DELETE CASCADE)."""
        self._execute("DELETE FROM interviews WHERE id = ?", (interview_id,))

    # ── questions ────────────────────────────────────────────────────────────

    def save_questions(self, interview_id: str, questions: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for index, question in enumerate(questions):
            qid = question.get("id") or uuid.uuid4().hex
            self._execute(
                """
                INSERT INTO interview_questions
                    (id, interview_id, target_skill, skill_family, kind, difficulty,
                     question_text, rubric_focus, time_limit_sec, order_index, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qid,
                    interview_id,
                    question["target_skill"],
                    question.get("skill_family"),
                    question["kind"],
                    question.get("difficulty", "moderate"),
                    question["question_text"],
                    _json(question.get("rubric_focus", [])),
                    int(question.get("time_limit_sec", 180)),
                    index,
                    _now(),
                ),
            )
            ids.append(qid)
        return ids

    def list_questions(self, interview_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT * FROM interview_questions
             WHERE interview_id = ?
             ORDER BY order_index ASC
            """,
            (interview_id,),
        )
        return [
            {
                "id": r["id"],
                "interview_id": r["interview_id"],
                "target_skill": r["target_skill"],
                "skill_family": r["skill_family"],
                "kind": r["kind"],
                "difficulty": r["difficulty"],
                "question_text": r["question_text"],
                "rubric_focus": _loads(r["rubric_focus"], []),
                "time_limit_sec": r["time_limit_sec"],
                "order_index": r["order_index"],
            }
            for r in rows
        ]

    def get_question(self, question_id: str) -> Optional[dict[str, Any]]:
        rows = self._query(
            "SELECT interview_id FROM interview_questions WHERE id = ?", (question_id,)
        )
        if not rows:
            return None
        for question in self.list_questions(rows[0]["interview_id"]):
            if question["id"] == question_id:
                return question
        return None

    # ── responses ────────────────────────────────────────────────────────────

    def save_response(
        self,
        *,
        question_id: str,
        interview_id: str,
        response_text: str,
        input_mode: str = "text",
        audio_url: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        timed_out: bool = False,
    ) -> str:
        """
        Record an answer, replacing any earlier one for the same question.

        Upsert rather than insert: a candidate who redoes a question must not end
        up with two responses and an inflated question count.
        """
        existing = self._query(
            "SELECT id FROM interview_responses WHERE question_id = ?", (question_id,)
        )
        rid = existing[0]["id"] if existing else uuid.uuid4().hex
        stamp = _now()
        words = len(response_text.split()) if response_text else 0
        if existing:
            self._execute(
                """
                UPDATE interview_responses
                   SET response_text = ?, input_mode = ?, audio_url = ?,
                       duration_seconds = ?, timed_out = ?, word_count = ?,
                       score = NULL, clarity_score = NULL, accuracy_score = NULL,
                       relevance_score = NULL, detailed_feedback = NULL,
                       strengths = '[]', weak_areas = '[]', recommendations = '[]',
                       evaluator_model = NULL, updated_at = ?
                 WHERE id = ?
                """,
                (
                    response_text,
                    input_mode,
                    audio_url,
                    duration_seconds,
                    1 if timed_out else 0,
                    words,
                    stamp,
                    rid,
                ),
            )
        else:
            self._execute(
                """
                INSERT INTO interview_responses
                    (id, question_id, interview_id, response_text, input_mode,
                     audio_url, duration_seconds, timed_out, word_count,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    question_id,
                    interview_id,
                    response_text,
                    input_mode,
                    audio_url,
                    duration_seconds,
                    1 if timed_out else 0,
                    words,
                    stamp,
                    stamp,
                ),
            )
        return rid

    def record_evaluation(
        self,
        *,
        question_id: str,
        score: float,
        clarity_score: float,
        accuracy_score: float,
        relevance_score: float,
        detailed_feedback: str,
        strengths: Optional[list[str]] = None,
        weak_areas: Optional[list[str]] = None,
        recommendations: Optional[list[str]] = None,
        evaluator_model: Optional[str] = None,
    ) -> None:
        """Attach an evaluation to an already-saved response."""
        self._execute(
            """
            UPDATE interview_responses
               SET score = ?, clarity_score = ?, accuracy_score = ?,
                   relevance_score = ?, detailed_feedback = ?,
                   strengths = ?, weak_areas = ?, recommendations = ?,
                   evaluator_model = ?, updated_at = ?
             WHERE question_id = ?
            """,
            (
                score,
                clarity_score,
                accuracy_score,
                relevance_score,
                detailed_feedback,
                _json(strengths or []),
                _json(weak_areas or []),
                _json(recommendations or []),
                evaluator_model,
                _now(),
                question_id,
            ),
        )

    def get_response(self, question_id: str) -> Optional[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM interview_responses WHERE question_id = ?", (question_id,)
        )
        return self._response_row(rows[0]) if rows else None

    def list_responses(self, interview_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM interview_responses WHERE interview_id = ?", (interview_id,)
        )
        return [self._response_row(r) for r in rows]

    def unevaluated_responses(self, interview_id: str) -> list[dict[str, Any]]:
        """Responses awaiting the evaluator — the async pipeline's work queue."""
        rows = self._query(
            """
            SELECT * FROM interview_responses
             WHERE interview_id = ? AND score IS NULL
             ORDER BY created_at ASC
            """,
            (interview_id,),
        )
        return [self._response_row(r) for r in rows]

    # ── full transcript ──────────────────────────────────────────────────────

    def transcript(self, interview_id: str) -> Optional[dict[str, Any]]:
        """
        Interview + questions + responses in one object.

        This is what the export endpoint serves. Ordered by `order_index`, with
        unanswered questions still present — a report that silently omits the
        question a candidate could not answer is misleading.
        """
        interview = self.get_interview(interview_id)
        if interview is None:
            return None
        responses = {r["question_id"]: r for r in self.list_responses(interview_id)}
        questions = []
        for question in self.list_questions(interview_id):
            questions.append(
                {
                    **question,
                    "response": responses.get(question["id"]),
                }
            )
        return {"interview": interview, "questions": questions}

    # ── skill alerts ─────────────────────────────────────────────────────────

    def upsert_skill_alert(
        self,
        *,
        user_id: str,
        target_skills: list[str],
        email_enabled: bool = False,
        email_address: Optional[str] = None,
        min_overlap: float = 0.5,
    ) -> dict[str, Any]:
        """
        Create or replace a user's skill watch.

        Upsert keyed on `user_id` (a UNIQUE index enforces one watch per user)
        rather than accumulating rows, because a watch is a setting, not a log.
        """
        self._execute(
            """
            INSERT INTO job_skill_alerts
                (id, user_id, target_skills_json, email_enabled, email_address,
                 min_overlap, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                target_skills_json = excluded.target_skills_json,
                email_enabled = excluded.email_enabled,
                email_address = excluded.email_address,
                min_overlap = excluded.min_overlap
            """,
            (
                uuid.uuid4().hex,
                user_id,
                _json(target_skills),
                1 if email_enabled else 0,
                email_address,
                min(1.0, max(0.0, min_overlap)),
                _now(),
            ),
        )
        saved = self._query("SELECT * FROM job_skill_alerts WHERE user_id = ?", (user_id,))
        return self._alert_row(saved[0]) if saved else {}

    def get_skill_alert(self, user_id: str) -> Optional[dict[str, Any]]:
        rows = self._query("SELECT * FROM job_skill_alerts WHERE user_id = ?", (user_id,))
        return self._alert_row(rows[0]) if rows else None

    def list_skill_alerts(self, *, email_enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM job_skill_alerts"
        if email_enabled_only:
            sql += " WHERE email_enabled = 1"
        return [self._alert_row(r) for r in self._query(sql)]

    def mark_skill_alert_matched(self, user_id: str, job_id: str) -> None:
        """
        Record a match so the same listing cannot alert twice.

        This is the deduplication that keeps an hourly crawl from mailing the
        same opportunity every cycle.
        """
        self._execute(
            """
            UPDATE job_skill_alerts
               SET last_matched_job_id = ?, last_matched_at = ?
             WHERE user_id = ?
            """,
            (job_id, _now(), user_id),
        )

    def already_matched(self, user_id: str, job_id: str) -> bool:
        rows = self._query(
            "SELECT last_matched_job_id FROM job_skill_alerts WHERE user_id = ?",
            (user_id,),
        )
        return bool(rows and rows[0]["last_matched_job_id"] == job_id)

    # ── row mappers ──────────────────────────────────────────────────────────

    @staticmethod
    def _interview_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "job_id": row["job_id"],
            "job_title": row["job_title"],
            "job_company": row["job_company"],
            "status": row["status"],
            "target_skills": _loads(row["target_skills"], []),
            "total_score": row["total_score"],
            "overall_feedback": row["overall_feedback"],
            "strengths": _loads(row["strengths"], []),
            "weak_areas": _loads(row["weak_areas"], []),
            "recommendations": _loads(row["recommendations"], []),
            "skill_radar": _loads(row["skill_radar"], []),
            "mode": row["mode"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }

    @staticmethod
    def _response_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "question_id": row["question_id"],
            "interview_id": row["interview_id"],
            "response_text": row["response_text"],
            "input_mode": row["input_mode"],
            "audio_url": row["audio_url"],
            "duration_seconds": row["duration_seconds"],
            "timed_out": bool(row["timed_out"]),
            "word_count": row["word_count"],
            "score": row["score"],
            "clarity_score": row["clarity_score"],
            "accuracy_score": row["accuracy_score"],
            "relevance_score": row["relevance_score"],
            "detailed_feedback": row["detailed_feedback"],
            "strengths": _loads(row["strengths"], []),
            "weak_areas": _loads(row["weak_areas"], []),
            "recommendations": _loads(row["recommendations"], []),
            "evaluator_model": row["evaluator_model"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _alert_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "target_skills": _loads(row["target_skills_json"], []),
            "email_enabled": bool(row["email_enabled"]),
            "email_address": row["email_address"],
            "min_overlap": row["min_overlap"],
            "last_matched_job_id": row["last_matched_job_id"],
            "last_matched_at": row["last_matched_at"],
            "created_at": row["created_at"],
        }


_repo: Optional[InterviewRepository] = None
_repo_lock = threading.Lock()


def get_repository() -> InterviewRepository:
    global _repo
    if _repo is None:
        with _repo_lock:
            if _repo is None:
                _repo = InterviewRepository()
    return _repo


def reset_repository(db_path: Optional[str] = None) -> InterviewRepository:
    """Swap the process-wide repository (tests, and the CLI tooling)."""
    global _repo
    with _repo_lock:
        _repo = InterviewRepository(db_path)
    return _repo
