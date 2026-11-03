"""
Plan Switching Service — proposes plan changes and executes approved ones.

The agent never changes a subscription on its own:

  find_alternatives()      → catalog plans that fit the user's measured
                             usage and preferences (pure ranking in
                             alternatives.py), with reasons for the rest.
  propose_plan_switch()    → compares the negotiated price (if a fresh deal
                             exists) with the best catalog alternative,
                             checks both against the KPI rules, and proposes
                             the larger saving as a Recommendation with a
                             structured explanation (status "proposed").
  approve_recommendation() → user decision; needs the
                             ``execute_plan_changes`` consent, re-validates,
                             then applies the change atomically.
  reject_recommendation()  → user decision; nothing changes.
  rollback_last_change()   → restores the terms before the last applied
                             change (needs ``execute_plan_changes``).

Integrates:
  - Usage Analyzer → measured usage profile
  - Plan catalog + user preferences → alternatives
  - Negotiation History → the agreed negotiated price
  - Plan Switching Executor → pure KPI validation
  - Privacy Mediator → consent gates
  - Audit Logger → every proposal, decision and change
  - transactional() → atomic commit / rollback
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import config
from app.core.errors import ConflictError
from app.core.run_context import current_run_id
from app.models import NegotiationBrief, PlanCatalog, PlanHistory, Recommendation, Subscription
from app.services.base_service import save, transactional
from app.services.catalog_service import list_plans
from app.services.recommendation_service import pending_recommendation, require_recommendation
from app.services.user_service import get_preferences, require_user
from app.modules.audit_logger.service import build_audit_log
from app.modules.plan_switching.alternatives import describe_tradeoffs, rank_alternatives
from app.modules.plan_switching.executor import MINIMUM_SAVINGS_PCT, evaluate_switch
from app.modules.privacy_mediator.service import require_consent
from app.modules.usage_analyzer.service import (
    analyze_subscription, find_optimization_target, get_usage_for_subscription,
)

_AGENT = "plan_switching"
_PROFILE_KEYS = (
    "periods", "avg_data_usage", "peak_data_usage", "projected_data_usage", "data_trend",
    "avg_call_usage", "peak_call_usage", "projected_call_usage", "avg_overage",
    "efficiency", "binding_metric", "confidence_score",
)


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_active_subscription(db: Session, user_id: int, subscription_id: int) -> Subscription:
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
        raise ValueError(f"No active subscription {subscription_id} found for user {user_id}")
    return sub


def _plan_terms(sub: Subscription) -> Dict[str, Any]:
    return {
        "provider": sub.provider,
        "plan_name": sub.plan_name,
        "monthly_cost": sub.monthly_cost,
        "data_limit_gb": sub.data_limit_gb,
        "call_minutes_limit": sub.call_minutes_limit,
        "features": sub.features,
        "catalog_plan_id": sub.catalog_plan_id,
    }


def _snapshot_plan(sub: Subscription) -> str:
    """JSON snapshot of the plan's terms (for history and rollback)."""
    return json.dumps(_plan_terms(sub))


def _subscription_category(db: Session, sub: Subscription) -> Optional[str]:
    """Category from the linked catalog plan, else from the provider's catalog plans."""
    if sub.catalog_plan is not None:
        return sub.catalog_plan.category
    plan = db.query(PlanCatalog).filter(PlanCatalog.provider == sub.provider).first()
    return plan.category if plan else None


def _get_recent_usage_stats(db: Session, subscription: Subscription) -> Dict[str, Any]:
    """Average usage over the last 6 billing periods (for SLA risk)."""
    records = get_usage_for_subscription(db, subscription, limit=6)
    if not records:
        return {"avg_data_used_gb": 0.0, "avg_call_minutes": 0.0, "periods": 0}
    return {
        "avg_data_used_gb": round(sum(r.data_used_gb or 0 for r in records) / len(records), 2),
        "avg_call_minutes": round(sum(r.call_minutes_used or 0 for r in records) / len(records), 2),
        "periods": len(records),
    }


def reported_deal(db: Session, sub: Subscription) -> Optional[NegotiationBrief]:
    """
    The latest negotiation brief for the subscription's *current* terms whose
    outcome the user has reported — a report older than the last plan change
    was about a different price and is ignored.  Open briefs (no answer yet)
    and dismissed ones are not a deal.
    """
    brief = (
        db.query(NegotiationBrief)
        .filter(NegotiationBrief.subscription_id == sub.id,
                NegotiationBrief.status.in_(("accepted", "declined")))
        .order_by(NegotiationBrief.id.desc())
        .first()
    )
    last_change = (
        db.query(PlanHistory)
        .filter(PlanHistory.subscription_id == sub.id)
        .order_by(PlanHistory.id.desc())
        .first()
    )
    if brief and last_change and brief.reported_at <= last_change.created_at:
        return None
    return brief


