"""
Interview report PDF — a real, generated PDF from persisted results.

Design constraints that shaped this module:

* **No new dependency.** reportlab is not in requirements.txt and adding a
  compiled wheel to a deployment that currently installs nothing but pure
  Python is a supply-chain decision, not a feature. So this writes a minimal
  PDF 1.4 document directly: correct header, xref table, page tree, and
  content streams. That is enough for a text report and it is verifiable —
  `test_interview_report_pdf.py` parses the output back with a real PDF
  reader, so "it is a valid PDF" is checked, not asserted.

* **Text only, escaped.** Every string is passed through `_pdf_text`, which
  escapes the four characters that are meaningful inside a PDF string
  literal and drops non-Latin-1 bytes. Report content is candidate and
  model-generated text; it never goes into the document unescaped.

* **Honest content.** The report carries whatever the session actually
  recorded, including "not assessed" and "not scored". It never fills a
  missing section with invented text.

The output is buffered in memory and streamed back by the router, so a
report can never be half-written to a shared path.
"""

from __future__ import annotations

import io
import re
from typing import Any, Iterable, Optional

# Brand palette, kept in step with the web design tokens.
IVORY = (0.992, 0.976, 0.949)
ESPRESSO = (0.149, 0.098, 0.071)
COCOA = (0.310, 0.271, 0.255)
GOLD = (0.639, 0.498, 0.325)

PAGE_W, PAGE_H = 595.28, 841.89  # A4 portrait, points
MARGIN = 56.0
CONTENT_W = PAGE_W - 2 * MARGIN

# A character-width table for the three Helvetica faces we use. Real PDFs
# embed fonts; here we approximate glyph widths so wrapping is close enough
# that no line silently runs off the page. Values are per-1000 units.
_WIDTHS: dict[str, dict[str, int]] = {
    "helv": {},
    "hebo": {},
}

# Helvetica / Helvetica-Bold standard widths (Adobe AFM), condensed to the
# characters a report actually contains.
_HELV = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
_HELV_W = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]
_HEBO_W = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
]
_WIDTHS["helv"] = dict(zip(_HELV, _HELV_W))
_WIDTHS["hebo"] = dict(zip(_HELV, _HEBO_W))
_DEFAULT_W = {"helv": 556, "hebo": 556}

# Typographic characters this report uses, mapped to their WinAnsiEncoding
# byte values. Without this the PDF writer would substitute '?' for them.
_WINANSI: dict[str, int] = {
    "\u2014": 0x97,  # em dash
    "\u2013": 0x96,  # en dash
    "\u2018": 0x91,  # left single quote
    "\u2019": 0x92,  # right single quote
    "\u201c": 0x93,  # left double quote
    "\u201d": 0x94,  # right double quote
    "\u2022": 0x95,  # bullet
    "\u2026": 0x85,  # ellipsis
    "\u00a0": 0x20,  # non-breaking space -> plain space
}


def _char_width(ch: str, font: str) -> float:
    return _WIDTHS[font].get(ch, _DEFAULT_W[font]) / 1000.0


def _text_width(text: str, font: str, size: float) -> float:
    return sum(_char_width(c, font) for c in text) * size


def _wrap(text: str, font: str, size: float, max_width: float) -> list[str]:
    """Greedy word wrap. Long unbroken tokens are hard-split, never dropped."""
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if _text_width(candidate, font, size) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        # A single word wider than the column: split it by characters.
        while _text_width(word, font, size) > max_width and len(word) > 1:
            cut = len(word)
            while cut > 1 and _text_width(word[:cut], font, size) > max_width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


def _pdf_text(value: Any) -> str:
    """
    Make a value safe to place in a PDF literal string.

    Two jobs here:
    1. Escape the PDF string metacharacters.
    2. Map the typographic characters we emit onto their WinAnsiEncoding
       byte values, so '—' and '•' render as real glyphs instead of '?'.
       Anything genuinely outside WinAnsi (emoji, CJK) becomes '?' rather
       than corrupting the file.
    """
    s = "" if value is None else str(value)
    out = []
    for ch in s:
        if ch in ("\\", "(", ")"):
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            continue
        elif ch == "\t":
            out.append("    ")
        elif ord(ch) < 32:
            continue
        elif ch in _WINANSI:
            out.append(chr(_WINANSI[ch]))
        elif ord(ch) > 255:
            out.append("?")
        else:
            out.append(ch)
    return "".join(out)


