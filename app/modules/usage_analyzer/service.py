"""
Usage Analyzer Service — DB orchestration for usage analysis.

Supports both single and multi-subscription analysis.
Fetches all active subscriptions, runs the pure analyzer on each,
and picks the subscription with the clearest optimization opportunity.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.models import Subscription, UsageData
from app.services.user_service import require_user
from app.modules.audit_logger.service import create_audit_log
from app.modules.privacy_mediator.service import require_consent
from app.modules.usage_analyzer.analyzer import (
    analyze_usage,
    pick_best_plan,
    pick_optimization_target,
)

logger = get_logger(__name__)


def get_usage_for_subscription(
    db: Session, subscription: Subscription, limit: Optional[int] = None
) -> List[UsageData]:
    """Fetch a subscription's usage records, most recent period first."""
    query = (
        db.query(UsageData)
        .filter(UsageData.subscription_id == subscription.id)
        .order_by(UsageData.period_start.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def _get_active_subscriptions(db: Session, user_id: int) -> List[Subscription]:
    """Return a user's active subscriptions, newest first."""
    return (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.is_active == True)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc())
        .all()
    )


def analyze_subscription(
    db: Session, user_id: int, subscription: Subscription
) -> Dict[str, Any]:
    """Run analysis for one subscription and attach context fields (no audit)."""
    usage_records = get_usage_for_subscription(db, subscription)
    result = analyze_usage(usage_records, subscription)

    # Attach context so the caller knows which subscription this belongs to
    result["subscription_id"] = subscription.id
    result["user_id"] = user_id
    result["provider"] = subscription.provider
    result["plan_name"] = subscription.plan_name
    result["monthly_cost"] = subscription.monthly_cost

    return result


def find_optimization_target(db: Session, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Return the analysis of the subscription the other agents should act on,
    or None when no active subscription is underutilized.  Writes no audit log.
    """
    analyses = [
        analyze_subscription(db, user_id, sub)
        for sub in _get_active_subscriptions(db, user_id)
    ]
    return pick_optimization_target(analyses)


def analyze_user_usage(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Analyze ALL active subscriptions for a user and record the decision.

    Steps:
        1. Validate user exists.
        2. Fetch all active subscriptions.
        3. For each subscription, fetch matching usage records and analyze.
        4. Pick the optimization target (largest underutilized plan) and
           the best-fit plan.
        5. Write one audit log entry and return the comparison.

    The top-level fields describe the optimization target when there is
    one, otherwise the best-fit plan.

    Raises:
        ValueError:           If user not found.
        ConsentRequiredError: If usage analysis has not been consented to.
    """
    # Step 1 — Validate user and consent
    require_user(db, user_id)
    require_consent(db, user_id, "usage_analysis", "usage_analyzer")
    logger.info("Analyzing usage for user_id=%d", user_id)

    # Step 2 — Get ALL active subscriptions
    subscriptions = _get_active_subscriptions(db, user_id)

    if not subscriptions:
        response = {
            "user_id": user_id,
            "efficiency": 0,
            "avg_data_usage": 0,
            "avg_call_usage": 0,
            "recommendation": "no_subscription",
            "message": "User has no active subscription to analyze.",
            "savings_estimate": 0,
            "usage_category": "unknown",
            "confidence_score": 0,
            "best_plan": None,
            "optimization_target": None,
            "analysis": [],
        }
        create_audit_log(db, user_id, "analysis", response)
        return response

    # Step 3 — Analyze each subscription
    analyses = [
        analyze_subscription(db, user_id, sub)
        for sub in subscriptions
    ]

    # Step 4 — Pick target and best plan
    target = pick_optimization_target(analyses)
    best = pick_best_plan(analyses)
    headline = target or best

    # Step 5 — Build response
    response = {
        "user_id": user_id,
        "subscription_id": headline["subscription_id"],
        "efficiency": headline["efficiency"],
        "avg_data_usage": headline["avg_data_usage"],
        "avg_call_usage": headline["avg_call_usage"],
        "recommendation": headline["recommendation"],
        "message": headline["message"],
        "savings_estimate": headline["savings_estimate"],
        "usage_category": headline["usage_category"],
        "confidence_score": headline["confidence_score"],
        "provider": headline["provider"],
        "plan_name": headline["plan_name"],
        "monthly_cost": headline["monthly_cost"],
        "best_plan": {
            "subscription_id": best["subscription_id"],
            "provider": best["provider"],
            "plan_name": best["plan_name"],
            "monthly_cost": best["monthly_cost"],
            "efficiency": best["efficiency"],
            "recommendation": best["recommendation"],
            "savings_estimate": best["savings_estimate"],
            "usage_category": best["usage_category"],
            "confidence_score": best["confidence_score"],
        },
        "optimization_target": {
            "subscription_id": target["subscription_id"],
            "provider": target["provider"],
            "plan_name": target["plan_name"],
            "monthly_cost": target["monthly_cost"],
            "savings_estimate": target["savings_estimate"],
        } if target else None,
        "analysis": analyses,
    }

    create_audit_log(db, user_id, "analysis", response)
    return response