def _risk_drawback(risk_flag: str) -> List[str]:
    if risk_flag == "medium":
        return ["Average usage is within 15% of the new limit (medium SLA risk)."]
    return []


# ─────────────────────────────────────────────────────────────
# Public API — alternatives
# ─────────────────────────────────────────────────────────────
def find_alternatives(
    db: Session, user_id: int, subscription: Subscription,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Rank catalog plans for a subscription against measured usage and the
    user's preferences.  Reads usage, so callers must hold the
    ``usage_analysis`` consent.
    """
    analysis = analysis or analyze_subscription(db, user_id, subscription)
    current = {**_plan_terms(subscription), "category": _subscription_category(db, subscription)}
    catalog = [
        {
            "id": p.id, "provider": p.provider, "plan_name": p.plan_name,
            "category": p.category, "monthly_cost": p.monthly_cost,
            "data_limit_gb": p.data_limit_gb, "call_minutes_limit": p.call_minutes_limit,
            "features": p.features, "source": p.source,
        }
        for p in list_plans(db)
    ]
    result = rank_alternatives(
        current, catalog, analysis, get_preferences(db, user_id), MINIMUM_SAVINGS_PCT,
    )
    return {
        "subscription_id": subscription.id,
        "current_plan": current,
        "usage_profile": {k: analysis.get(k) for k in _PROFILE_KEYS},
        **result,
    }


def get_alternatives(db: Session, subscription_id: int) -> Dict[str, Any]:
    """
    Alternatives for a subscription (API entry point).

    Raises:
        ValueError:           If the subscription does not exist.
        ConsentRequiredError: If ``usage_analysis`` is not granted.
    """
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise ValueError(f"Subscription with id {subscription_id} not found")
    require_consent(db, sub.user_id, "usage_analysis", _AGENT)
    return find_alternatives(db, sub.user_id, sub)


# ─────────────────────────────────────────────────────────────
# Public API — proposal
# ─────────────────────────────────────────────────────────────
def _recently_rejected(db: Session, subscription_id: int, option: Dict[str, Any]) -> Optional[Recommendation]:
    """
    A rejected recommendation, inside the cool-down, with exactly these terms.

    A different plan or price is a new proposal and is never held back.
    """
    if config.REJECTION_COOLDOWN_DAYS <= 0:
        return None
    since = _now() - timedelta(days=config.REJECTION_COOLDOWN_DAYS)
    plan_id = option["plan"]["plan_id"] if option["plan"] else None
    return (
        db.query(Recommendation)
        .filter(
            Recommendation.subscription_id == subscription_id,
            Recommendation.status == "rejected",
            Recommendation.decided_at >= since,
            Recommendation.action == option["action"],
            Recommendation.proposed_plan_id == plan_id,
            Recommendation.proposed_cost == option["proposed_cost"],
        )
        .order_by(Recommendation.id.desc())
        .first()
    )


def propose_plan_switch(
    db: Session, user_id: int, subscription_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Decide whether a change is worth proposing for one subscription.

    Options considered (each must pass the KPI rules):
      A. the negotiated price on the current plan — only a fresh,
         *accepted* deal counts;
      B. the best-fitting catalog alternative.
    The option with the larger monthly saving becomes a Recommendation
    awaiting the user's approval.  Nothing about the subscription changes.

    Without ``subscription_id`` the analyzer's optimization target is used.

    Raises:
        ValueError:           If user or subscription not found.
        ConflictError:        If there is nothing to optimize or a
                              proposal is already pending.
        ConsentRequiredError: If ``usage_analysis`` is not granted.
    """
    # 1 — User, consent, target subscription
    require_user(db, user_id)
    require_consent(db, user_id, "usage_analysis", _AGENT)
    if subscription_id is not None:
        subscription = _require_active_subscription(db, user_id, subscription_id)
    else:
        target = find_optimization_target(db, user_id)
        if target is None:
            raise ConflictError(f"No subscription with an optimization opportunity for user {user_id}")
        subscription = db.get(Subscription, target["subscription_id"])

    # 2 — One open proposal per subscription
    pending = pending_recommendation(db, subscription.id)
    if pending is not None:
        raise ConflictError(
            f"Recommendation #{pending.id} for subscription {subscription.id} "
            f"is awaiting your decision"
        )

    analysis = analyze_subscription(db, user_id, subscription)
    usage_stats = _get_recent_usage_stats(db, subscription)
    current = _plan_terms(subscription)
    current_cost = subscription.monthly_cost
    options, notes = [], []

    # 3A — A price the provider agreed to, as reported by the user
    negotiation = reported_deal(db, subscription)
    if negotiation is None:
        notes.append("No provider offer reported for the current terms.")
    elif negotiation.status != "accepted":
        notes.append(f"The provider declined your request (brief #{negotiation.id}).")
    else:
        proposed = {**current, "monthly_cost": negotiation.agreed_price}
        ev = evaluate_switch(current, proposed, usage_stats)
        if not ev["approved"]:
            notes.append(ev["reason"])
        else:
            savings = round(current_cost - negotiation.agreed_price, 2)
            options.append({
                "action": "apply_negotiated_price",
                "proposed_cost": negotiation.agreed_price,
                "monthly_savings": savings,
                "evaluation": ev,
                "plan": None,
                "what": (
                    f"Keep {subscription.provider} {subscription.plan_name} at the negotiated "
                    f"price ₹{negotiation.agreed_price} instead of ₹{current_cost}."
                ),
                "benefits": [
                    f"Saves ₹{savings}/month ({round(savings / current_cost * 100, 2)}%).",
                    "Same plan, limits and features.",
                ],
                "drawbacks": [
                    "This is the price you reported the provider agreed to; the twin has not "
                    "verified it, so check your next bill."
                ] + _risk_drawback(ev["risk_flag"]),
            })

    # 3B — Best catalog alternative that also passes the KPI rules
    alternatives = find_alternatives(db, user_id, subscription, analysis)
    for alt in alternatives["fits"]:
        ev = evaluate_switch(current, alt, usage_stats)
        if not ev["approved"]:
            continue
        tradeoffs = describe_tradeoffs(current, alt, analysis)
        options.append({
            "action": "switch_plan",
            "proposed_cost": alt["monthly_cost"],
            "monthly_savings": alt["monthly_savings"],
            "evaluation": ev,
            "plan": alt,
            "what": (
                f"Switch from {subscription.provider} {subscription.plan_name} to "
                f"{alt['provider']} {alt['plan_name']} at ₹{alt['monthly_cost']}/month."
            ),
            "benefits": tradeoffs["benefits"],
            "drawbacks": tradeoffs["drawbacks"] + _risk_drawback(ev["risk_flag"]),
        })
        break
    else:
        n_excluded = len(alternatives["excluded"])
        if n_excluded:
            first = alternatives["excluded"][0]
            notes.append(
                f"No catalog plan fits ({n_excluded} checked; e.g. {first['plan_name']}: "
                f"{first['reasons'][0]})."
            )
        else:
            notes.append("No comparable catalog plan.")

    chosen = max(options, key=lambda o: o["monthly_savings"]) if options else None
    if chosen is not None:
        repeat = _recently_rejected(db, subscription.id, chosen)
        if repeat is not None:
            notes.append(
                f"Not proposed again: you rejected the same proposal (#{repeat.id}) on "
                f"{repeat.decided_at:%Y-%m-%d}. It can be offered again after "
                f"{config.REJECTION_COOLDOWN_DAYS} days, or sooner if the terms change."
            )
            chosen = None
    considered = [
        {"action": o["action"], "proposed_cost": o["proposed_cost"],
         "monthly_savings": o["monthly_savings"],
         "plan_name": o["plan"]["plan_name"] if o["plan"] else subscription.plan_name}
        for o in options
    ]

    # 4 — Persist proposal + audit entry atomically
    rec = None
    explanation = None
    with transactional(db):
        if chosen is not None:
            ev = chosen["evaluation"]
            explanation = {
                "what": chosen["what"],
                "why": f"{analysis['message']} {ev['reason']}",
                "benefits": chosen["benefits"],
                "drawbacks": chosen["drawbacks"],
                "data_used": {k: analysis.get(k) for k in _PROFILE_KEYS},
                "requires_approval": True,
            }
            rec = save(db, Recommendation(
                user_id=user_id,
                subscription_id=subscription.id,
                run_id=current_run_id.get(),
                proposed_plan_id=chosen["plan"]["plan_id"] if chosen["plan"] else None,
                action=chosen["action"],
                current_cost=current_cost,
                proposed_cost=chosen["proposed_cost"],
                estimated_monthly_savings=chosen["monthly_savings"],
                rationale=" ".join([chosen["what"], explanation["why"]] + chosen["drawbacks"][:1]),
                evidence=json.dumps({
                    "explanation": explanation,
                    "current_plan": current,
                    "options_considered": considered,
                    "alternatives_excluded": [
                        {"plan_name": a["plan_name"], "reasons": a["reasons"]}
                        for a in alternatives["excluded"][:5]
                    ],
                    "risk_flag": ev["risk_flag"],
                    "minimum_savings_pct": MINIMUM_SAVINGS_PCT,
                    "usage_stats": usage_stats,
                    "negotiation_brief_id": negotiation.id if negotiation else None,
                    "user_reported_price": chosen["action"] == "apply_negotiated_price",
                }),
                requires_approval=True,
                status="proposed",
            ))

        reason = (
            chosen["evaluation"]["reason"] if chosen
            else " ".join(notes)
        )
        save(db, build_audit_log(db, user_id, "switching", {
            "subscription_id": subscription.id,
            "provider": subscription.provider,
            "plan_name": subscription.plan_name,
            "applied": False,
            "proposed": rec is not None,
            "recommendation_id": rec.id if rec else None,
            "action": chosen["action"] if chosen else None,
            "proposed_plan_name": chosen["plan"]["plan_name"] if chosen and chosen["plan"] else None,
            "reason": reason,
            "previous_cost": current_cost,
            "proposed_cost": chosen["proposed_cost"] if chosen else current_cost,
            "monthly_savings": chosen["monthly_savings"] if chosen else 0,
            "risk_flag": chosen["evaluation"]["risk_flag"] if chosen else "not_assessed",
            "options_considered": considered,
        }))

    return {
        "proposed": rec is not None,
        "recommendation_id": rec.id if rec else None,
        "requires_approval": rec is not None,
        "action": chosen["action"] if chosen else None,
        "reason": reason,
        "current_cost": current_cost,
        "projected_cost": chosen["proposed_cost"] if chosen else current_cost,
        "estimated_monthly_savings": chosen["monthly_savings"] if chosen else 0.0,
        "risk_flag": chosen["evaluation"]["risk_flag"] if chosen else "not_assessed",
        "proposed_plan": chosen["plan"] if chosen else None,
        "options_considered": considered,
        "explanation": explanation,
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


def _recommendation_out(rec: Recommendation) -> Dict[str, Any]:
    return {
        "recommendation_id": rec.id,
        "status": rec.status,
        "action": rec.action,
        "subscription_id": rec.subscription_id,
        "current_cost": rec.current_cost,
        "proposed_cost": rec.proposed_cost,
        "estimated_monthly_savings": rec.estimated_monthly_savings,
    }


def _stale_reason(rec: Recommendation) -> Optional[str]:
    """Why the recommendation can no longer be applied as proposed, if it can't."""
    sub = rec.subscription
    if not sub.is_active:
        return "the subscription is no longer active"
    proposed_terms = json.loads(rec.evidence or "{}").get("current_plan", {})
    if sub.monthly_cost != rec.current_cost or (
        proposed_terms and sub.plan_name != proposed_terms.get("plan_name")
    ):
        return "the subscription changed after the proposal was made"
    if rec.action == "switch_plan":
        plan = rec.proposed_plan
        if plan is None or not plan.is_available or plan.monthly_cost != rec.proposed_cost:
            return "the proposed catalog plan changed or is no longer available"
    return None


def approve_recommendation(db: Session, recommendation_id: int) -> Dict[str, Any]:
    """
    Approve a proposed recommendation and apply it.

    ``apply_negotiated_price`` changes the price; ``switch_plan`` moves the
    subscription to the catalog plan's terms.  Either way the change is
    recorded in plan_history.  A stale recommendation expires unapplied.

    Raises:
        ValueError:           If the recommendation does not exist.
        ConflictError:        If it is not "proposed" or has gone stale.
        ConsentRequiredError: If ``execute_plan_changes`` is not granted
                              (the recommendation stays "proposed").
    """
    rec = require_recommendation(db, recommendation_id)
    _require_proposed(rec)
    require_consent(db, rec.user_id, "execute_plan_changes", _AGENT)

    stale = _stale_reason(rec)
    if stale:
        with transactional(db):
            rec.status = "expired"
            rec.decided_at = _now()
            save(db, build_audit_log(db, rec.user_id, "recommendation", {
                "recommendation_id": rec.id, "status": "expired", "reason": stale,
            }))
        raise ConflictError(f"Recommendation #{rec.id} has expired: {stale}")

    subscription = rec.subscription
    previous_terms = _snapshot_plan(subscription)
    with transactional(db):
        subscription.previous_plan_snapshot = previous_terms
        if rec.action == "switch_plan":
            plan = rec.proposed_plan
            subscription.provider = plan.provider
            subscription.plan_name = plan.plan_name
            subscription.data_limit_gb = plan.data_limit_gb
            subscription.call_minutes_limit = plan.call_minutes_limit
            subscription.features = plan.features
            subscription.catalog_plan_id = plan.id
        subscription.monthly_cost = rec.proposed_cost
        db.flush()
        save(db, PlanHistory(
            subscription_id=subscription.id,
            recommendation_id=rec.id,
            change_type="plan_change" if rec.action == "switch_plan" else "price_change",
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
            "monthly_savings": rec.estimated_monthly_savings,
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
