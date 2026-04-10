"""
Usage Analyzer Service — DB orchestration for usage analysis.

Supports both single and multi-subscription analysis.
Fetches all active subscriptions, runs the pure analyzer on each,
and picks the best plan.
"""

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.models import Subscription, UsageData
from app.services.user_service import require_user
from app.modules.usage_analyzer.analyzer import analyze_usage, pick_best_plan

logger = get_logger(__name__)


def _get_usage_for_subscription(
    db: Session, user_id: int, provider: str
) -> List[UsageData]:
    """Fetch usage records for a specific user + provider pair."""
    return (
        db.query(UsageData)
        .filter(
            UsageData.user_id == user_id,
            UsageData.provider == provider,
        )
        .order_by(UsageData.period_start.desc())
        .all()
    )


def _analyze_single(
    db: Session, user_id: int, subscription: Subscription
) -> Dict[str, Any]:
    """Run analysis for one subscription and attach context fields."""
    usage_records = _get_usage_for_subscription(db, user_id, subscription.provider)
    result = analyze_usage(usage_records, subscription)

    # Attach context so the caller knows which subscription this belongs to
    result["subscription_id"] = subscription.id
    result["user_id"] = user_id
    result["provider"] = subscription.provider
    result["plan_name"] = subscription.plan_name
    result["monthly_cost"] = subscription.monthly_cost

    return result


def analyze_user_usage(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Analyze ALL active subscriptions for a user and pick the best plan.

    Steps:
        1. Validate user exists.
        2. Fetch all active subscriptions.
        3. For each subscription, fetch matching usage records and analyze.
        4. Pick the best plan from the results.
        5. Return comparison + best-plan recommendation.

    Backward-compatible:
        When a user has only one subscription the response shape is
        identical to the original single-subscription output, with an
        added ``analysis`` list containing one entry.

    Raises:
        ValueError: If user not found.
    """
    # Step 1 — Validate user
    require_user(db, user_id)
    logger.info("Analyzing usage for user_id=%d", user_id)

    # Step 2 — Get ALL active subscriptions
    subscriptions = (
        db.query(Subscription)
        .filter(Subscription.user_id == user_id, Subscription.is_active == True)
        .order_by(Subscription.created_at.desc())
        .all()
    )

    if not subscriptions:
        return {
            "efficiency": 0,
            "avg_data_usage": 0,
            "avg_call_usage": 0,
            "recommendation": "no_subscription",
            "message": "User has no active subscription to analyze.",
            "savings_estimate": 0,
            "usage_category": "unknown",
            "confidence_score": 0,
            "best_plan": None,
            "analysis": [],
        }

    # Step 3 — Analyze each subscription
    analyses = [
        _analyze_single(db, user_id, sub)
        for sub in subscriptions
    ]

    # Step 4 — Pick best plan
    best = pick_best_plan(analyses)

    # Step 5 — Build response
    # For backward compatibility when there's only 1 subscription,
    # the top-level fields mirror the single-analysis result.
    response = {
        "user_id": user_id,
        "efficiency": best["efficiency"],
        "avg_data_usage": best["avg_data_usage"],
        "avg_call_usage": best["avg_call_usage"],
        "recommendation": best["recommendation"],
        "message": best["message"],
        "savings_estimate": best["savings_estimate"],
        "usage_category": best["usage_category"],
        "confidence_score": best["confidence_score"],
        "provider": best["provider"],
        "plan_name": best["plan_name"],
        "monthly_cost": best["monthly_cost"],
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
        "analysis": analyses,
    }

    return response
