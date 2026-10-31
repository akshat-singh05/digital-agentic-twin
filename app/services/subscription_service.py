"""
Subscription Service — Business logic for subscription, usage,
negotiation, plan-history and audit-log management.

All functions accept a SQLAlchemy Session and return ORM model objects.
Validation errors are raised as ValueError; the route layer converts
them to appropriate HTTP responses.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import (
    AuditLog, NegotiationHistory, PlanCatalog, PlanHistory, Subscription, UsageData,
)
from app.services.base_service import get_or_raise, get_or_none, save, transactional


# ─────────────────────────────────────────────────────────────
# Subscriptions
# ─────────────────────────────────────────────────────────────
def get_subscription_by_id(db: Session, sub_id: int):
    """Fetch a single subscription by primary key. Returns None if not found."""
    return get_or_none(db, Subscription, sub_id)


def require_subscription(db: Session, sub_id: int) -> Subscription:
    """Fetch a subscription by ID or raise ValueError if not found."""
    return get_or_raise(db, Subscription, sub_id, "Subscription")


def get_subscriptions_by_user(db: Session, user_id: int) -> List[Subscription]:
    """Return all subscriptions belonging to a user."""
    return db.query(Subscription).filter(Subscription.user_id == user_id).all()


def create_subscription(db: Session, sub_data: dict) -> Subscription:
    """
    Create and persist a new subscription.

    Usage records the user already ingested for this provider without a
    subscription are linked to the new one.

    Raises:
        ValueError: If monthly_cost is not positive or catalog_plan_id is
                    unknown / belongs to another provider.
    """
    if sub_data.get("monthly_cost", 0) <= 0:
        raise ValueError("monthly_cost must be a positive number")

    catalog_plan_id = sub_data.get("catalog_plan_id")
    if catalog_plan_id is not None:
        plan = get_or_none(db, PlanCatalog, catalog_plan_id)
        if plan is None:
            raise ValueError(f"Catalog plan with id {catalog_plan_id} not found")
        if plan.provider != sub_data["provider"]:
            raise ValueError(
                f"Catalog plan {catalog_plan_id} belongs to {plan.provider}, "
                f"not {sub_data['provider']}"
            )

    sub = Subscription(**sub_data)
    with transactional(db):
        save(db, sub)
        (
            db.query(UsageData)
            .filter(
                UsageData.user_id == sub.user_id,
                UsageData.provider == sub.provider,
                UsageData.subscription_id.is_(None),
            )
            .update({UsageData.subscription_id: sub.id}, synchronize_session=False)
        )
    return sub


# ─────────────────────────────────────────────────────────────
# Usage Data
# ─────────────────────────────────────────────────────────────
def get_usage_by_user(db: Session, user_id: int) -> List[UsageData]:
    """Return all usage records for a user."""
    return db.query(UsageData).filter(UsageData.user_id == user_id).all()


def _resolve_usage_subscription(db: Session, usage_data: dict) -> Optional[int]:
    """
    Return the subscription a usage record belongs to.

    An explicit ``subscription_id`` must be the user's and match the
    provider; otherwise the user's subscription with that provider is used
    (active first, then most recent), or None if there is none yet.
    """
    sub_id = usage_data.get("subscription_id")
    if sub_id is not None:
        sub = get_or_none(db, Subscription, sub_id)
        if sub is None or sub.user_id != usage_data["user_id"]:
            raise ValueError(
                f"Subscription {sub_id} does not belong to user {usage_data['user_id']}"
            )
        if sub.provider != usage_data["provider"]:
            raise ValueError(
                f"Subscription {sub_id} is with {sub.provider}, not {usage_data['provider']}"
            )
        return sub_id

    sub = (
        db.query(Subscription)
        .filter(
            Subscription.user_id == usage_data["user_id"],
            Subscription.provider == usage_data["provider"],
        )
        .order_by(Subscription.is_active.desc(), Subscription.id.desc())
        .first()
    )
    return sub.id if sub else None


def create_usage_record(db: Session, usage_data: dict) -> UsageData:
    """
    Ingest and persist a single usage record, linked to its subscription.

    Raises:
        ValueError: If period_start >= period_end, or the given
                    subscription does not match the user / provider.
    """
    if usage_data.get("period_start") >= usage_data.get("period_end"):
        raise ValueError("period_start must be earlier than period_end")

    usage_data = dict(usage_data, subscription_id=_resolve_usage_subscription(db, usage_data))

    record = UsageData(**usage_data)
    with transactional(db):
        save(db, record)
    return record


# ─────────────────────────────────────────────────────────────
# Negotiation History
# ─────────────────────────────────────────────────────────────
def get_negotiation_rounds(db: Session, subscription_id: int) -> List[NegotiationHistory]:
    """Return all negotiation rounds for a subscription, ordered by round number."""
    return (
        db.query(NegotiationHistory)
        .filter(NegotiationHistory.subscription_id == subscription_id)
        .order_by(NegotiationHistory.round_number)
        .all()
    )


# ─────────────────────────────────────────────────────────────
# Plan History
# ─────────────────────────────────────────────────────────────
def get_plan_history(db: Session, subscription_id: int) -> List[PlanHistory]:
    """Return every applied change to a subscription, oldest first."""
    return (
        db.query(PlanHistory)
        .filter(PlanHistory.subscription_id == subscription_id)
        .order_by(PlanHistory.id)
        .all()
    )


# ─────────────────────────────────────────────────────────────
# Audit Logs
# ─────────────────────────────────────────────────────────────
def get_audit_logs_by_user(
    db: Session, user_id: int, skip: int = 0, limit: int = 100
) -> List[AuditLog]:
    """Return audit logs for a user, most recent first, with pagination."""
    return (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
