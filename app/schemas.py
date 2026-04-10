"""
Pydantic Schemas — Request / response models for the Agentic Digital Twin API.

Conventions:
  - Every successful response is wrapped in:  {"status": "success", "data": ...}
  - Every error response follows:             {"status": "error",   "message": ...}
  - *Create  schemas → request bodies (with field-level validation)
  - *Out     schemas → the inner 'data' payload
"""

from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator


# ─────────────────────────────────────────────────────────────
# Standard Response Wrappers
# ─────────────────────────────────────────────────────────────
DataT = TypeVar("DataT")


class SuccessResponse(BaseModel, Generic[DataT]):
    """Uniform success envelope returned by every endpoint."""
    status: str = "success"
    data: DataT


class ErrorResponse(BaseModel):
    """Uniform error envelope returned on failures."""
    status: str = "error"
    message: str


# ─────────────────────────────────────────────────────────────
# User Schemas
# ─────────────────────────────────────────────────────────────
class UserCreate(BaseModel):
    """Payload to register a new user."""
    name: str = Field(
        ..., min_length=1, max_length=120,
        description="Full name of the user",
        examples=["Aarav Sharma"],
    )
    email: str = Field(
        ..., min_length=5, max_length=255,
        description="Unique email address",
        examples=["aarav@example.com"],
    )
    phone: Optional[str] = Field(
        None, max_length=20,
        description="Phone number (optional)",
        examples=["+91-98765-43210"],
    )

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        """Basic email format validation."""
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format — must contain '@' and a domain")
        return v

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, v: str) -> str:
        """Ensure name is not just whitespace."""
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty or whitespace-only")
        return v


class UserOut(BaseModel):
    """Public representation of a user."""
    id: int
    name: str
    email: str
    phone: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────
# Subscription Schemas
# ─────────────────────────────────────────────────────────────
class SubscriptionCreate(BaseModel):
    """Payload to create a new subscription."""
    user_id: int = Field(..., gt=0, description="ID of the user who owns this subscription")
    provider: str = Field(
        ..., min_length=1, max_length=100,
        description="Service provider name",
        examples=["Jio", "Airtel", "Netflix"],
    )
    plan_name: str = Field(
        ..., min_length=1, max_length=100,
        description="Name of the subscription plan",
        examples=["Gold 599"],
    )
    monthly_cost: float = Field(
        ..., gt=0,
        description="Monthly cost in ₹ (must be positive)",
        examples=[599.0],
    )
    data_limit_gb: Optional[float] = Field(
        None, ge=0,
        description="Data cap in GB (null = unlimited)",
        examples=[100.0],
    )
    call_minutes_limit: Optional[int] = Field(
        None, ge=0,
        description="Call minutes cap (null = unlimited)",
        examples=[500],
    )
    features: Optional[str] = Field(
        None, max_length=1000,
        description="JSON-encoded feature flags",
        examples=['{"5g": true, "hotspot": true}'],
    )


class SubscriptionOut(BaseModel):
    """Public representation of a subscription."""
    id: int
    user_id: int
    provider: str
    plan_name: str
    monthly_cost: float
    data_limit_gb: Optional[float] = None
    call_minutes_limit: Optional[int] = None
    features: Optional[str] = None
    is_active: bool
    start_date: datetime
    end_date: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────
# Usage Data Schemas
# ─────────────────────────────────────────────────────────────
class UsageDataCreate(BaseModel):
    """Payload to ingest one usage record."""
    user_id: int = Field(..., gt=0, description="ID of the user")
    provider: str = Field(
        ..., min_length=1, max_length=100,
        description="Service provider name",
    )
    period_start: datetime = Field(..., description="Start of the billing period")
    period_end: datetime = Field(..., description="End of the billing period")
    data_used_gb: float = Field(0.0, ge=0, description="Data consumed in GB")
    call_minutes_used: int = Field(0, ge=0, description="Call minutes consumed")
    billing_amount: float = Field(0.0, ge=0, description="Billed amount in ₹")

    @field_validator("period_end")
    @classmethod
    def validate_period_order(cls, v, info):
        """Ensure period_end is after period_start."""
        start = info.data.get("period_start")
        if start and v <= start:
            raise ValueError("period_end must be later than period_start")
        return v


class UsageDataOut(BaseModel):
    """Public representation of a usage record."""
    id: int
    user_id: int
    provider: str
    period_start: datetime
    period_end: datetime
    data_used_gb: float
    call_minutes_used: int
    billing_amount: float
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────
# Negotiation Schemas
# ─────────────────────────────────────────────────────────────
class NegotiationRoundOut(BaseModel):
    """A single round in a negotiation session."""
    id: int
    subscription_id: int
    round_number: int
    agent_offer: float
    provider_counter: Optional[float] = None
    status: str
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NegotiationSessionOut(BaseModel):
    """Aggregated view returned after a full negotiation run."""
    subscription_id: int
    provider: str
    original_cost: float
    final_offer: float
    savings_pct: float
    rounds: List[NegotiationRoundOut]
    outcome: str  # accepted | rejected


# ─────────────────────────────────────────────────────────────
# Audit Log Schemas
# ─────────────────────────────────────────────────────────────
class AuditLogOut(BaseModel):
    """Public representation of an audit log entry."""
    id: int
    user_id: int
    action: str
    module: str
    description: str
    details: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────
# Usage Analysis Result (returned by the analyzer module)
# ─────────────────────────────────────────────────────────────
class UsageAnalysisResult(BaseModel):
    """Output of the usage analyzer module."""
    user_id: int
    provider: str
    avg_data_used_gb: float
    avg_call_minutes: float
    avg_billing: float
    recommendation: str          # "upgrade" | "downgrade" | "keep"
    reason: str                  # plain-English explanation


# ─────────────────────────────────────────────────────────────
# Plan Switching Result (returned by the switching module)
# ─────────────────────────────────────────────────────────────
class PlanSwitchResult(BaseModel):
    """Output of the plan switching module."""
    applied: bool
    reason: str
    projected_cost: float
    risk_flag: str               # "low" | "medium" | "high"
    rollback: bool
    subscription_id: int
    provider: str
    plan_name: str
