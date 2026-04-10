"""
Centralized Logger — Application-wide logging configuration.

Provides a single ``get_logger()`` factory that all modules should use
instead of ``print()`` statements or direct ``logging.getLogger()`` calls.

Usage::

    from app.core.logger import get_logger

    logger = get_logger(__name__)
    logger.info("Pipeline started for user_id=%d", user_id)

The underlying configuration (format, level, handlers) is set once
via ``setup_logging()`` at application startup.  All loggers created
through ``get_logger`` inherit that configuration automatically.
"""

import logging
import sys
from typing import Optional

from app.config import LOG_LEVEL, LOG_FORMAT, LOG_DATE_FORMAT


# ─────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────
_logging_configured: bool = False


def setup_logging(
    level: Optional[str] = None,
    fmt: Optional[str] = None,
    datefmt: Optional[str] = None,
) -> None:
    """
    Configure application-wide logging.

    This should be called **once** at application startup (e.g. in main.py).
    Subsequent calls are no-ops to avoid duplicate handlers.

    Args:
        level:   Override log level (default: from config).
        fmt:     Override log format (default: from config).
        datefmt: Override date format (default: from config).
    """
    global _logging_configured
    if _logging_configured:
        return

    effective_level = getattr(logging, (level or LOG_LEVEL).upper(), logging.INFO)
    effective_format = fmt or LOG_FORMAT
    effective_datefmt = datefmt or LOG_DATE_FORMAT

    # ── Root logger configuration ────────────────────────────
    root = logging.getLogger()
    root.setLevel(effective_level)

    # Console handler (stdout)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(effective_level)
        formatter = logging.Formatter(
            fmt=effective_format,
            datefmt=effective_datefmt,
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # Quieten noisy third-party loggers
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger that inherits the application configuration.

    This is the **preferred** way for any module to obtain a logger::

        from app.core.logger import get_logger
        logger = get_logger(__name__)

    Args:
        name: Logger name — typically ``__name__`` of the calling module.

    Returns:
        A configured ``logging.Logger`` instance.
    """
    return logging.getLogger(name)
