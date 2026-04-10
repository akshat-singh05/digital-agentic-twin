"""
Negotiation Agent Service — DB orchestration for autonomous negotiation.

Integrates:
  - Usage Analyzer   → to get efficiency score
  - Privacy Mediator → to ensure raw data is never leaked
  - Negotiation Engine → pure offer-counteroffer logic
  - NegotiationHistory model → persists every round

Follows the same service-layer pattern as usage_analyzer/service.py.
"""

from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models import NegotiationHistory, Subscription
from app.services.user_service import require_user
from app.services.base_service import save, transactional
from app.modules.usage_analyzer.service import analyze_user_usage
from app.modules.privacy_mediator.service import get_sanitized_usage
from app.modules.negotiation_agent.engine import negotiate


def _get_latest_active_subscription(db: Session, user_id: int) -> Subscription:
    """
    Return the most recently created active subscription for a user.

    Raises:
        ValueError: If no active subscription exists.
    """
    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.is_active == True)
        .order_by(Subscription.created_at.desc())
        .first()
    )
    if not sub:
        raise ValueError(f"No active subscription found for user {user_id}")
    return sub


def run_negotiation(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Orchestrate a full autonomous negotiation for a user.

    Steps:
        1. Validate user exists.
        2. Fetch the latest active subscription.
        3. Run usage analysis to get efficiency.
        4. Fetch sanitized usage (privacy mediator) — ensures raw data
           is never passed externally.
        5. Run the negotiation engine.
        6. Persist every round to negotiation_history.
        7. Return the negotiation summary.

    Args:
        db:      Active SQLAlchemy session.
        user_id: Primary key of the user.

    Returns:
        Dict with final_price, rounds, savings_pct, status, etc.

    Raises:
        ValueError: If user not found or no active subscription.
    """
    # Step 1 — Validate user
    require_user(db, user_id)

    # Step 2 — Get subscription
    subscription = _get_latest_active_subscription(db, user_id)

    # Step 3 — Usage analysis (efficiency score)
    analysis = analyze_user_usage(db, user_id)
    efficiency = analysis.get("efficiency", 0.5)

    # Step 4 — Sanitized usage (privacy layer — logged but not sent
    #          to the engine; proves we invoked the mediator)
    sanitized = get_sanitized_usage(db, user_id)

    # Step 5 — Run negotiation engine
    result = negotiate(
        current_cost=subscription.monthly_cost,
        efficiency=efficiency,
    )

    # Step 6 — Persist rounds
    with transactional(db):
        for rnd in result["rounds"]:
            record = NegotiationHistory(
                subscription_id=subscription.id,
                round_number=rnd["round_number"],
                agent_offer=rnd["agent_offer"],
                provider_counter=rnd["provider_counter"],
                status=rnd["status"],
                notes=rnd["notes"],
            )
            save(db, record)

    # Step 7 — Build response
    return {
        "user_id": user_id,
        "subscription_id": subscription.id,
        "provider": subscription.provider,
        "plan_name": subscription.plan_name,
        "original_cost": result["original_cost"],
        "final_price": result["final_price"],
        "savings_pct": result["savings_pct"],
        "total_rounds": result["total_rounds"],
        "status": result["status"],
        "efficiency_used": efficiency,
        "sanitized_records_count": len(sanitized.get("sanitized_usage", [])),
        "rounds": result["rounds"],
    }
