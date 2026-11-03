"""
Negotiation Agent Service — DB orchestration for negotiation briefs.

The twin does not negotiate: no provider is contacted.  It prepares a brief
(what to ask for, the walk-away price, supporting facts, a draft message),
and the *user* contacts the provider and reports the answer.

Integrates:
  - Usage Analyzer     → picks the subscription and supplies the evidence
  - Plan alternatives  → the best catalog alternative is the walk-away anchor
  - Privacy Mediator   → usage figures appear in the draft only if the user
                         granted ``share_usage_with_provider``
  - Brief logic        → pure rules (brief.py)
  - Audit Logger       → every brief and every reported outcome, in the same
                         transaction as the change

An accepted, reported price can then become a plan-switching proposal
(``apply_negotiated_price``); it is the user's report, never an assumption.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import config
from app.core.errors import ConflictError
from app.core.run_context import current_run_id
from app.models import NegotiationBrief, Subscription
from app.modules.audit_logger.service import build_audit_log
from app.modules.negotiation_agent.brief import build_brief
from app.modules.plan_switching.executor import MINIMUM_SAVINGS_PCT
from app.modules.plan_switching.service import find_alternatives, reported_deal
from app.modules.privacy_mediator.service import has_consent, require_consent
from app.modules.usage_analyzer.service import analyze_subscription, find_optimization_target
from app.services.base_service import get_or_raise, save, transactional
from app.services.user_service import require_user

_AGENT = "negotiation_agent"
OUTCOMES = ("accepted", "declined", "dismissed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_target(db: Session, user_id: int, subscription_id: Optional[int]) -> Dict[str, Any]:
    """
    The analysis of the subscription to prepare a brief for.

    Raises:
        ValueError:    If the given subscription is not the user's active one.
        ConflictError: If nothing is underutilized (no case to make).
    """
    if subscription_id is not None:
        sub = (
            db.query(Subscription)
            .filter(Subscription.id == subscription_id, Subscription.user_id == user_id,
                    Subscription.is_active == True)
            .first()
        )
        if not sub:
            raise ValueError(f"No active subscription {subscription_id} found for user {user_id}")
        target = analyze_subscription(db, user_id, sub)
    else:
        target = find_optimization_target(db, user_id)
    if target is None or target.get("recommendation") != "downgrade":
        raise ConflictError(
            f"No underutilized subscription to negotiate for user {user_id}: "
            "a request for a discount needs capacity you are not using"
        )
    return target


def open_brief(db: Session, subscription_id: int) -> Optional[NegotiationBrief]:
    """The subscription's brief still waiting for the user's outcome, if any."""
    return (
        db.query(NegotiationBrief)
        .filter(NegotiationBrief.subscription_id == subscription_id, NegotiationBrief.status == "open")
        .order_by(NegotiationBrief.id.desc())
        .first()
    )


def brief_block_reason(db: Session, subscription_id: int) -> Optional[str]:
    """
    Why no new brief should be prepared for the subscription right now, or None.

      - an earlier brief still waits for the provider's answer;
      - an accepted price was reported and has not been applied yet;
      - the user declined or dismissed a brief within the cool-down.
    """
    pending = open_brief(db, subscription_id)
    if pending is not None:
        return f"brief #{pending.id} is waiting for the provider's answer"

    sub = db.get(Subscription, subscription_id)
    deal = reported_deal(db, sub)
    if deal is not None and deal.status == "accepted":
        return f"you reported an accepted price on brief #{deal.id}; it can be proposed for your approval"

    if config.REJECTION_COOLDOWN_DAYS > 0:
        since = _now() - timedelta(days=config.REJECTION_COOLDOWN_DAYS)
        recent = (
            db.query(NegotiationBrief)
            .filter(NegotiationBrief.subscription_id == subscription_id,
                    NegotiationBrief.status.in_(("declined", "dismissed")),
                    NegotiationBrief.reported_at >= since)
            .order_by(NegotiationBrief.id.desc())
            .first()
        )
        if recent is not None:
            return (f"brief #{recent.id} was {recent.status} on {recent.reported_at:%Y-%m-%d}; "
                    f"no new one for {config.REJECTION_COOLDOWN_DAYS} days")
    return None


def brief_out(brief: NegotiationBrief) -> Dict[str, Any]:
    """The brief as the API returns it (content expanded from JSON)."""
    return {
        "id": brief.id,
        "user_id": brief.user_id,
        "subscription_id": brief.subscription_id,
        "provider": brief.subscription.provider,
        "plan_name": brief.subscription.plan_name,
        "run_id": brief.run_id,
        "status": brief.status,
        "current_cost": brief.current_cost,
        "effective_cost": brief.effective_cost,
        "opening_ask": brief.opening_ask,
        "walk_away_price": brief.walk_away_price,
        **json.loads(brief.content),
        "draft_message": brief.draft_message,
        "included_usage": brief.included_usage,
        "agreed_price": brief.agreed_price,
        "outcome_note": brief.outcome_note,
        "reported_at": brief.reported_at,
        "created_at": brief.created_at,
        "contacts_provider": False,     # the twin never sends anything
    }


def prepare_brief(db: Session, user_id: int, subscription_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Prepare a negotiation brief for the user's most underutilized plan.

    Raises:
        ValueError:           If user or subscription not found.
        ConflictError:        If nothing is underutilized, or a brief for the
                              subscription still awaits the user's outcome.
        ConsentRequiredError: If ``usage_analysis`` or ``negotiate_on_behalf`` is missing.
    """
    require_user(db, user_id)
    require_consent(db, user_id, "usage_analysis", _AGENT)
    require_consent(db, user_id, "negotiate_on_behalf", _AGENT)

    analysis = _resolve_target(db, user_id, subscription_id)
    subscription = db.get(Subscription, analysis["subscription_id"])

    blocked = brief_block_reason(db, subscription.id)
    if blocked is not None:
        raise ConflictError(f"No new brief for subscription {subscription.id}: {blocked}")

    fits = find_alternatives(db, user_id, subscription, analysis)["fits"]
    share = has_consent(db, user_id, "share_usage_with_provider")
    brief = build_brief(
        plan={
            "provider": subscription.provider, "plan_name": subscription.plan_name,
            "monthly_cost": subscription.monthly_cost,
            "data_limit_gb": subscription.data_limit_gb,
            "call_minutes_limit": subscription.call_minutes_limit,
        },
        analysis=analysis,
        alternatives=fits,
        min_savings_pct=MINIMUM_SAVINGS_PCT,
        share_usage=share,
    )
    content = {k: brief[k] for k in ("evidence", "leverage", "fallback", "caveats")}

    with transactional(db):
        row = save(db, NegotiationBrief(
            user_id=user_id, subscription_id=subscription.id, run_id=current_run_id.get(),
            status="open", current_cost=brief["current_cost"], effective_cost=brief["effective_cost"],
            opening_ask=brief["opening_ask"], walk_away_price=brief["walk_away_price"],
            content=json.dumps(content), draft_message=brief["draft_message"],
            included_usage=brief["included_usage"],
        ))
        save(db, build_audit_log(db, user_id, "negotiation", {
            "kind": "brief", "brief_id": row.id, "subscription_id": subscription.id,
            "provider": subscription.provider, "plan_name": subscription.plan_name,
            "current_cost": brief["current_cost"], "opening_ask": brief["opening_ask"],
            "walk_away_price": brief["walk_away_price"], "included_usage": share,
        }))
    return brief_out(row)


