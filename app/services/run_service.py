"""
Run Service — Read access to orchestrator run history.

Runs and their steps are written by app/orchestrator/pipeline.py.
"""

from typing import List

from sqlalchemy.orm import Session

from app.models import AgentRun
from app.services.base_service import get_or_raise


def require_run(db: Session, run_id: int) -> AgentRun:
    """Fetch a run by ID or raise ValueError if not found."""
    return get_or_raise(db, AgentRun, run_id, "Run")


def list_runs(db: Session, user_id: int, limit: int = 50) -> List[AgentRun]:
    """Return a user's runs, newest first."""
    return (
        db.query(AgentRun)
        .filter(AgentRun.user_id == user_id)
        .order_by(AgentRun.id.desc())
        .limit(limit)
        .all()
    )
