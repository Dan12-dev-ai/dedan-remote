"""
DEDAN Remote — FastAPI application.

Public HTTP API over the AIJobFinder discovery engine. Serves the REST API
and (when built) the static frontend with SPA fallback.

Run:  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse

from api import services
from api.deps import ApiError
from api.routers import auth, discovery, jobs, meta, system, user
from api.store import get_user_store
from config.settings import get_settings

logger = logging.getLogger("dedan.api")

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Static assets emitted by Vite are content-hashed, so they can be cached
# forever. Anything else (index.html, favicons) must be revalidated so a new
# deployment is picked up immediately.
IMMUTABLE_ASSET_PREFIX = "/assets/"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Startup/shutdown work for the API process.

    Startup is deliberately defensive: housekeeping failures must degrade the
    service (logged) rather than prevent it from serving read-only traffic.
    """
    settings = get_settings()
    if not settings.DEDAN_SYSTEM_TOKEN:
        if settings.is_production:
            logger.warning(
                "DEDAN_SYSTEM_TOKEN is unset — /api/system is disabled."
            )
    try:
        removed = get_user_store().purge_expired_sessions()
        if removed:
            logger.info("Purged %s expired session(s) at startup.", removed)
    except Exception:
        logger.exception("Session purge failed at startup; continuing.")

    # Warm read-only aggregates so the first public request is already fast.
    try:
        services.invalidate_caches()
        services.get_stats()
    except Exception:
        logger.warning("Cache warm-up skipped: discovery DB unavailable.")

    logger.info("DEDAN Remote API ready (env=%s).", settings.DEDAN_ENV)
    yield
    logger.info("DEDAN Remote API shutting down.")



def _versioned_alias(router: APIRouter) -> APIRouter:
    """
    Mirror a router's routes under /api/v1.

    FastAPI *prepends* prefixes on include, so re-including an already
    prefixed router would nest ``/api/v1/api/...``. Rewriting each route path
    keeps exactly one implementation per handler while exposing a versioned
    surface. The alias is excluded from the OpenAPI schema so the docs keep
    listing one canonical path per operation.
    """
    alias = APIRouter(tags=list(router.tags))
    for route in router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or not path.startswith("/api"):
            continue
        alias.add_api_route(
            "/api/v1" + path[len("/api"):],
            route.endpoint,
            methods=sorted(methods),
            response_model=getattr(route, "response_model", None),
            status_code=getattr(route, "status_code", None),
            dependencies=list(getattr(route, "dependencies", None) or []),
            summary=getattr(route, "summary", None),
            description=getattr(route, "description", None),
            response_class=getattr(route, "response_class", None),
            name=route.name,
            include_in_schema=False,
        )
    return alias


