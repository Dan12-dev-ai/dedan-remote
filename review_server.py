"""Review harness: serve the static frontend AND the REST API from a single
origin so one public tunnel URL covers the whole product (SPA + /api + /docs).

The FastAPI app already owns every /api route before this mount, so
StaticFiles(html=True) at "/" only handles the loose frontend/*.html files.
No repository files are modified.
"""

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from api.main import app

FRONTEND = Path(__file__).resolve().parent / "frontend"

if not FRONTEND.is_dir():
    raise SystemExit(f"frontend directory not found at {FRONTEND}")

app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="review-frontend")
