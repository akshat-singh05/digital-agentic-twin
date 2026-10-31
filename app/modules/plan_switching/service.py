"""
Plan Switching Service — DB orchestration for safe plan switching.

Integrates:
  - Negotiation History → to get the agreed negotiated price
  - Plan Switching Executor → pure KPI validation
  - Audit Logger → records the decision in the same transaction
  - transactional() → atomic commit / rollback

Follows the same service-layer pattern as negotiation_agent/service.py.
"""

import json
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import NegotiationHistory, Subscription, UsageData
from app.services.user_service import require_user
from app.services.base_service import save, transactional
from app.modules.audit_logger.service import build_audit_log
from app.modules.plan_switching.executor import evaluate_switch


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _get_target_and_negotiation(
    db: Session, user_id: int, subscription_id: Optional[int]
) -> Tuple[Subscription, NegotiationHistory]:
    """
    Return the subscription to switch and the last round of its most
    recent negotiation.

    Without ``subscription_id`` the user's most recently negotiated active
    subscription is used.

    Raises:
        ValueError:    If the given subscription is not the user's active one.
        ConflictError: If no negotiation result exists.
    """
    query = (
        db.query(NegotiationHistory)
        .join(Subscription, NegotiationHistory.subscription_id == Subscription.id)
        .filter(Subscription.user_id == user_id, Subscription.is_active == True)
    )

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
        query = query.filter(Subscription.id == subscription_id)

    # Rounds of one session share a commit timestamp, so order by id.
    negotiation = query.order_by(NegotiationHistory.id.desc()).first()
    if not negotiation:
        raise ConflictError(
            f"No negotiation result found for user {user_id}. Run a negotiation first."
        )
    return negotiation.subscription, negotiation


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
    Build the proposed plan dict from an accepted negotiation round.

    The agreed price (the agent offer the provider accepted) replaces the
    current cost; limits stay as-is (same plan tier, just cheaper).
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
def switch_plan(
    db: Session, user_id: int, subscription_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Orchestrate a safe plan switch with automatic rollback.

    Steps:
        1. Validate user exists.
        2. Fetch the target subscription and its latest negotiation round.
        3. Reject outright if that negotiation ended without agreement.
        4. Gather recent usage stats and evaluate the switch (KPI + risk).
        5. Within a transaction:
           a. If approved, snapshot the previous plan and apply the new price.
           b. Write the audit log entry.
           If anything fails → automatic rollback via transactional().
        6. Return the decision result.

    Args:
        db:              Active SQLAlchemy session.
        user_id:         Primary key of the user.
        subscription_id: Subscription to switch; defaults to the most
                         recently negotiated one.

    Returns:
        Dict with applied, reason, projected_cost, rollback flag.

    Raises:
        ValueError:    If user or subscription not found.
        ConflictError: If no negotiation result exists.
    """
    # Step 1 — Validate user
    require_user(db, user_id)

    # Step 2 — Target subscription + latest negotiation round
    subscription, negotiation = _get_target_and_negotiation(db, user_id, subscription_id)
    current_cost = subscription.monthly_cost

    # Step 3 / 4 — Only an accepted deal has a price both sides agreed to
    usage_stats = _get_recent_usage_stats(db, user_id, subscription.provider)

    if negotiation.status != "accepted":
        proposed_cost = current_cost
        evaluation = {
            "applied": False,
            "reason": (
                f"Switch rejected: negotiation ended without agreement "
                f"(provider's final counter ₹{negotiation.provider_counter}). "
                f"Current plan kept at ₹{current_cost}."
            ),
            "projected_cost": current_cost,
            "risk_flag": "not_assessed",
        }
    else:
        current_plan = {
            "provider": subscription.provider,
            "plan_name": subscription.plan_name,
            "monthly_cost": current_cost,
            "data_limit_gb": subscription.data_limit_gb,
            "call_minutes_limit": subscription.call_minutes_limit,
        }
        proposed_plan = _build_proposed_plan(subscription, negotiation)
        proposed_cost = proposed_plan["monthly_cost"]
        evaluation = evaluate_switch(current_plan, proposed_plan, usage_stats)

    # Step 5 — Apply within a transaction
    rollback_occurred = False

    try:
        with transactional(db):
            if evaluation["applied"]:
                # 5a. Snapshot for rollback, then apply the agreed price
                subscription.previous_plan_snapshot = _snapshot_plan(subscription)
                subscription.monthly_cost = proposed_cost
                db.add(subscription)
                db.flush()

            # 5b. Write audit log
            save(db, build_audit_log(db, user_id, "switching", {
                "subscription_id": subscription.id,
                "provider": subscription.provider,
                "plan_name": subscription.plan_name,
                "applied": evaluation["applied"],
                "reason": evaluation["reason"],
                "previous_cost": current_cost,
                "proposed_cost": proposed_cost,
                "projected_cost": evaluation["projected_cost"],
                "risk_flag": evaluation["risk_flag"],
                "negotiation_status": negotiation.status,
                "usage_stats": usage_stats,
            }))

    except Exception:
        # transactional() already rolled back the DB session
        rollback_occurred = True
        evaluation["applied"] = False
        evaluation["reason"] = (
            "Switch failed during application — all changes rolled back. "
            + evaluation.get("reason", "")
        )

    # Step 6 — Build response
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
