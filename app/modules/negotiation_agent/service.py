"""
Negotiation Agent Service — DB orchestration for autonomous negotiation.

Integrates:
  - Usage Analyzer   → to pick the target subscription and its efficiency
  - Privacy Mediator → to ensure raw data is never leaked
  - Plan alternatives → the best catalog alternative sets the walk-away price
  - Negotiation Engine → pure offer-counteroffer logic (simulated provider)
  - NegotiationHistory model → persists every round
  - Audit Logger → records the outcome in the same transaction

Follows the same service-layer pattern as usage_analyzer/service.py.
"""

from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import NegotiationHistory, Subscription
from app.services.user_service import require_user
from app.services.base_service import save, transactional
from app.modules.audit_logger.service import build_audit_log
from app.modules.usage_analyzer.service import analyze_subscription, find_optimization_target
from app.modules.privacy_mediator.service import get_sanitized_usage, require_consent
from app.modules.negotiation_agent.engine import negotiate
from app.modules.plan_switching.executor import MINIMUM_SAVINGS_PCT
from app.modules.plan_switching.service import find_alternatives


def _resolve_target(
    db: Session, user_id: int, subscription_id: Optional[int]
) -> Dict[str, Any]:
    """
    Return the analysis of the subscription to negotiate for.

    With an explicit ``subscription_id`` that subscription is used (it must
    be the user's and active); otherwise the analyzer's optimization target.

    Raises:
        ValueError:    If the given subscription is not the user's active one.
        ConflictError: If no subscription has an optimization opportunity.
    """
    if subscription_id is not None:
        sub = (
            db.query(Subscription)
            .filter(
                Subscription.id == subscription_id,
                Subscription.user_id == user_id,
                Subscription.is_active == True,
            )
            .first()
        )
        if not sub:
            raise ValueError(
                f"No active subscription {subscription_id} found for user {user_id}"
            )
        return analyze_subscription(db, user_id, sub)

    target = find_optimization_target(db, user_id)
    if target is None or target["recommendation"] != "downgrade":
        raise ConflictError(
            f"No underutilized subscription to negotiate for user {user_id}"
        )
    return target


def _reservation_price(current_cost: float, best_alternative: Optional[Dict[str, Any]]) -> float:
    """
    Walk-away price: a deal must save at least MINIMUM_SAVINGS_PCT, and must
    not cost more than the best catalog alternative (the user's BATNA).
    """
    ceiling = current_cost * (1 - MINIMUM_SAVINGS_PCT / 100)
    if best_alternative is not None:
        ceiling = min(ceiling, best_alternative["monthly_cost"])
    return round(ceiling, 2)


def run_negotiation(
    db: Session, user_id: int, subscription_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Orchestrate a full autonomous negotiation for a user.

    Steps:
        1. Validate user exists.
        2. Resolve the target subscription and its usage analysis.
        3. Fetch sanitized usage (privacy mediator) — ensures raw data
           is never passed externally.
        4. Run the negotiation engine.
        5. Persist every round and the audit entry in one transaction.
        6. Return the negotiation summary.

    Args:
        db:              Active SQLAlchemy session.
        user_id:         Primary key of the user.
        subscription_id: Subscription to negotiate for; defaults to the
                         analyzer's optimization target.

    Returns:
        Dict with final_price, rounds, savings_pct, status, etc.

    Raises:
        ValueError:           If user or subscription not found.
        ConflictError:        If there is nothing worth negotiating.
        ConsentRequiredError: If a required consent scope is missing.
    """
    # Step 1 — Validate user and consent (sharing is checked by the mediator)
    require_user(db, user_id)
    require_consent(db, user_id, "usage_analysis", "negotiation_agent")
    require_consent(db, user_id, "negotiate_on_behalf", "negotiation_agent")

    # Step 2 — Target subscription, its efficiency, and the walk-away price
    analysis = _resolve_target(db, user_id, subscription_id)
    subscription = db.get(Subscription, analysis["subscription_id"])
    efficiency = analysis["efficiency"] if analysis["efficiency"] is not None else 0.5
    alternatives = find_alternatives(db, user_id, subscription, analysis)
    best_alternative = alternatives["fits"][0] if alternatives["fits"] else None
    reservation = _reservation_price(subscription.monthly_cost, best_alternative)

    # Same subscription + session number → same simulated provider behaviour
    session_no = (
        db.query(func.count(NegotiationHistory.id))
        .filter(NegotiationHistory.subscription_id == subscription.id,
                NegotiationHistory.round_number == 1)
        .scalar()
    ) + 1

    # Step 3 — Sanitized usage (privacy layer — logged but not sent
    #          to the engine; proves we invoked the mediator)
    sanitized = get_sanitized_usage(db, user_id)

    # Step 4 — Run negotiation engine
    result = negotiate(
        current_cost=subscription.monthly_cost,
        efficiency=efficiency,
        reservation_price=reservation,
        seed=f"{subscription.id}:{session_no}",
    )

    summary = {
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
        "reservation_price": result["reservation_price"],
        "best_alternative": (
            {k: best_alternative[k] for k in ("plan_id", "provider", "plan_name", "monthly_cost")}
            if best_alternative else None
        ),
        "session": session_no,
        "sanitized_records_count": len(sanitized.get("sanitized_usage", [])),
        # The counterparty is the built-in provider simulator, not a real provider.
        "simulated": True,
        "rounds": result["rounds"],
    }

    # Step 5 — Persist rounds + audit entry
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
        save(db, build_audit_log(db, user_id, "negotiation", summary))

    # Step 6 — Return summary
    return summary
