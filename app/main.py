"""
Agentic Digital Twin — FastAPI Application Entry Point.

Configures logging, applies database migrations, registers routes,
serves the frontend dashboard, and provides structured error handling.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import APP_NAME, APP_VERSION, CORS_ORIGINS, SECRET_KEY_IS_EPHEMERAL
from app.core import rate_limit
from app.core.logger import setup_logging, get_logger
from app.database import engine
from app.db_migrations import migrate_to_head

# Configure logging FIRST — before any module-level getLogger() calls propagate
setup_logging()

logger = get_logger(__name__)

# Import models so SQLAlchemy registers all tables with Base.metadata
import app.models  # noqa: F401

from app.api.routes import router as api_router

# ── Paths ────────────────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")


# ── Application lifespan ────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hook."""
    logger.info("Applying database migrations...")
    migrate_to_head()
    if SECRET_KEY_IS_EPHEMERAL:
        logger.warning(
            "SECRET_KEY is not set — using a random key for this process; "
            "access tokens stop working on restart. Set SECRET_KEY to keep them."
        )
    logger.info(
        "%s v%s started successfully — ready to accept requests",
        APP_NAME, APP_VERSION,
    )
    yield
    logger.info("Shutting down %s", APP_NAME)


# ── FastAPI application ──────────────────────────────────────
app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "An intelligent agent that autonomously manages user subscriptions "
        "by analyzing usage patterns, negotiating better plans, ensuring "
        "user privacy, and maintaining explainable audit logs."
    ),
    lifespan=lifespan,
)


# ── Rate limiting (per client IP; see app/core/rate_limit.py) ─
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if rate_limit.uses_redis():          # a network call: keep it off the event loop
        retry_after = await run_in_threadpool(rate_limit.check, client_ip, request.url.path)
    else:
        retry_after = rate_limit.check(client_ip, request.url.path)
    if retry_after:
        return JSONResponse(
            status_code=429,
            content={"status": "error", "message": "Too many requests; slow down and retry later"},
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


# ── Dashboard files: always revalidate, so an upgrade is never half-cached ──
@app.middleware("http")
async def no_stale_dashboard(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ── Security headers ─────────────────────────────────────────
# The dashboard still uses inline event handlers, so its script-src has to allow
# 'unsafe-inline'; the rest of the policy (no framing, no plugins, no foreign
# connections, fixed base URI and form targets) still applies.  /docs loads its
# assets from a CDN, so the policy is sent only with the dashboard itself.
_DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    headers = response.headers
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if path == "/" or path.startswith("/static/"):
        headers["Content-Security-Policy"] = _DASHBOARD_CSP
    if path.startswith("/api/"):
        headers["Cache-Control"] = "no-store"          # tokens and personal data are never cached
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── CORS: only configured origins; tokens travel in a header, not cookies ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Global exception handlers ───────────────────────────────
# Every error leaves the API as {"status": "error", "message": "..."}.
@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """Unwrap HTTPException details (routes pass the envelope as detail)."""
    detail = exc.detail
    message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
    content = {"status": "error", "message": message}
    if isinstance(detail, dict) and "errors" in detail:
        content["errors"] = detail["errors"]        # e.g. the problems found in an import file
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    """Flatten Pydantic validation errors into the standard envelope."""
    errors = [
        {"loc": list(err.get("loc", [])), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    message = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'] if p != 'body') or 'body'}: {err['msg']}"
        for err in errors
    )
    return JSONResponse(
        status_code=422,
        content={"status": "error", "message": message, "errors": errors},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Catch unhandled ValueErrors and return a clean 422 response."""
    logger.warning("ValueError on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content={"status": "error", "message": str(exc)},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    """Catch unexpected exceptions and return a clean 500 response."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "An internal error occurred. Please try again later.",
        },
    )


# ── Root endpoint — serve the dashboard ──────────────────────
@app.get("/", tags=["System"])
def root():
    """Serve the frontend dashboard."""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": "Agentic Digital Twin API Running",
        "version": APP_VERSION,
    }


# ── Health check (unauthenticated; for load balancers and containers) ──
@app.get("/health", tags=["System"])
def health():
    """200 when the app can reach its database, 503 otherwise."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check: database unreachable")
        return JSONResponse(status_code=503, content={"status": "error", "message": "database unavailable"})
    return {"status": "success", "version": APP_VERSION}


# ── Register API routes ─────────────────────────────────────
app.include_router(api_router, prefix="/api")


# ── Serve frontend static files ─────────────────────────────
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

