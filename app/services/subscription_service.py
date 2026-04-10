"""
Subscription Service — Business logic for subscription, usage,
negotiation, and audit-log management.

All functions accept a SQLAlchemy Session and return ORM model objects.
Validation errors are raised as ValueError; the route layer converts
them to appropriate HTTP responses.
"""

from typing import List

from sqlalchemy.orm import Session

from app.models import Subscription, UsageData, NegotiationHistory, AuditLog
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

    Raises:
        ValueError: If monthly_cost is not a positive number.
    """
    if sub_data.get("monthly_cost", 0) <= 0:
        raise ValueError("monthly_cost must be a positive number")

    sub = Subscription(**sub_data)
    with transactional(db):
        save(db, sub)
    return sub


# ─────────────────────────────────────────────────────────────
# Usage Data
# ─────────────────────────────────────────────────────────────
def get_usage_by_user(db: Session, user_id: int) -> List[UsageData]:
    """Return all usage records for a user."""
    return db.query(UsageData).filter(UsageData.user_id == user_id).all()


def create_usage_record(db: Session, usage_data: dict) -> UsageData:
    """
    Ingest and persist a single usage record.

    Raises:
        ValueError: If period_start >= period_end.
    """
    if usage_data.get("period_start") >= usage_data.get("period_end"):
        raise ValueError("period_start must be earlier than period_end")

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