def report_outcome(
    db: Session, brief_id: int, outcome: str, agreed_price: Optional[float] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record what the provider answered.

    ``accepted`` needs the agreed monthly price, which must be lower than the
    current one.  Reporting is the user's own statement; it is not verified.

    Raises:
        ValueError:    If the brief does not exist or the report is invalid.
        ConflictError: If the outcome was already reported.
    """
    brief = get_or_raise(db, NegotiationBrief, brief_id, "Negotiation brief")
    if brief.status != "open":
        raise ConflictError(f"Brief #{brief.id} is already {brief.status}")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(OUTCOMES)}")
    if outcome == "accepted":
        if agreed_price is None or agreed_price <= 0:
            raise ValueError("An accepted outcome needs the agreed monthly price")
        if agreed_price >= brief.current_cost:
            raise ValueError(
                f"The agreed price must be lower than the current price (₹{brief.current_cost:g})"
            )
    elif agreed_price is not None:
        raise ValueError("A price can only be reported with an accepted outcome")

    with transactional(db):
        brief.status = outcome
        brief.agreed_price = agreed_price
        brief.outcome_note = (note or "").strip() or None
        brief.reported_at = _now()
        save(db, build_audit_log(db, brief.user_id, "negotiation", {
            "kind": "outcome", "brief_id": brief.id, "outcome": outcome,
            "agreed_price": agreed_price, "current_cost": brief.current_cost,
            "provider": brief.subscription.provider, "plan_name": brief.subscription.plan_name,
        }))
    return brief_out(brief)


def require_brief(db: Session, brief_id: int) -> NegotiationBrief:
    """Fetch a brief by ID or raise ValueError."""
    return get_or_raise(db, NegotiationBrief, brief_id, "Negotiation brief")


def list_briefs(db: Session, user_id: int, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """A user's briefs, newest first, optionally by status."""
    query = db.query(NegotiationBrief).filter(NegotiationBrief.user_id == user_id)
    if status:
        query = query.filter(NegotiationBrief.status == status)
    return [brief_out(b) for b in query.order_by(NegotiationBrief.id.desc()).limit(limit).all()]
