"""
ORM Models — All SQLAlchemy table definitions for the Agentic Digital Twin.

Tables:
  - users
  - subscriptions
  - usage_data
  - negotiation_history
  - audit_logs
  - plan_catalog        (plans providers offer — the alternatives)
  - user_preferences    (constraints the twin must respect)
  - consents            (per-scope data / action permissions)
  - agent_runs, agent_run_steps (orchestrator execution history)
  - recommendations     (proposed actions awaiting a user decision)
  - plan_history        (every applied change to a subscription)

Every table includes:
  - Primary key (id)
  - created_at / updated_at timestamp (UTC, auto-set)
  - Proper foreign key constraints with cascade behaviour

The schema is created and changed only through Alembic migrations
(migrations/versions/).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    """Return the current UTC timestamp (used as a column default)."""
    return datetime.now(timezone.utc)


def _in_check(values):
    """SQL for a CHECK that a status column holds one of ``values``."""
    return "status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


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
    preferences = relationship(
        "UserPreference", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    consents = relationship(
        "Consent", back_populates="user", cascade="all, delete-orphan"
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
    catalog_plan_id        = Column(Integer, ForeignKey("plan_catalog.id", ondelete="SET NULL"), nullable=True, index=True)

    # Terms are copied from the catalog at sign-up and may diverge later
    # (e.g. a negotiated price), so they are stored on the subscription.
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
    catalog_plan = relationship("PlanCatalog")
    negotiations = relationship(
        "NegotiationHistory", back_populates="subscription", cascade="all, delete-orphan"
    )
    usage_records = relationship("UsageData", back_populates="subscription")
    history = relationship(
        "PlanHistory", back_populates="subscription", cascade="all, delete-orphan",
        order_by="PlanHistory.id",
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
    # NULL only for legacy rows that could not be matched to a subscription
    subscription_id   = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True)

    provider          = Column(String(100), nullable=False)
    period_start      = Column(DateTime, nullable=False)
    period_end        = Column(DateTime, nullable=False)

    data_used_gb      = Column(Float, default=0.0)
    call_minutes_used = Column(Integer, default=0)
    billing_amount    = Column(Float, default=0.0)

    created_at        = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_usage_data_subscription_period", "subscription_id", "period_start"),
    )

    # ── Relationships ────────────────────────────────────────
    user = relationship("User", back_populates="usage_records")
    subscription = relationship("Subscription", back_populates="usage_records")

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
    run_id      = Column(Integer, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)

    action      = Column(String(60), nullable=False)         # analysis | negotiation | switching | consent
    module      = Column(String(60), nullable=False)         # originating module name
    description = Column(Text, nullable=False)               # plain-English explanation
    details     = Column(Text, nullable=True)                # JSON-encoded payload

    created_at  = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_audit_logs_user_created", "user_id", "created_at"),
    )

    # ── Relationships ────────────────────────────────────────
    user = relationship("User", back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog id={self.id} action={self.action!r} module={self.module!r}>"


# ─────────────────────────────────────────────────────────────
# 6. Plan Catalog
# ─────────────────────────────────────────────────────────────
PLAN_CATEGORIES = ("mobile", "broadband", "streaming", "music", "cloud")


class PlanCatalog(Base):
    """A plan a provider offers — the alternatives the twin can recommend."""

    __tablename__ = "plan_catalog"

    id                 = Column(Integer, primary_key=True)
    provider           = Column(String(100), nullable=False, index=True)
    plan_name          = Column(String(100), nullable=False)
    category           = Column(String(30), nullable=False)
    monthly_cost       = Column(Float, nullable=False)
    data_limit_gb      = Column(Float, nullable=True)                # NULL = unlimited / not applicable
    call_minutes_limit = Column(Integer, nullable=True)              # NULL = unlimited / not applicable
    features           = Column(Text, nullable=True)                 # JSON-encoded extras
    is_available       = Column(Boolean, default=True, nullable=False)
    source             = Column(String(30), nullable=False, default="manual")  # demo_seed | manual
    created_at         = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "plan_name", name="uq_plan_catalog_provider_plan"),
        CheckConstraint("monthly_cost > 0", name="positive_cost"),
    )

    def __repr__(self):
        return f"<PlanCatalog id={self.id} provider={self.provider!r} plan={self.plan_name!r}>"


# ─────────────────────────────────────────────────────────────
# 7. User Preferences
# ─────────────────────────────────────────────────────────────
class UserPreference(Base):
    """Constraints the twin must respect when recommending a plan (1:1 with user)."""

    __tablename__ = "user_preferences"

    id                    = Column(Integer, primary_key=True)
    user_id               = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    max_monthly_budget    = Column(Float, nullable=True)
    min_data_gb           = Column(Float, nullable=True)
    min_call_minutes      = Column(Integer, nullable=True)
    required_features     = Column(Text, nullable=True)              # JSON list of feature keys
    preferred_providers   = Column(Text, nullable=True)              # JSON list of provider names
    allow_provider_change = Column(Boolean, default=False, nullable=False)
    updated_at            = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User", back_populates="preferences")


# ─────────────────────────────────────────────────────────────
# 8. Consents
# ─────────────────────────────────────────────────────────────
class Consent(Base):
    """
    Whether the user allows one scope of data use or action.

    No row means the scope has never been granted (deny by default).
    """

    __tablename__ = "consents"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    scope      = Column(String(50), nullable=False)
    granted    = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "scope", name="uq_consents_user_scope"),
    )

    user = relationship("User", back_populates="consents")


# ─────────────────────────────────────────────────────────────
# 9. Agent Runs
# ─────────────────────────────────────────────────────────────
RUN_STATUSES = ("running", "awaiting_approval", "completed", "partial", "failed")
STEP_STATUSES = ("running", "completed", "skipped", "failed")


class AgentRun(Base):
    """One execution of the orchestrator for a user."""

    __tablename__ = "agent_runs"

    id          = Column(Integer, primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger     = Column(String(30), nullable=False, default="api")   # api | schedule
    status      = Column(String(20), nullable=False, default="running")
    started_at  = Column(DateTime, default=_utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    error       = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(_in_check(RUN_STATUSES), name="valid_status"),
    )

    steps = relationship(
        "AgentRunStep", back_populates="run", cascade="all, delete-orphan",
        order_by="AgentRunStep.id",
    )


class AgentRunStep(Base):
    """One agent's step within a run (timing, outcome, error)."""

    __tablename__ = "agent_run_steps"

    id          = Column(Integer, primary_key=True)
    run_id      = Column(Integer, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    agent       = Column(String(50), nullable=False)   # usage_analyzer | privacy_mediator | negotiation_agent | plan_switching
    status      = Column(String(20), nullable=False, default="running")
    started_at  = Column(DateTime, default=_utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    summary     = Column(Text, nullable=True)          # JSON-encoded step output summary
    error       = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(_in_check(STEP_STATUSES), name="valid_status"),
    )

    run = relationship("AgentRun", back_populates="steps")


# ─────────────────────────────────────────────────────────────
# 10. Recommendations
# ─────────────────────────────────────────────────────────────
RECOMMENDATION_STATUSES = ("proposed", "approved", "rejected", "executed", "expired", "failed")


class Recommendation(Base):
    """An action the twin proposes; consequential ones wait for user approval."""

    __tablename__ = "recommendations"

    id                        = Column(Integer, primary_key=True)
    user_id                   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id           = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id                    = Column(Integer, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    proposed_plan_id          = Column(Integer, ForeignKey("plan_catalog.id", ondelete="SET NULL"), nullable=True)

    action                    = Column(String(30), nullable=False)    # switch_plan | apply_negotiated_price | keep
    current_cost              = Column(Float, nullable=False)
    proposed_cost             = Column(Float, nullable=True)
    estimated_monthly_savings = Column(Float, nullable=True)
    rationale                 = Column(Text, nullable=False)          # plain-English why
    evidence                  = Column(Text, nullable=True)           # JSON: data the decision rests on
    requires_approval         = Column(Boolean, nullable=False, default=True)
    status                    = Column(String(20), nullable=False, default="proposed")

    created_at                = Column(DateTime, default=_utcnow, nullable=False)
    decided_at                = Column(DateTime, nullable=True)
    executed_at               = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(_in_check(RECOMMENDATION_STATUSES), name="valid_status"),
    )

    subscription = relationship("Subscription")
    proposed_plan = relationship("PlanCatalog")


# ─────────────────────────────────────────────────────────────
# 11. Plan History
# ─────────────────────────────────────────────────────────────
class PlanHistory(Base):
    """Every applied change to a subscription's terms (for history and rollback)."""

    __tablename__ = "plan_history"

    id                = Column(Integer, primary_key=True)
    subscription_id   = Column(Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True)
    change_type       = Column(String(30), nullable=False)   # price_change | plan_change | rollback
    previous_terms    = Column(Text, nullable=False)         # JSON snapshot before the change
    new_terms         = Column(Text, nullable=False)         # JSON snapshot after the change
    previous_cost     = Column(Float, nullable=False)
    new_cost          = Column(Float, nullable=False)
    reason            = Column(Text, nullable=True)
    created_at        = Column(DateTime, default=_utcnow, nullable=False)

    subscription = relationship("Subscription", back_populates="history")
