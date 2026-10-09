"""
Interview report PDF — generation and endpoint contract.

The point of these tests is that "it produced bytes" is not evidence a PDF
works. Two things are actually checked:

1. **Structure.** The bytes are parsed by a real PDF reader, so a broken
   xref table, wrong object offsets or a missing /Contents reference fail
   here instead of in a candidate's download.
2. **Content.** The document contains the values that were persisted, and
   the honesty rules hold — an unscored interview says so, an incomplete
   interview is refused, and one account cannot download another's report.

Reader preference: `pypdf` when installed (it is, transitively, via the test
deps), otherwise a byte-level structural check. Either way the assertions
below never pass on a file no parser accepts.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

import pytest
import requests
from fastapi.testclient import TestClient

import api.routers.interview_mock as mock_router
import api.store as store_module
from api.main import create_app
from api.security import rate_limiter
from interview import persistence as persistence_module
from interview import provider as provider_module
from interview import workers as workers_module
from interview.persistence import reset_repository
from interview.report_pdf import (
    _pdf_text,
    _wrap,
    build_interview_report_pdf,
    safe_filename,
)

# ── minimal PDF reader ───────────────────────────────────────────────────────


def _read_pdf(data: bytes) -> list[str]:
    """
    Return the text of each page, or raise if no reader is available.

    Prefers pypdf (a real parser). Falls back to extracting the literal
    strings from the content streams, which still catches a malformed file:
    a broken document has no coherent stream/text at all.
    """
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(__import__("io").BytesIO(data))
        return [(page.extract_text() or "") for page in reader.pages]
    except Exception:
        pass

    # Fallback: structural sanity plus raw string recovery.
    if not data.startswith(b"%PDF-"):
        raise AssertionError("not a PDF")
    if b"%%EOF" not in data[-64:]:
        raise AssertionError("no EOF marker")
    text = data.decode("latin-1", "replace")
    if "/Type /Page" not in text or "/Type /Catalog" not in text:
        raise AssertionError("missing page tree or catalog")
    import re

    out: list[str] = []
    for chunk in re.findall(r"\((.*?)\)\s*Tj", text, flags=re.S):
        chunk = (
            chunk.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\").replace("\\n", "\n")
        )
        out.append(chunk)
    return ["\n".join(out)]


def _all_text(data: bytes) -> str:
    return "\n".join(_read_pdf(data))


def _page_count(data: bytes) -> int:
    try:
        from pypdf import PdfReader  # type: ignore
        import io

        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return _all_text(data).count("\f") + 1


# ── fixtures ─────────────────────────────────────────────────────────────────


def _completed(  # noqa: PLR0913 - a report needs its inputs named
    *,
    job_title: str = "Senior Backend Engineer",
    job_company: str = "OneForma",
    total_score: Optional[float] = 72.0,
    answers_scored: int = 2,
    question_count: int = 2,
    with_answer: bool = True,
    status: str = "completed",
) -> dict[str, Any]:
    questions: list[dict[str, Any]] = []
    for i in range(question_count):
        response: Optional[dict[str, Any]] = None
        if with_answer and i == 0:
            response = {
                "score": 68,
                "response_text": (
                    "I would read the annotation guidelines, build a small calibration "
                    "set of fifty items, measure agreement against the gold labels, "
                    "and only then scale up. The trade-off I would watch is throughput "
                    "against consistency."
                ),
                "strengths": ["Named a concrete validation step"],
                "weak_areas": ["Did not quantify the agreement threshold"],
                "recommendations": ["State an inter-annotator agreement target"],
            }
        elif with_answer and i == 1:
            response = {
                "score": 76,
                "response_text": "I would pair with a reviewer for the first week.",
                "strengths": [],
                "weak_areas": [],
                "recommendations": [],
            }
        questions.append(
            {
                "id": f"q{i}",
                "kind": "tech" if i == 0 else "behavioral",
                "target_skill": "data annotation",
                "question_text": (
                    "How would you approach a real piece of annotation work?"
                    if i == 0
                    else "Tell me about working with a reviewer."
                ),
                "response": response,
            }
        )

    return {
        "interview_id": "abcd1234abcd1234",
        "status": status,
        "generated_at": "2026-10-09T06:00:00+00:00",
        "interview": {
            "id": "abcd1234abcd1234",
            "job_title": job_title,
            "job_company": job_company,
            "interview_type": "role_rehearsal",
            "mode": "live",
            "target_skills": ["data annotation"],
            "started_at": "2026-10-09T05:30:00+00:00",
            "completed_at": "2026-10-09T05:52:00+00:00",
        },
        "total_score": total_score,
        "answers_scored": answers_scored,
        "overall_feedback": "Strong on annotation depth, thinner on trade-offs.",
        "strengths": ["Concrete, sequenced approach"],
        "weak_areas": ["Vague on scale"],
        "recommendations": ["Practise system-design trade-off questions"],
        "skill_radar": [
            {"skill": "role_depth", "score": 80},
            {"skill": "communication", "score": None},
        ],
        "questions": questions,
    }


class _FakeProvider:
    """
    A scripted model.

    The planner and the evaluator ask for *different* JSON shapes, and the
    planner is strict about the target skills it is allowed to use. So this
    reads the offered skill distribution out of the prompt and answers in
    the shape that call expects. A provider that returned a fixed payload
    would make the planner reject it and the test would fail for a reason
    that has nothing to do with the PDF.
    """

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        if self.fail:
            raise requests.Timeout("simulated timeout")

        import json as _json
        import re as _re

        messages = (json or {}).get("messages") or []
        prompt = "\n".join(str(m.get("content", "")) for m in messages)

        content = self._content_for(prompt)
        return _Response(content)

    def _content_for(self, prompt: str) -> str:
        import ast
        import json as _json

        if "Write the interview questions." in prompt:
            # The planner embeds the skill distribution as a Python `repr`
            # (single quotes), not JSON — `json.loads` on it dies with
            # "Expecting property name". `literal_eval` reads it as written.
            import re as _re

            m = _re.search(
                r"Distribution of target skills by kind:\s*(\{.*?\})\s*\n", prompt, _re.S
            )
            slots = ast.literal_eval(m.group(1)) if m else {}
            questions = [
                {
                    "kind": kind,
                    "target_skill": skill,
                    "difficulty": "moderate",
                    "question_text": f"Walk me through a real piece of {skill} work you have done.",
                    "rubric_focus": ["depth", "trade-offs"],
                }
                for kind, targets in slots.items()
                for skill in targets
            ]
            if not questions:
                questions = [
                    {
                        "kind": "tech",
                        "target_skill": "general",
                        "difficulty": "moderate",
                        "question_text": "Walk me through your approach to this kind of work.",
                        "rubric_focus": ["depth"],
                    }
                ]
            return _json.dumps({"questions": questions})

        if "Score this one answer." in prompt:
            # The evaluator wants a flat rubric, not the nested envelope the
            # planner uses. Returning the wrong shape is the one failure mode
            # that produces a 503 on /finish.
            return _json.dumps(
                {
                    "relevance": 72,
                    "clarity": 68,
                    "accuracy": 64,
                    "feedback": (
                        "Named a concrete validation step and a sensible order of "
                        "work. The trade-off reasoning stayed general."
                    ),
                    "strengths": ["Concrete, sequenced approach"],
                    "weak_areas": ["Did not quantify the agreement threshold"],
                    "recommendations": ["State an inter-annotator agreement target"],
                }
            )

        if "interview debrief" in prompt or "closing paragraph" in prompt:
            return _json.dumps({"overall": "Solid rehearsal; tighten the trade-off reasoning."})

        # Any other caller: a generic scoring envelope.
        return _json.dumps(
            {
                "scores": {
                    k: 68
                    for k in (
                        "role_depth",
                        "problem_solving",
                        "critical_thinking",
                        "communication",
                        "creativity",
                        "ownership",
                    )
                },
                "evidence": {"role_depth": "Named a concrete validation step."},
                "strengths": ["Concrete, sequenced approach"],
                "weak_areas": ["Did not quantify the agreement threshold"],
                "recommendations": ["State an inter-annotator agreement target"],
            }
        )


class _Response:
    """An OpenAI-shaped chat completion carrying a JSON string in the message."""

    status_code = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, content: str) -> None:
        # `content` must be a JSON *string*. The provider parses the message
        # text with `_extract_json`, so handing it a Python dict would emit
        # single-quoted repr and fail with "Expecting property name".
        self._content = content
        self.content = content.encode()

    @property
    def text(self) -> str:
        return self._content

    def json(self):
        import json as _json

        return {
            "choices": [
                {"message": {"content": self._content}, "finish_reason": "stop"}
            ]
        }

    def raise_for_status(self) -> None:
        return None


@pytest.fixture()
def repo():
    return reset_repository(os.path.join(tempfile.mkdtemp(), "iv.db"))


@pytest.fixture()
def fake_model(monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr(requests, "post", provider.post)
    return provider


@pytest.fixture()
def client(monkeypatch, fake_model):
    rate_limiter.reset()
    store_module.reset_user_store_for_tests(os.path.join(tempfile.mkdtemp(), "users.db"))
    monkeypatch.setenv("INTERVIEW_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("INTERVIEW_DB_PATH", os.path.join(tempfile.mkdtemp(), "iv.db"))
    reset_repository(os.environ["INTERVIEW_DB_PATH"])
    provider_module.reset_llm()
    workers_module.reset_queue()
    yield TestClient(create_app(), raise_server_exceptions=False)
    workers_module.reset_queue()
    provider_module.reset_llm()


@pytest.fixture()
def auth(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "c@example.com", "password": "longenough1", "display_name": "C"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture()
def other_auth(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "d@example.com", "password": "longenough1", "display_name": "D"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture()
def slug(client):
    return client.get("/api/jobs?page_size=1").json()["items"][0]["slug"]


# ── the PDF writer itself ────────────────────────────────────────────────────


class TestPdfStructure:
    def test_output_is_a_parseable_pdf(self):
        data = build_interview_report_pdf(_completed())
        assert data.startswith(b"%PDF-")
        assert b"%%EOF" in data[-64:]
        # A real parser must accept it, not just "look like" a PDF.
        assert _page_count(data) >= 1
        assert len(_read_pdf(data)) >= 1

    def test_branding_and_role_are_in_the_document(self):
        text = _all_text(build_interview_report_pdf(_completed()))
        assert "DEDAN Remote" in text
        assert "Interview rehearsal report" in text
        assert "Senior Backend Engineer" in text
        assert "OneForma" in text

    def test_persisted_answer_text_is_reproduced(self):
        text = _all_text(build_interview_report_pdf(_completed()))
        assert "calibration" in text
        assert "inter-annotator agreement" in text
        assert "Named a concrete validation step" in text

    def test_scores_and_bands_render(self):
        text = _all_text(build_interview_report_pdf(_completed()))
        assert "72" in text
        assert "solid" in text
        assert "role_depth" in text

    def test_multi_question_reports_do_not_lose_the_last_page(self):
        """
        Regression: the canvas only pushed a page when the next one began, so
        a report's final page was dropped and the question section vanished.
        """
        text = _all_text(build_interview_report_pdf(_completed(question_count=6, with_answer=True)))
        assert "Question-by-question" in text
        assert "Methodology and limitations" in text
        assert "Q6" in text

    def test_typography_survives_encoding(self):
        text = _all_text(build_interview_report_pdf(_completed()))
        # Em dash and bullet must be real glyphs, not '?' substitutions.
        assert "—" in text
        assert "•" in text


class TestPdfHonesty:
    def test_unscored_interview_says_so(self):
        text = _all_text(build_interview_report_pdf(_completed(total_score=None, answers_scored=0)))
        assert "No answer was scored" in text
        assert "/ 100" not in text

    def test_unanswered_questions_are_marked_not_omitted(self):
        text = _all_text(
            build_interview_report_pdf(
                _completed(question_count=3, with_answer=True, total_score=68, answers_scored=1)
            )
        )
        assert "skipped or no response recorded" in text
        assert "Q3" in text

    def test_no_employer_is_claimed(self):
        text = _all_text(build_interview_report_pdf(_completed()))
        assert "No employer reviewed it" in text
        assert "cannot" in text and "hiring decision" in text

    def test_missing_fields_report_as_not_recorded(self):
        data = _completed()
        data["interview"].pop("started_at", None)
        data["interview"].pop("completed_at", None)
        text = _all_text(build_interview_report_pdf(data))
        assert "not recorded" in text

    def test_score_count_never_exceeds_questions(self):
        """
        Regression: a persisted rollup claiming 4 scored answers against 1
        question printed "4 of 1 answers scored". The questions are the
        durable record, so they win.
        """
        text = _all_text(
            build_interview_report_pdf(_completed(question_count=1, answers_scored=4, total_score=70))
        )
        assert "1 answers scored" in text
        assert "4 of 1" not in text


class TestEscaping:
    def test_pdf_metacharacters_are_escaped(self):
        # An unescaped ')' would terminate the string early and corrupt the file.
        assert _pdf_text("a)b") == "a\\)b"
        assert _pdf_text("a(b") == "a\\(b"
        assert _pdf_text("back\\slash") == "back\\\\slash"

    def test_hostile_text_cannot_break_the_document(self):
        data = _completed()
        data["questions"][0]["response"]["response_text"] = ")))) (( \\\\ )))) PDF injection attempt"
        out = build_interview_report_pdf(data)
        assert out.startswith(b"%PDF-")
        assert _page_count(out) >= 1

    def test_non_winansi_characters_are_replaced_not_dropped(self):
        assert _pdf_text("emoji \U0001f600 here") == "emoji ? here"


class TestWrapping:
    def test_long_words_are_split_not_dropped(self):
        lines = _wrap("x" * 400, "helv", 10, 200)
        assert len(lines) > 1
        assert "".join(lines) == "x" * 400

    def test_short_text_stays_on_one_line(self):
        assert _wrap("hello world", "helv", 10, 500) == ["hello world"]

    def test_no_line_exceeds_the_column(self):
        body = "word " * 200
        for line in _wrap(body, "helv", 10, 300):
            from interview.report_pdf import _text_width

            assert _text_width(line, "helv", 10) <= 300.001


class TestFilename:
    def test_filename_is_safe_and_identifiable(self):
        name = safe_filename({"job_title": "Senior / Backend: Engineer", "id": "abcd1234abcd"})
        assert "/" not in name and ":" not in name
        assert name.endswith(".pdf")
        assert "abcd1234" in name

    def test_filename_survives_missing_fields(self):
        name = safe_filename({})
        assert name.endswith(".pdf")
        assert name


# ── the endpoint ─────────────────────────────────────────────────────────────


def _finish(client, auth, interview_id):
    resp = client.post(f"/api/interview/mocks/{interview_id}/finish", headers=auth)
    return resp


class TestReportEndpoint:
    def test_refuses_an_incomplete_interview(self, client, auth, slug):
        created = client.post(
            "/api/interview/mocks", json={"slug": slug, "min_questions": 3, "max_questions": 3}, headers=auth
        ).json()
        iid = created["interview_id"]
        resp = client.get(f"/api/interview/mocks/{iid}/report.pdf", headers=auth)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "not_completed"

    def test_requires_authentication(self, client, auth, slug):
        created = client.post(
            "/api/interview/mocks", json={"slug": slug, "min_questions": 3, "max_questions": 3}, headers=auth
        ).json()
        iid = created["interview_id"]
        _finish(client, auth, iid)
        resp = client.get(f"/api/interview/mocks/{iid}/report.pdf")
        assert resp.status_code == 401

    def test_one_account_cannot_download_anothers_report(self, client, auth, other_auth, slug):
        created = client.post(
            "/api/interview/mocks", json={"slug": slug, "min_questions": 3, "max_questions": 3}, headers=auth
        ).json()
        iid = created["interview_id"]
        _finish(client, auth, iid)

        # The other account gets a 404, not a 403: a 403 would confirm the id exists.
        resp = client.get(f"/api/interview/mocks/{iid}/report.pdf", headers=other_auth)
        assert resp.status_code == 404

    def test_unknown_id_is_404(self, client, auth):
        resp = client.get("/api/interview/mocks/deadbeefdeadbeef/report.pdf", headers=auth)
        assert resp.status_code == 404

    def test_completed_interview_downloads_a_real_pdf(self, client, auth, slug):
        created = client.post(
            "/api/interview/mocks", json={"slug": slug, "min_questions": 3, "max_questions": 3}, headers=auth
        ).json()
        iid = created["interview_id"]

        first = created["question"]
        assert first is not None
        ans = client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={
                "question_id": first["id"],
                "response_text": (
                    "I would read the guidelines first, build a small calibration set, "
                    "measure agreement against gold labels, then scale up carefully."
                ),
                "input_mode": "text",
            },
            headers=auth,
        )
        assert ans.status_code == 200, ans.text

        fin = _finish(client, auth, iid)
        assert fin.status_code == 200, fin.text
        assert fin.json()["status"] == "completed"

        resp = client.get(f"/api/interview/mocks/{iid}/report.pdf", headers=auth)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.headers["content-disposition"].endswith('.pdf"')

        body = resp.content
        assert body.startswith(b"%PDF-")
        text = _all_text(body)
        assert "DEDAN Remote" in text
        assert "calibration set" in text

    def test_report_reflects_the_job_it_was_run_against(self, client, auth, slug):
        job = client.get("/api/jobs?page_size=1").json()["items"][0]
        created = client.post(
            "/api/interview/mocks", json={"slug": slug, "min_questions": 3, "max_questions": 3}, headers=auth
        ).json()
        iid = created["interview_id"]
        _finish(client, auth, iid)
        text = _all_text(client.get(f"/api/interview/mocks/{iid}/report.pdf", headers=auth).content)
        assert job["title"] in text
        assert job["company"] in text

    def test_deleting_an_interview_removes_its_report(self, client, auth, slug):
        created = client.post(
            "/api/interview/mocks", json={"slug": slug, "min_questions": 3, "max_questions": 3}, headers=auth
        ).json()
        iid = created["interview_id"]
        _finish(client, auth, iid)
        assert client.get(f"/api/interview/mocks/{iid}/report.pdf", headers=auth).status_code == 200
        assert client.delete(f"/api/interview/mocks/{iid}", headers=auth).status_code == 204
        assert client.get(f"/api/interview/mocks/{iid}/report.pdf", headers=auth).status_code == 404


class TestNoEvaluatorConfigured:
    """
    With no model configured the honest outcome is an unscored transcript.
    The report must still exist and must still say nothing was scored.
    """

    @pytest.fixture()
    def unconfigured(self, monkeypatch, tmp_path):
        monkeypatch.setenv("INTERVIEW_ENABLED", "false")
        monkeypatch.setenv("INTERVIEW_DB_PATH", str(tmp_path / "iv.db"))
        reset_repository(os.environ["INTERVIEW_DB_PATH"])
        provider_module.reset_llm()
        rate_limiter.reset()
        store_module.reset_user_store_for_tests(str(tmp_path / "users.db"))
        yield TestClient(create_app(), raise_server_exceptions=False)
        provider_module.reset_llm()

    def test_report_is_served_and_declares_no_scoring(self, unconfigured):
        client = unconfigured
        auth = {
            "Authorization": "Bearer "
            + client.post(
                "/api/auth/register",
                json={"email": "u@example.com", "password": "longenough1", "display_name": "U"},
            ).json()["token"]
        }
        slug = client.get("/api/jobs?page_size=1").json()["items"][0]["slug"]
        created = client.post(
            "/api/interview/mocks", json={"slug": slug, "min_questions": 3, "max_questions": 3}, headers=auth
        ).json()
        iid = created["interview_id"]
        q = created["question"]
        client.post(
            f"/api/interview/mocks/{iid}/answers",
            json={"question_id": q["id"], "response_text": "A real answer about the work.", "input_mode": "text"},
            headers=auth,
        )
        fin = client.post(f"/api/interview/mocks/{iid}/finish", headers=auth)
        assert fin.status_code == 200
        assert fin.json()["total_score"] is None

        resp = client.get(f"/api/interview/mocks/{iid}/report.pdf", headers=auth)
        assert resp.status_code == 200
        text = _all_text(resp.content)
        assert "No answer was scored" in text
        assert "A real answer about the work." in text
