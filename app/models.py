"""
ORM Models — All SQLAlchemy table definitions for the Agentic Digital Twin.

Tables:
  - users
  - subscriptions
  - usage_data
  - negotiation_history
  - audit_logs

Every table includes:
  - Primary key (id)
  - created_at timestamp (UTC, auto-set)
  - Proper foreign key constraints with cascade behaviour
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    """Return the current UTC timestamp (used as a column default)."""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────
# 1. Users
# ─────────────────────────────────────────────────────────────
class User(Base):
    """Registered end-user of the digital-twin system."""

    __tablename__ = "users"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(120), nullable=False)
    email      = Column(String(255), unique=True, nullable=False, index=True)
    phone      = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────
    subscriptions = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )
    usage_records = relationship(
        "UsageData", back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs = relationship(
        "AuditLog", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User id={self.id} email={self.email!r}>"


# ─────────────────────────────────────────────────────────────
# 2. Subscriptions
# ─────────────────────────────────────────────────────────────
class Subscription(Base):
    """A user's subscription to a service provider (Netflix, Jio, AWS, etc.)."""

    __tablename__ = "subscriptions"

    id                     = Column(Integer, primary_key=True, index=True)
    user_id                = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    provider               = Column(String(100), nullable=False)
    plan_name              = Column(String(100), nullable=False)
    monthly_cost           = Column(Float, nullable=False)
    data_limit_gb          = Column(Float, nullable=True)            # NULL = unlimited
    call_minutes_limit     = Column(Integer, nullable=True)          # NULL = unlimited
    features               = Column(Text, nullable=True)             # JSON-encoded extras

    is_active              = Column(Boolean, default=True, nullable=False)
    start_date             = Column(DateTime, default=_utcnow, nullable=False)
    end_date               = Column(DateTime, nullable=True)

    previous_plan_snapshot = Column(Text, nullable=True)             # JSON blob for rollback
    created_at             = Column(DateTime, default=_utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────
    user = relationship("User", back_populates="subscriptions")
    negotiations = relationship(
        "NegotiationHistory", back_populates="subscription", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Subscription id={self.id} provider={self.provider!r} plan={self.plan_name!r}>"


# ─────────────────────────────────────────────────────────────
# 3. Usage Data
# ─────────────────────────────────────────────────────────────
class UsageData(Base):
    """Per-period consumption metrics for a user's service."""

    __tablename__ = "usage_data"

    id                = Column(Integer, primary_key=True, index=True)
    user_id           = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    provider          = Column(String(100), nullable=False)
    period_start      = Column(DateTime, nullable=False)
    period_end        = Column(DateTime, nullable=False)

    data_used_gb      = Column(Float, default=0.0)
    call_minutes_used = Column(Integer, default=0)
    billing_amount    = Column(Float, default=0.0)

    created_at        = Column(DateTime, default=_utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────
    user = relationship("User", back_populates="usage_records")

    def __repr__(self):
        return f"<UsageData id={self.id} provider={self.provider!r} billing={self.billing_amount}>"


# ─────────────────────────────────────────────────────────────
# 4. Negotiation History
# ─────────────────────────────────────────────────────────────
class NegotiationHistory(Base):
    """One round of an offer–counteroffer negotiation cycle."""

    __tablename__ = "negotiation_history"

    id               = Column(Integer, primary_key=True, index=True)
    subscription_id  = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)

    round_number     = Column(Integer, nullable=False)
    agent_offer      = Column(Float, nullable=False)
    provider_counter = Column(Float, nullable=True)
    status           = Column(String(20), nullable=False, default="pending")  # pending | accepted | rejected | final

    notes            = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=_utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────
    subscription = relationship("Subscription", back_populates="negotiations")

    def __repr__(self):
        return f"<NegotiationHistory id={self.id} round={self.round_number} status={self.status!r}>"


# ─────────────────────────────────────────────────────────────
# 5. Audit Logs
# ─────────────────────────────────────────────────────────────
class AuditLog(Base):
    """Immutable record of every decision the digital twin makes."""

    __tablename__ = "audit_logs"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    action      = Column(String(60), nullable=False)         # analyze | negotiate | switch | rollback
    module      = Column(String(60), nullable=False)         # originating module name
    description = Column(Text, nullable=False)               # plain-English explanation
    details     = Column(Text, nullable=True)                # JSON-encoded payload

    created_at  = Column(DateTime, default=_utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────
    user = relationship("User", back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog id={self.id} action={self.action!r} module={self.module!r}>"
