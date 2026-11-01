"""
Usage Analyzer Service — DB orchestration for usage analysis.

Supports both single and multi-subscription analysis.
Fetches all active subscriptions, runs the pure analyzer on each,
and picks the subscription with the clearest optimization opportunity.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.models import PlanHistory, Subscription, UsageData
from app.services.user_service import require_user
from app.modules.audit_logger.service import create_audit_log
from app.modules.privacy_mediator.service import require_consent
from app.modules.usage_analyzer.analyzer import (
    analyze_usage,
    opportunity_value,
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


def _last_plan_change(db: Session, subscription: Subscription) -> Optional[datetime]:
    """When the subscription's terms last changed (None if never)."""
    return (
        db.query(func.max(PlanHistory.created_at))
        .filter(PlanHistory.subscription_id == subscription.id)
        .scalar()
    )


def analyze_subscription(
    db: Session, user_id: int, subscription: Subscription
) -> Dict[str, Any]:
    """Run analysis for one subscription and attach context fields (no audit)."""
    usage_records = get_usage_for_subscription(db, subscription)
    result = analyze_usage(
        usage_records, subscription, overage_since=_last_plan_change(db, subscription),
    )

    # Attach context so the caller knows which subscription this belongs to
    result["subscription_id"] = subscription.id
    result["user_id"] = user_id
    result["provider"] = subscription.provider
    result["plan_name"] = subscription.plan_name
    result["monthly_cost"] = subscription.monthly_cost
    result["data_limit_gb"] = subscription.data_limit_gb
    result["call_minutes_limit"] = subscription.call_minutes_limit
    result["opportunity_value"] = opportunity_value(result)

    return result


def find_optimization_target(db: Session, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Return the analysis of the subscription the other agents should act on,
    or None when no active subscription has an opportunity.  Writes no audit log.
    """
    analyses = [
        analyze_subscription(db, user_id, sub)
        for sub in _get_active_subscriptions(db, user_id)
    ]
    return pick_optimization_target(analyses)


def _summary(a: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("subscription_id", "provider", "plan_name", "monthly_cost", "efficiency",
            "recommendation", "usage_category", "unused_spend", "avg_overage",
            "opportunity_value", "confidence_score")
    return {k: a.get(k) for k in keys}


def analyze_user_usage(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Analyze ALL active subscriptions for a user and record the decision.

    Steps:
        1. Validate user exists and has consented to analysis.
        2. Fetch all active subscriptions.
        3. For each subscription, fetch its usage records and analyze.
        4. Pick the optimization target (largest ₹/month at stake) and
           the best-fit plan.
        5. Write one audit log entry and return the comparison.

    The top-level metric fields describe the optimization target when there
    is one, otherwise the best-fit plan.

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
            "efficiency": None,
            "recommendation": "no_subscription",
            "usage_category": "unknown",
            "message": "User has no active subscription to analyze.",
            "unused_spend": 0,
            "confidence_score": 0,
            "best_plan": None,
            "optimization_target": None,
            "analysis": [],
        }
        create_audit_log(db, user_id, "analysis", response)
        return response

    # Step 3 — Analyze each subscription
    analyses = [analyze_subscription(db, user_id, sub) for sub in subscriptions]

    # Step 4 — Pick target and best plan
    target = pick_optimization_target(analyses)
    best = pick_best_plan(analyses)
    headline = target or best

    # Step 5 — Build response (headline metrics + comparison)
    response = {
        **headline,
        "user_id": user_id,
        "best_plan": _summary(best),
        "optimization_target": _summary(target) if target else None,
        "analysis": analyses,
    }

    create_audit_log(db, user_id, "analysis", response)
    return response
