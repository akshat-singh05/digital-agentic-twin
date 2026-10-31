"""
Programmatic Alembic runner.

``migrate_to_head()`` brings any database to the latest schema:
  - empty database          → all migrations run
  - pre-Alembic database    → (tables exist, no ``alembic_version``) stamped
                              at the baseline, then upgraded
  - already versioned       → pending migrations only (no-op when current)
"""

import os
from typing import Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.core.logger import get_logger
from app.database import engine as default_engine

logger = get_logger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_REVISION = "0001"


def _alembic_config() -> Config:
    cfg = Config(os.path.join(_PROJECT_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_PROJECT_ROOT, "migrations"))
    return cfg


def _run(fn, engine: Engine, *args) -> None:
    """Run an Alembic command on a connection from ``engine``."""
    cfg = _alembic_config()
    with engine.connect() as connection:
        cfg.attributes["connection"] = connection
        fn(cfg, *args)


def migrate_to_head(engine: Optional[Engine] = None) -> None:
    """Upgrade the database to the latest revision (see module docstring)."""
    engine = engine or default_engine
    tables = set(inspect(engine).get_table_names())

    if "alembic_version" not in tables and "users" in tables:
        logger.info("Pre-migration database detected — stamping baseline %s", BASELINE_REVISION)
        _run(command.stamp, engine, BASELINE_REVISION)

    _run(command.upgrade, engine, "head")


def upgrade(revision: str, engine: Optional[Engine] = None) -> None:
    """Upgrade the database to ``revision`` (no legacy detection)."""
    _run(command.upgrade, engine or default_engine, revision)


def downgrade(revision: str, engine: Optional[Engine] = None) -> None:
    """Downgrade the database to ``revision`` (e.g. "base")."""
    _run(command.downgrade, engine or default_engine, revision)
