"""
Agentic Digital Twin — FastAPI Application Entry Point.

Configures logging, creates database tables, registers routes,
serves the frontend dashboard, and provides structured error handling.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, APP_VERSION
from app.core.logger import setup_logging, get_logger
from app.database import Base, engine

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
    logger.info("Creating database tables (if not exist)...")
    Base.metadata.create_all(bind=engine)
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


# ── CORS middleware (allows frontend to call API) ────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global exception handlers ───────────────────────────────
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


# ── Register API routes ─────────────────────────────────────
app.include_router(api_router, prefix="/api")


# ── Serve frontend static files ─────────────────────────────
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