class _Canvas:
    """Collects drawing ops per page, then serialises them to content streams."""

    def __init__(self) -> None:
        self.pages: list[list[str]] = []
        self.page: list[str] = []
        self.y = PAGE_H - MARGIN
        self.bottom = MARGIN + 28  # leave room for the footer

    # -- lifecycle -------------------------------------------------
    def new_page(self) -> None:
        self.pages.append(self.page)
        self.page = []
        self.y = PAGE_H - MARGIN

    def ensure(self, needed: float) -> None:
        if self.y - needed < self.bottom:
            self.new_page()

    # -- primitives -------------------------------------------------
    def fill(self, rgb: tuple[float, float, float], x: float, y: float, w: float, h: float) -> None:
        r, g, b = rgb
        self.page.append(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def text(self, x: float, y: float, body: str, font: str, size: float,
             rgb: tuple[float, float, float] = COCOA) -> None:
        # The content stream must reference the resource names declared in the
        # page dictionary (/F1, /F2), not the internal font keys.
        tag = "F2" if font == "hebo" else "F1"
        r, g, b = rgb
        self.page.append(
            f"BT {r:.3f} {g:.3f} {b:.3f} rg /{tag} {size:.1f} Tf "
            f"1 0 0 1 {x:.2f} {y:.2f} Tm ({_pdf_text(body)}) Tj ET"
        )

    def rule(self, y: float, rgb: tuple[float, float, float] = (0.85, 0.80, 0.75), w: float = 0.7) -> None:
        r, g, b = rgb
        self.page.append(
            f"{r:.3f} {g:.3f} {b:.3f} RG {w:.2f} w "
            f"{MARGIN:.2f} {y:.2f} m {PAGE_W - MARGIN:.2f} {y:.2f} l S"
        )

    # -- composites -------------------------------------------------
    def heading(self, label: str, size: float = 15) -> None:
        self.ensure(size + 14)
        self.text(MARGIN, self.y - size, label, "hebo", size, ESPRESSO)
        self.y -= size + 6
        self.rule(self.y)
        self.y -= 14

    def paragraph(self, body: str, size: float = 10, font: str = "helv",
                  color: tuple[float, float, float] = COCOA, indent: float = 0.0,
                  gap: float = 5) -> None:
        leading = size * 1.45
        for line in _wrap(body, font, size, CONTENT_W - indent):
            self.ensure(leading)
            self.text(MARGIN + indent, self.y - size, line, font, size, color)
            self.y -= leading
        self.y -= gap

    def bullets(self, items: Iterable[str], size: float = 10, empty_note: str = "—") -> None:
        items = [i for i in items if i]
        if not items:
            self.paragraph(empty_note, size=size, color=COCOA, indent=14)
            return
        leading = size * 1.4
        for item in items:
            lines = _wrap(item, "helv", size, CONTENT_W - 26)
            for idx, line in enumerate(lines):
                self.ensure(leading)
                if idx == 0:
                    self.text(MARGIN + 6, self.y - size, "\u2022", "helv", size, GOLD)
                self.text(MARGIN + 20, self.y - size, line, "helv", size, COCOA)
                self.y -= leading
            self.y -= 2.5
        self.y -= 4

    def kv_row(self, key: str, value: str) -> None:
        self.ensure(15)
        self.text(MARGIN, self.y - 9.5, key, "hebo", 9.5, ESPRESSO)
        lines = _wrap(value, "helv", 9.5, CONTENT_W - 170)
        for idx, line in enumerate(lines[:2]):
            self.text(MARGIN + 170, self.y - 9.5, line, "helv", 9.5, COCOA)
            if idx == 0:
                pass
            self.y -= 12.5
        if len(lines) > 2:
            self.text(MARGIN + 170, self.y - 9.5, "…", "helv", 9.5, COCOA)
            self.y -= 12.5
        self.y -= 3


def _score_band(score: Optional[float]) -> str:
    if score is None:
        return "not scored"
    if score >= 85:
        return "strong"
    if score >= 70:
        return "solid"
    if score >= 55:
        return "developing"
    return "needs work"


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "not recorded"
    return f"{value}{suffix}"


def build_interview_report_pdf(data: dict[str, Any]) -> bytes:
    """
    Render a completed interview's persisted results as a PDF.

    `data` is the transcript/report dict from `GET /api/interview/mocks/{id}`
    plus the caller's display name. Nothing here invents a value: every field
    has a "not recorded" path.
    """
    interview = data.get("interview") or {}
    questions = data.get("questions") or []
    status = str(data.get("status") or "")
    generated = str(data.get("generated_at") or "")[:19].replace("T", " ") + " UTC"

    c = _Canvas()

    # ── Cover band ────────────────────────────────────────────────
    band_h = 96
    c.fill(ESPRESSO, 0, PAGE_H - band_h, PAGE_W, band_h)
    c.text(MARGIN, PAGE_H - 40, "DEDAN Remote", "hebo", 20, IVORY)
    c.text(MARGIN, PAGE_H - 58, "Interview rehearsal report", "helv", 11, (0.85, 0.76, 0.72))
    c.text(MARGIN, PAGE_H - 76, _fmt(interview.get("job_title"), ""), "hebo", 12, IVORY)
    c.text(MARGIN, PAGE_H - 90, _fmt(interview.get("job_company"), ""), "helv", 10, (0.85, 0.76, 0.72))
    c.y = PAGE_H - band_h - 26

    # ── Honesty banner ────────────────────────────────────────────
    c.fill((0.976, 0.949, 0.906), MARGIN, c.y - 46, CONTENT_W, 46)
    c.text(MARGIN + 12, c.y - 16, "This is a self-directed rehearsal. No employer reviewed it.", "hebo", 9.5, ESPRESSO)
    for i, line in enumerate(_wrap(
        "Scores below come from the rubric and the configured evaluation model "
        "applied to your own answers. They are practice feedback only and carry "
        "no weight with any employer.", "helv", 8.8, CONTENT_W - 24
    )[:2]):
        c.text(MARGIN + 12, c.y - 29 - i * 11, line, "helv", 8.8, COCOA)
    c.y -= 46 + 20

    # ── Session facts ─────────────────────────────────────────────
    c.heading("Session")
    c.kv_row("Role", _fmt(interview.get("job_title")))
    c.kv_row("Organisation", _fmt(interview.get("job_company")))
    c.kv_row("Interview type", _fmt(interview.get("interview_type") or interview.get("mode")))
    c.kv_row("Questions asked", str(len(questions)))
    c.kv_row("Completion", "Completed" if status == "completed" else f"Not completed ({status or 'unknown'})")
    c.kv_row("Started", _fmt((interview.get("started_at") or "")[:19].replace("T", " ")))
    c.kv_row("Completed at", _fmt((interview.get("completed_at") or "")[:19].replace("T", " ")))
    c.kv_row("Report generated", _fmt(generated))
    c.kv_row("Report ID", _fmt(interview.get("id") or data.get("interview_id")))
    c.y -= 6

    # ── Overall result ────────────────────────────────────────────
    c.heading("Overall result")
    total = data.get("total_score")
    scored = data.get("answers_scored")
    # A report must never contradict itself. If the persisted rollup claims
    # more scored answers than there are questions, trust the questions —
    # they are the durable record — rather than printing "4 of 1".
    asked = len(questions)
    if isinstance(scored, int) and asked and scored > asked:
        scored = asked
    if total is None:
        c.paragraph(
            "No answer was scored by an evaluation model, so no overall score is "
            "reported. The transcript and per-question notes below are the record.",
            color=ESPRESSO,
        )
    else:
        c.text(MARGIN, c.y - 26, f"{total:.0f}", "hebo", 26, ESPRESSO)
        c.text(MARGIN + _text_width(f"{total:.0f}", "hebo", 26) + 8, c.y - 26, "/ 100", "helv", 11, COCOA)
        c.text(MARGIN, c.y - 44,
               f"{_score_band(total)} · {_fmt(scored)} of {asked} answers scored",
               "helv", 9.5, COCOA)
        c.y -= 58

    overall = data.get("overall_feedback")
    if overall:
        c.paragraph(str(overall))

    # ── Competencies ──────────────────────────────────────────────
    radar = data.get("skill_radar") or []
    c.heading("Competency breakdown")
    if not radar:
        c.paragraph("No competency scores were recorded for this session.")
    else:
        bar_max = 150.0
        for entry in radar:
            name = str(entry.get("skill") or entry.get("name") or "—")
            value = entry.get("score")
            c.ensure(24)
            c.text(MARGIN, c.y - 9, name, "hebo", 9.5, ESPRESSO)
            label = "not scored" if value is None else f"{value:.0f}"
            c.text(PAGE_W - MARGIN - _text_width(label, "hebo", 9.5), c.y - 9, label, "hebo", 9.5, COCOA)
            c.y -= 15
            c.fill((0.906, 0.886, 0.859), MARGIN, c.y - 5, bar_max, 5)
            if value is not None:
                width = max(2.0, min(bar_max, bar_max * (float(value) / 100.0)))
                c.fill(GOLD, MARGIN, c.y - 5, width, 5)
            c.y -= 17
        c.y -= 4

    # ── Strengths / improvements ──────────────────────────────────
    c.heading("Strengths — evidence from your answers")
    c.bullets(data.get("strengths") or [], empty_note="No strengths were recorded for this session.")

    c.heading("Areas to improve")
    c.bullets(data.get("weak_areas") or [], empty_note="No specific weaknesses were recorded.")

    c.heading("Recommended practice")
    c.bullets(data.get("recommendations") or [], empty_note="No practice recommendations were generated.")

    # ── Per-question detail ───────────────────────────────────────
    c.heading("Question-by-question")
    if not questions:
        c.paragraph("No questions were recorded for this session.")
    for idx, q in enumerate(questions, 1):
        resp = q.get("response") or {}
        c.ensure(70)
        c.text(MARGIN, c.y - 11, f"Q{idx} · {_fmt(q.get('kind'))} · target: {_fmt(q.get('target_skill'))}",
               "hebo", 10, ESPRESSO)
        c.y -= 16
        c.paragraph(str(q.get("question_text") or ""), size=9.5, indent=8)
        score = resp.get("score")
        c.text(MARGIN + 8, c.y - 9,
               f"Score: {('not scored' if score is None else f'{score:.0f}/100')}",
               "hebo", 9.5, GOLD)
        c.y -= 16
        answer = str(resp.get("response_text") or "").strip()
        if answer:
            c.paragraph("Your answer: " + answer, size=9, indent=8)
        else:
            c.paragraph("Your answer: (skipped or no response recorded)", size=9, indent=8)
        if resp.get("strengths"):
            c.bullets(resp["strengths"], size=9, empty_note="")
        if resp.get("weak_areas"):
            c.bullets(resp["weak_areas"], size=9, empty_note="")
        if resp.get("recommendations"):
            c.bullets(resp["recommendations"], size=9, empty_note="")
        c.y -= 4

    # ── Methodology & limits ──────────────────────────────────────
    c.heading("Methodology and limitations")
    c.paragraph(
        "Each answer was scored against the DEDAN Remote interview rubric across the "
        "competencies listed above, using the evaluation model configured by this "
        "deployment. Where no model was configured, answers are unscored and the "
        "transcript is reproduced without judgement.", size=9.5)
    c.paragraph(
        "Questions were selected from the skills and requirements published in the "
        "original opportunity listing. A rehearsal of this kind measures how you "
        "explain your experience; it does not measure employability, and it cannot "
        "predict any hiring decision.", size=9.5)
    c.paragraph(
        "Your answers were sent to the evaluation model to be scored. Audio was not "
        "recorded or retained. The transcript and this report are stored for the "
        "retention period disclosed in the interview room and can be deleted by you "
        "at any time from your interview history.", size=9.5)

    # ── Serialise ─────────────────────────────────────────────────
    # Flush the page still being drawn. `new_page` only pushes a page when
    # the *next* one starts, so without this the final page's ops would be
    # silently dropped and the report would end mid-section.
    c.new_page()

    # Object map is fixed up front so every cross-reference is correct:
    #   1 catalog, 2 pages tree, 3 helvetica, 4 helvetica-bold,
    #   5..4+N page objects, 5+N..4+2N content streams.
    n = len(c.pages)
    page_obj_first = 5
    content_obj_first = page_obj_first + n

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    # Ordered (id, body) pairs. Pages and streams must be built before the
    # Pages tree, because the tree lists their object numbers.
    page_bodies: list[bytes] = []
    for i, _ops in enumerate(c.pages):
        page_bodies.append((
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
            "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>"
            % (PAGE_W, PAGE_H, content_obj_first + i)
        ).encode("latin-1"))

    stream_bodies: list[bytes] = []
    for page_ops in c.pages:
        raw = "\n".join(page_ops).encode("latin-1", "replace")
        stream_bodies.append(b"<< /Length %d >>\nstream\n" % len(raw) + raw + b"\nendstream")

    objects: list[tuple[int, bytes]] = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, ("<< /Type /Pages /Kids [%s] /Count %d >>"
             % (" ".join(f"{page_obj_first + i} 0 R" for i in range(n)), n)).encode("latin-1")),
        (3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"),
        (4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"),
    ]
    for i, body in enumerate(page_bodies):
        objects.append((page_obj_first + i, body))
    for i, body in enumerate(stream_bodies):
        objects.append((content_obj_first + i, body))

    offsets: dict[int, int] = {}
    for obj_id, body in objects:
        offsets[obj_id] = out.tell()
        out.write(b"%d 0 obj\n" % obj_id)
        out.write(body)
        out.write(b"\nendobj\n")

    xref_at = out.tell()
    max_id = max(offsets)
    out.write(b"xref\n0 %d\n" % (max_id + 1))
    out.write(b"0000000000 65535 f \n")
    for obj_id in range(1, max_id + 1):
        if obj_id in offsets:
            out.write(b"%010d 00000 n \n" % offsets[obj_id])
        else:
            # A gap must still occupy a row, or every later offset is wrong.
            out.write(b"0000000000 65535 f \n")
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (max_id + 1, xref_at))

    return out.getvalue()


def safe_filename(interview: dict[str, Any]) -> str:
    """A download name that is safe on every OS and identifiable."""
    title = re.sub(r"[^A-Za-z0-9]+", "-", str(interview.get("job_title") or "interview")).strip("-")
    ident = str(interview.get("id") or interview.get("interview_id") or "session")[:8]
    return f"dedan-interview-{title.lower()[:40] or 'report'}-{ident}.pdf"
