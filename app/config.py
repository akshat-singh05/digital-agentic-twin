"""
Application configuration.

Centralised settings so every module reads from one place.
All tunables are environment-variable overridable for deployment flexibility.
"""

import os
import logging

# ── Database ─────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./digital_twin.db")

# ── Application ──────────────────────────────────────────────
APP_NAME = "Agentic Digital Twin"
APP_VERSION = "1.0.0"
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

# ── Logging ──────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG" if DEBUG else "INFO").upper()
LOG_FORMAT = "%(asctime)s │ %(levelname)-8s │ %(name)-30s │ %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def configure_logging():
    """
    Set up application-wide logging.

    Call once at application startup.  All modules use
    ``logging.getLogger(__name__)`` which inherits this config.
    """
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )
    # Quieten noisy third-party loggers
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

