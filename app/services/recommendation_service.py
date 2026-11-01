"""
Recommendation Service — Read access to the twin's proposed actions.

Recommendations are written by the Plan Switching agent and decided on
(approve / reject) through app/modules/plan_switching/service.py.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import Recommendation
from app.services.base_service import get_or_raise


def require_recommendation(db: Session, recommendation_id: int) -> Recommendation:
    """Fetch a recommendation by ID or raise ValueError if not found."""
    return get_or_raise(db, Recommendation, recommendation_id, "Recommendation")


def list_recommendations(
    db: Session, user_id: int, status: Optional[str] = None, limit: int = 50
) -> List[Recommendation]:
    """Return a user's recommendations, newest first, optionally by status."""
    query = db.query(Recommendation).filter(Recommendation.user_id == user_id)
    if status:
        query = query.filter(Recommendation.status == status)
    return query.order_by(Recommendation.id.desc()).limit(limit).all()


def pending_recommendation(db: Session, subscription_id: int) -> Optional[Recommendation]:
    """The subscription's recommendation still awaiting a decision, if any."""
    return (
        db.query(Recommendation)
        .filter(
            Recommendation.subscription_id == subscription_id,
            Recommendation.status == "proposed",
        )
        .order_by(Recommendation.id.desc())
        .first()
    )
