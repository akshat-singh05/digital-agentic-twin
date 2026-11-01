"""
Plan Switching Service — proposes plan changes and executes approved ones.

The agent never changes a subscription on its own:

  propose_plan_switch()    → evaluates the latest negotiation against the
                             KPI rules; if it passes, writes a
                             Recommendation (status "proposed").
  approve_recommendation() → user decision; needs the
                             ``execute_plan_changes`` consent, re-validates,
                             then applies the change atomically.
  reject_recommendation()  → user decision; nothing changes.
  rollback_last_change()   → restores the terms before the last applied
                             change (needs ``execute_plan_changes``).

Integrates:
  - Negotiation History → the agreed negotiated price
  - Plan Switching Executor → pure KPI validation
  - Privacy Mediator → consent gate for execution
  - Audit Logger → every proposal, decision and change
  - transactional() → atomic commit / rollback
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.run_context import current_run_id
from app.models import NegotiationHistory, PlanHistory, Recommendation, Subscription
from app.services.user_service import require_user
from app.services.base_service import save, transactional
from app.services.recommendation_service import pending_recommendation, require_recommendation
from app.modules.audit_logger.service import build_audit_log
from app.modules.plan_switching.executor import MINIMUM_SAVINGS_PCT, evaluate_switch
from app.modules.privacy_mediator.service import require_consent
from app.modules.usage_analyzer.service import get_usage_for_subscription

_AGENT = "plan_switching"


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


def _get_recent_usage_stats(db: Session, subscription: Subscription) -> Dict[str, Any]:
    """
    Compute average usage stats from the last 6 billing periods.
    """
    records = get_usage_for_subscription(db, subscription, limit=6)

    if not records:
        return {"avg_data_used_gb": 0.0, "avg_call_minutes": 0.0, "periods": 0}

    avg_data = sum(r.data_used_gb or 0 for r in records) / len(records)
    avg_calls = sum(r.call_minutes_used or 0 for r in records) / len(records)

    return {
        "avg_data_used_gb": round(avg_data, 2),
        "avg_call_minutes": round(avg_calls, 2),
        "periods": len(records),
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recommendation_out(rec: Recommendation) -> Dict[str, Any]:
    return {
        "recommendation_id": rec.id,
        "status": rec.status,
        "subscription_id": rec.subscription_id,
        "current_cost": rec.current_cost,
        "proposed_cost": rec.proposed_cost,
        "estimated_monthly_savings": rec.estimated_monthly_savings,
    }


# ─────────────────────────────────────────────────────────────
# Public API — proposal
# ─────────────────────────────────────────────────────────────
def propose_plan_switch(
    db: Session, user_id: int, subscription_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Evaluate the latest negotiation and, if it passes the KPI rules,
    propose it as a Recommendation awaiting the user's approval.

    Nothing about the subscription changes here.

    Steps:
        1. Validate user exists.
        2. Fetch the target subscription and its latest negotiation round.
        3. Refuse if a proposal for it already awaits a decision.
        4. No agreement → not proposed.  Otherwise evaluate (KPI + risk).
        5. Within one transaction: write the Recommendation (if approved
           by the rules) and the audit entry.

    Returns:
        Dict with proposed, recommendation_id, reason, projected_cost, …

    Raises:
        ValueError:    If user or subscription not found.
        ConflictError: If no negotiation exists or a proposal is pending.
    """
    # Step 1 / 2 — User, target subscription, latest negotiation round
    require_user(db, user_id)
    subscription, negotiation = _get_target_and_negotiation(db, user_id, subscription_id)
    current_cost = subscription.monthly_cost

    # Step 3 — One open proposal per subscription
    pending = pending_recommendation(db, subscription.id)
    if pending is not None:
        raise ConflictError(
            f"Recommendation #{pending.id} for subscription {subscription.id} "
            f"is awaiting your decision"
        )

    # Step 4 — Only an accepted deal has a price both sides agreed to
    usage_stats = _get_recent_usage_stats(db, subscription)

    if negotiation.status != "accepted":
        proposed_cost = current_cost
        evaluation = {
            "approved": False,
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

    savings = round(current_cost - proposed_cost, 2)

    # Step 5 — Persist proposal + audit entry atomically
    rec = None
    with transactional(db):
        if evaluation["approved"]:
            rec = save(db, Recommendation(
                user_id=user_id,
                subscription_id=subscription.id,
                run_id=current_run_id.get(),
                action="apply_negotiated_price",
                current_cost=current_cost,
                proposed_cost=proposed_cost,
                estimated_monthly_savings=savings,
                rationale=(
                    f"{evaluation['reason']} Same plan and limits; only the price changes. "
                    f"The price comes from the built-in provider simulator — a real "
                    f"provider would have to confirm it."
                ),
                evidence=json.dumps({
                    "negotiation_round_id": negotiation.id,
                    "negotiation_status": negotiation.status,
                    "risk_flag": evaluation["risk_flag"],
                    "minimum_savings_pct": MINIMUM_SAVINGS_PCT,
                    "usage_stats": usage_stats,
                    "limits": {
                        "data_limit_gb": subscription.data_limit_gb,
                        "call_minutes_limit": subscription.call_minutes_limit,
                    },
                    "simulated_negotiation": True,
                }),
                requires_approval=True,
                status="proposed",
            ))

        save(db, build_audit_log(db, user_id, "switching", {
            "subscription_id": subscription.id,
            "provider": subscription.provider,
            "plan_name": subscription.plan_name,
            "applied": False,
            "proposed": rec is not None,
            "recommendation_id": rec.id if rec else None,
            "reason": evaluation["reason"],
            "previous_cost": current_cost,
            "proposed_cost": proposed_cost,
            "risk_flag": evaluation["risk_flag"],
            "negotiation_status": negotiation.status,
            "usage_stats": usage_stats,
        }))

    return {
        "proposed": rec is not None,
        "recommendation_id": rec.id if rec else None,
        "requires_approval": rec is not None,
        "reason": evaluation["reason"],
        "current_cost": current_cost,
        "projected_cost": evaluation["projected_cost"],
        "estimated_monthly_savings": savings if rec else 0.0,
        "risk_flag": evaluation["risk_flag"],
        "subscription_id": subscription.id,
        "provider": subscription.provider,
        "plan_name": subscription.plan_name,
    }


# ─────────────────────────────────────────────────────────────
# Public API — user decisions
# ─────────────────────────────────────────────────────────────
def _require_proposed(rec: Recommendation) -> None:
    if rec.status != "proposed":
        raise ConflictError(f"Recommendation #{rec.id} is already {rec.status}")


def approve_recommendation(db: Session, recommendation_id: int) -> Dict[str, Any]:
    """
    Approve a proposed recommendation and apply it.

    The subscription must still be active and at the price the proposal
    was made against; otherwise the recommendation expires unapplied.

    Raises:
        ValueError:           If the recommendation does not exist.
        ConflictError:        If it is not "proposed" or has gone stale.
        ConsentRequiredError: If ``execute_plan_changes`` is not granted
                              (the recommendation stays "proposed").
    """
    rec = require_recommendation(db, recommendation_id)
    _require_proposed(rec)
    require_consent(db, rec.user_id, "execute_plan_changes", _AGENT)

    subscription = rec.subscription
    if not subscription.is_active or subscription.monthly_cost != rec.current_cost:
        with transactional(db):
            rec.status = "expired"
            rec.decided_at = _now()
            save(db, build_audit_log(db, rec.user_id, "recommendation", {
                "recommendation_id": rec.id, "status": "expired",
                "reason": "the subscription changed after the proposal was made",
            }))
        raise ConflictError(
            f"Recommendation #{rec.id} has expired: the subscription changed "
            f"since it was proposed"
        )

    previous_terms = _snapshot_plan(subscription)
    with transactional(db):
        subscription.previous_plan_snapshot = previous_terms
        subscription.monthly_cost = rec.proposed_cost
        db.flush()
        save(db, PlanHistory(
            subscription_id=subscription.id,
            recommendation_id=rec.id,
            change_type="price_change",
            previous_terms=previous_terms,
            new_terms=_snapshot_plan(subscription),
            previous_cost=rec.current_cost,
            new_cost=rec.proposed_cost,
            reason=f"Recommendation #{rec.id} approved by user",
        ))
        rec.status = "executed"
        rec.decided_at = rec.executed_at = _now()
        save(db, build_audit_log(db, rec.user_id, "switching", {
            "subscription_id": subscription.id,
            "provider": subscription.provider,
            "plan_name": subscription.plan_name,
            "applied": True,
            "approved_by_user": True,
            "recommendation_id": rec.id,
            "previous_cost": rec.current_cost,
            "proposed_cost": rec.proposed_cost,
            "risk_flag": json.loads(rec.evidence or "{}").get("risk_flag", "unknown"),
        }))

    return _recommendation_out(rec)


def reject_recommendation(db: Session, recommendation_id: int) -> Dict[str, Any]:
    """
    Reject a proposed recommendation; the subscription is left unchanged.

    Raises:
        ValueError:    If the recommendation does not exist.
        ConflictError: If it is not "proposed".
    """
    rec = require_recommendation(db, recommendation_id)
    _require_proposed(rec)
    with transactional(db):
        rec.status = "rejected"
        rec.decided_at = _now()
        save(db, build_audit_log(db, rec.user_id, "recommendation", {
            "recommendation_id": rec.id, "status": "rejected",
        }))
    return _recommendation_out(rec)


def rollback_last_change(db: Session, subscription_id: int) -> Dict[str, Any]:
    """
    Restore the subscription's terms from before its last applied change.

    Raises:
        ValueError:           If the subscription does not exist.
        ConflictError:        If there is nothing to roll back.
        ConsentRequiredError: If ``execute_plan_changes`` is not granted.
    """
    subscription = db.get(Subscription, subscription_id)
    if subscription is None:
        raise ValueError(f"Subscription with id {subscription_id} not found")

    last = (
        db.query(PlanHistory)
        .filter(PlanHistory.subscription_id == subscription_id)
        .order_by(PlanHistory.id.desc())
        .first()
    )
    if last is None or last.change_type == "rollback":
        raise ConflictError(
            f"Subscription {subscription_id} has no applied change to roll back"
        )
    require_consent(db, subscription.user_id, "execute_plan_changes", _AGENT)

    restored = json.loads(last.previous_terms)
    current_terms = _snapshot_plan(subscription)
    current_cost = subscription.monthly_cost

    with transactional(db):
        for field, value in restored.items():
            setattr(subscription, field, value)
        db.flush()
        entry = save(db, PlanHistory(
            subscription_id=subscription.id,
            recommendation_id=last.recommendation_id,
            change_type="rollback",
            previous_terms=current_terms,
            new_terms=_snapshot_plan(subscription),
            previous_cost=current_cost,
            new_cost=subscription.monthly_cost,
            reason=f"Rolled back change #{last.id}",
        ))
        save(db, build_audit_log(db, subscription.user_id, "rollback", {
            "subscription_id": subscription.id,
            "provider": subscription.provider,
            "plan_name": subscription.plan_name,
            "previous_cost": current_cost,
            "restored_cost": subscription.monthly_cost,
            "history_id": last.id,
        }))

    return {
        "subscription_id": subscription.id,
        "restored_cost": subscription.monthly_cost,
        "previous_cost": current_cost,
        "history_id": entry.id,
    }
