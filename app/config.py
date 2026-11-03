"""
Application configuration.

Centralised settings so every module reads from one place.
All tunables are environment-variable overridable for deployment flexibility.
"""

import logging
import os
import secrets

# ── Database ─────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./digital_twin.db")

# ── Application ──────────────────────────────────────────────
APP_NAME = "Agentic Digital Twin"
APP_VERSION = "1.0.0"
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")

# ── Security ─────────────────────────────────────────────────
# Signing key for access tokens.  Required outside DEBUG; in DEBUG a random
# key is generated per process (tokens then stop working on restart).
SECRET_KEY = os.getenv("SECRET_KEY") or ""
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("SECRET_KEY must be set when DEBUG is off")
    SECRET_KEY = secrets.token_urlsafe(48)
    SECRET_KEY_IS_EPHEMERAL = True
else:
    SECRET_KEY_IS_EPHEMERAL = False

ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))

# Browser origins allowed to call the API (comma-separated).  The bundled
# dashboard is served from the API's own origin and needs no entry.
CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",") if o.strip()
]

# ── Accounts: email, verification, password reset ────────────
# Where emails are sent.  console: written to the log (development only);
# smtp: sent through SMTP_*; memory: kept in a list (tests).
MAIL_MODE = os.getenv("MAIL_MODE", "console").lower()
MAIL_FROM = os.getenv("MAIL_FROM", "Digital Twin <no-reply@localhost>")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() in ("1", "true", "yes")
# Public address of the app, used in the links inside emails.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
RESET_TOKEN_MINUTES = int(os.getenv("RESET_TOKEN_MINUTES", "30"))
VERIFY_TOKEN_HOURS = int(os.getenv("VERIFY_TOKEN_HOURS", "24"))
# When true, an account must verify its email address before it can sign in.
REQUIRE_EMAIL_VERIFICATION = os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").lower() in ("1", "true", "yes")

# Requests per client IP per minute (0 disables).  In memory and per process
# unless REDIS_URL is set, which shares the counters between processes.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
LOGIN_RATE_LIMIT_PER_MINUTE = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "10"))
REDIS_URL = os.getenv("REDIS_URL", "")

# Days a rejected proposal is not offered again unchanged (0 disables).
REJECTION_COOLDOWN_DAYS = int(os.getenv("REJECTION_COOLDOWN_DAYS", "30"))

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