def create_app() -> FastAPI:
    app = FastAPI(
        title="DEDAN Remote API",
        description=(
            "Public API for DEDAN Remote — intelligent global opportunity "
            "discovery. Read layer over the AIJobFinder discovery engine."
        ),
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    settings = get_settings()
    origins = [
        o.strip()
        for o in settings.DEDAN_CORS_ORIGINS.split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,  # bearer tokens, not cookies
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-System-Token"],
        expose_headers=["X-Request-ID"],
    )

    # Compress JSON responses (job lists and detail payloads are large).
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """
        Single ASGI pass that adds request correlation, security headers and a
        request-size guard.

        Doing all three here (rather than in three stacked middlewares) keeps
        one layer of overhead on the hottest path in the service.
        """
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]

        # Reject oversized bodies before they are read into memory.
        declared = request.headers.get("content-length")
        if declared:
            try:
                too_big = int(declared) > settings.DEDAN_MAX_BODY_BYTES
            except ValueError:
                too_big = True
            if too_big:
                return JSONResponse(
                    status_code=413,
                    content={"error": {
                        "code": "payload_too_large",
                        "message": "Request body is too large.",
                    }},
                    headers={"X-Request-ID": request_id},
                )

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        response.headers["X-Request-ID"] = request_id
        # Baseline hardening headers. Safe for a JSON API and the SPA bundle.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if settings.DEDAN_ENABLE_HSTS:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        if request.url.path.startswith("/api"):
            logger.info(
                "%s %s -> %s (%.0fms) rid=%s",
                request.method, request.url.path,
                response.status_code, duration * 1000, request_id,
            )
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        # Carry through per-error headers (e.g. Retry-After on a 429).
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
            headers=exc.headers,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        # Consistent structured errors for API paths (incl. 404/405).
        if request.url.path.startswith("/api"):
            detail = exc.detail
            if isinstance(detail, dict) and "code" in detail:
                payload = detail
            else:
                code = {
                    404: "not_found",
                    405: "method_not_allowed",
                }.get(exc.status_code, "http_error")
                message = (
                    detail if isinstance(detail, str)
                    else "Request could not be completed."
                )
                payload = {"code": code, "message": message}
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": payload},
                headers=getattr(exc, "headers", None),
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request,
                                       exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = first.get("msg", "Invalid request.")
        return JSONResponse(
            status_code=422,
            content={"error": {
                "code": "validation_error",
                "message": f"{loc}: {message}" if loc else message,
            }},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        # Log full traceback server-side; never expose it to clients.
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {
                "code": "internal_error",
                "message": "Something went wrong. Please try again.",
            }},
        )

    app.include_router(jobs.router)
    app.include_router(meta.router)
    app.include_router(auth.router)
    app.include_router(user.router)
    app.include_router(discovery.router)
    app.include_router(system.router)

    # Versioned mirror of the public surface. The unversioned /api paths stay
    # canonical (existing clients and tests depend on them); /api/v1 exists so
    # future breaking changes have somewhere to land without a rewrite.
    for module in (jobs, meta, auth, user, discovery, system):
        app.include_router(_versioned_alias(module.router))

    # /api/health and /api/ready are declared on the app itself (they must
    # answer even when router wiring is broken), so they need explicit aliases.
    @app.get("/api/v1/health", tags=["meta"], include_in_schema=False)
    def health_v1() -> dict[str, str]:
        return {"status": "ok", "service": "dedan-remote", "version": "1.0.0"}

    @app.get("/api/v1/ready", tags=["meta"], include_in_schema=False)
    def ready_v1() -> JSONResponse:
        ok, detail = services.probe_jobs_db()
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ready" if ok else "not_ready",
                "database": {"ok": ok, "detail": detail},
            },
        )

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        """Liveness: the process is up and serving (no dependencies checked)."""
        return {"status": "ok", "service": "dedan-remote", "version": "1.0.0"}

    @app.get("/api/ready", tags=["meta"])
    def ready() -> JSONResponse:
        """
        Readiness: can this instance actually answer questions?

        Unlike /api/health this touches the discovery database, so orchestrators
        (Docker, Kubernetes, load balancers) can keep traffic away from an
        instance whose data files are missing or unreadable.
        """
        ok, detail = services.probe_jobs_db()
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "status": "ready" if ok else "not_ready",
                "database": {"ok": ok, "detail": detail},
            },
        )

    # Explicit catch-all for unmatched /api/* requests. Newer Starlette
    # versions do not route unmatched-path 404s through HTTPException
    # handlers, so we register this AFTER all real API routes.
    @app.api_route(
        "/api/{unmatched:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def api_unmatched(unmatched: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found",
                               "message": "API endpoint not found."}},
        )

    # ── Static frontend (production build) ─────────────────────────────────
    # Catch-all registered AFTER API routes: serves real files from dist/,
    # falls back to index.html for SPA client-side routes, 404s otherwise.
    if FRONTEND_DIST.is_dir():
        dist_root = FRONTEND_DIST.resolve()

        @app.api_route(
            "/{full_path:path}",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        def spa_fallback(full_path: str):
            # Serve the real file when it exists (bounded to dist/ so no
            # traversal is possible via `..` in the path).
            candidate = (dist_root / full_path).resolve()
            if (str(candidate).startswith(str(dist_root))
                    and candidate.is_file()):
                headers: dict[str, str] = {}
                if full_path.startswith(IMMUTABLE_ASSET_PREFIX.lstrip("/")):
                    # Vite content-hashes these filenames -> safe forever.
                    headers["Cache-Control"] = (
                        "public, max-age=31536000, immutable"
                    )
                else:
                    headers["Cache-Control"] = "no-cache"
                return FileResponse(candidate, headers=headers)

            # A missing file (anything with an extension) is a real 404 — do
            # not disguise it as the SPA shell, or broken assets get cached as
            # HTML by browsers and CDNs.
            last_segment = full_path.rsplit("/", 1)[-1]
            if "." in last_segment:
                return JSONResponse(
                    status_code=404,
                    content={"error": {"code": "not_found",
                                       "message": "Asset not found."}},
                )

            index = dist_root / "index.html"
            if index.is_file():
                return FileResponse(
                    index, headers={"Cache-Control": "no-cache"},
                )
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found",
                                   "message": "Not found."}},
            )

    return app


app = create_app()
