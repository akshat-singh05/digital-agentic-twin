"""
Plan Switching Service — DB orchestration for safe plan switching.

Integrates:
  - Usage Analyzer   → to get recent usage stats
  - Negotiation History → to get the final negotiated price
  - Plan Switching Executor → pure KPI validation
  - AuditLog → records the decision
  - transactional() → atomic commit / rollback

Follows the same service-layer pattern as negotiation_agent/service.py.
"""

import json
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models import AuditLog, NegotiationHistory, Subscription, UsageData
from app.services.user_service import require_user
from app.services.base_service import save, transactional
from app.modules.plan_switching.executor import evaluate_switch
from app.modules.usage_analyzer.service import analyze_user_usage


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _get_active_subscription(db: Session, user_id: int) -> Subscription:
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


def _get_latest_negotiation(db: Session, subscription_id: int) -> NegotiationHistory:
    """
    Return the most recent accepted/final negotiation round for a subscription.

    Raises:
        ValueError: If no negotiation result exists.
    """
    neg = (
        db.query(NegotiationHistory)
        .filter(
            NegotiationHistory.subscription_id == subscription_id,
            NegotiationHistory.status.in_(["accepted", "final"]),
        )
        .order_by(NegotiationHistory.created_at.desc())
        .first()
    )
    if not neg:
        raise ValueError(
            f"No negotiation result found for subscription {subscription_id}. "
            f"Run a negotiation first."
        )
    return neg


def _get_recent_usage_stats(db: Session, user_id: int, provider: str) -> Dict[str, Any]:
    """
    Compute average usage stats from the last 6 billing periods.
    """
    records = (
        db.query(UsageData)
        .filter(UsageData.user_id == user_id, UsageData.provider == provider)
        .order_by(UsageData.period_start.desc())
        .limit(6)
        .all()
    )

    if not records:
        return {"avg_data_used_gb": 0.0, "avg_call_minutes": 0.0}

    avg_data = sum(r.data_used_gb or 0 for r in records) / len(records)
    avg_calls = sum(r.call_minutes_used or 0 for r in records) / len(records)

    return {
        "avg_data_used_gb": round(avg_data, 2),
        "avg_call_minutes": round(avg_calls, 2),
    }


def _snapshot_plan(sub: Subscription) -> str:
    """Create a JSON snapshot of the current plan for rollback."""
    return json.dumps({
        "plan_name": sub.plan_name,
        "monthly_cost": sub.monthly_cost,
        "data_limit_gb": sub.data_limit_gb,
        "call_minutes_limit": sub.call_minutes_limit,
        "features": sub.features,
    })


def _build_proposed_plan(
    subscription: Subscription,
    negotiation: NegotiationHistory,
) -> Dict[str, Any]:
    """
    Build the proposed plan dict from the negotiation result.

    The negotiated price replaces the current cost; limits stay
    as-is (provider keeps the same plan tier, just cheaper).
    """
    return {
        "provider": subscription.provider,
        "plan_name": subscription.plan_name,
        "monthly_cost": negotiation.agent_offer,
        "data_limit_gb": subscription.data_limit_gb,
        "call_minutes_limit": subscription.call_minutes_limit,
    }


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
def switch_plan(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Orchestrate a safe plan switch with automatic rollback.

    Steps:
        1. Validate user exists.
        2. Fetch latest active subscription.
        3. Fetch latest accepted/final negotiation result.
        4. Gather recent usage stats.
        5. Call executor to evaluate the switch (KPI + risk).
        6. Within a transaction:
           a. Save previous plan snapshot.
           b. Apply new plan if approved.
           c. Write an audit log entry.
           If anything fails → automatic rollback via transactional().
        7. Return the decision result.

    Args:
        db:      Active SQLAlchemy session.
        user_id: Primary key of the user.

    Returns:
        Dict with applied, reason, projected_cost, rollback flag.

    Raises:
        ValueError: If user, subscription, or negotiation not found.
    """
    # Step 1 — Validate user
    require_user(db, user_id)

    # Step 2 — Get active subscription
    subscription = _get_active_subscription(db, user_id)

    # Step 3 — Get latest negotiation result
    negotiation = _get_latest_negotiation(db, subscription.id)

    # Step 4 — Get recent usage stats
    usage_stats = _get_recent_usage_stats(db, user_id, subscription.provider)

    # Step 5 — Build plans and evaluate
    current_plan = {
        "provider": subscription.provider,
        "plan_name": subscription.plan_name,
        "monthly_cost": subscription.monthly_cost,
        "data_limit_gb": subscription.data_limit_gb,
        "call_minutes_limit": subscription.call_minutes_limit,
    }
    proposed_plan = _build_proposed_plan(subscription, negotiation)

    evaluation = evaluate_switch(current_plan, proposed_plan, usage_stats)

    # Step 6 — Apply within a transaction
    rollback_occurred = False

    try:
        with transactional(db):
            # 6a. Save snapshot for rollback
            subscription.previous_plan_snapshot = _snapshot_plan(subscription)

            if evaluation["applied"]:
                # 6b. Apply new plan
                subscription.monthly_cost = proposed_plan["monthly_cost"]
                db.add(subscription)
                db.flush()

            # 6c. Write audit log
            audit = AuditLog(
                user_id=user_id,
                action="switch" if evaluation["applied"] else "switch_rejected",
                module="plan_switching",
                description=evaluation["reason"],
                details=json.dumps({
                    "previous_cost": current_plan["monthly_cost"],
                    "proposed_cost": proposed_plan["monthly_cost"],
                    "projected_cost": evaluation["projected_cost"],
                    "risk_flag": evaluation["risk_flag"],
                    "applied": evaluation["applied"],
                    "usage_stats": usage_stats,
                }),
            )
            save(db, audit)

    except Exception:
        # transactional() already rolled back the DB session
        rollback_occurred = True
        evaluation["applied"] = False
        evaluation["reason"] = (
            "Switch failed during application — all changes rolled back. "
            + evaluation.get("reason", "")
        )

    # Step 7 — Build response
    return {
        "applied": evaluation["applied"],
        "reason": evaluation["reason"],
        "projected_cost": evaluation["projected_cost"],
        "risk_flag": evaluation["risk_flag"],
        "rollback": rollback_occurred,
        "subscription_id": subscription.id,
        "provider": subscription.provider,
        "plan_name": subscription.plan_name,
    }
