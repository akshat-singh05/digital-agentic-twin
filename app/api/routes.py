"""
API Routes — All REST endpoints for the Agentic Digital Twin.

This layer is intentionally thin:
  ✓ Parse the request
  ✓ Call the appropriate service function
  ✓ Wrap the result in a standard response envelope

Response contract:
  Success → {"status": "success", "data": <payload>}
  Error   → {"status": "error",   "message": "<description>"}
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.logger import get_logger

logger = get_logger(__name__)

from app.database import get_db
from app.schemas import (
    SuccessResponse,
    UserCreate,
    UserOut,
    SubscriptionCreate,
    SubscriptionOut,
    UsageDataCreate,
    UsageDataOut,
    NegotiationRoundOut,
    AuditLogOut,
)
from app.services import user_service, subscription_service
from app.modules.usage_analyzer.service import analyze_user_usage
from app.modules.privacy_mediator.service import get_sanitized_usage
from app.modules.negotiation_agent.service import run_negotiation
from app.modules.plan_switching.service import switch_plan
from app.modules.audit_logger.service import create_audit_log, get_user_audit_logs
from app.services.system_service import run_full_cycle

router = APIRouter()


# ─────────────────────────────────────────────────────────────
# Helper — convert service-layer ValueError to HTTPException
# ─────────────────────────────────────────────────────────────
def _raise_not_found(e: ValueError):
    """Convert a service ValueError into a 404 HTTPException."""
    raise HTTPException(
        status_code=404,
        detail={"status": "error", "message": str(e)},
    )


def _raise_validation(e: ValueError):
    """Convert a service ValueError into a 422 HTTPException."""
    raise HTTPException(
        status_code=422,
        detail={"status": "error", "message": str(e)},
    )


# ═════════════════════════════════════════════════════════════
#  USERS
# ═════════════════════════════════════════════════════════════
@router.post(
    "/users",
    response_model=SuccessResponse[UserOut],
    status_code=201,
    tags=["Users"],
)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user.

    Validates that the email is not already taken.
    """
    try:
        user = user_service.create_user(db, payload.model_dump())
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail={"status": "error", "message": str(e)},
        )
    return {"status": "success", "data": user}


@router.get(
    "/users",
    response_model=SuccessResponse[List[UserOut]],
    tags=["Users"],
)
def list_users(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all registered users with pagination."""
    users = user_service.get_all_users(db, skip=skip, limit=limit)
    return {"status": "success", "data": users}


@router.get(
    "/users/{user_id}",
    response_model=SuccessResponse[UserOut],
    tags=["Users"],
)
def get_user(user_id: int, db: Session = Depends(get_db)):
    """Retrieve a single user by ID."""
    try:
        user = user_service.require_user(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": user}


# ═════════════════════════════════════════════════════════════
#  SUBSCRIPTIONS
# ═════════════════════════════════════════════════════════════
@router.post(
    "/subscriptions",
    response_model=SuccessResponse[SubscriptionOut],
    status_code=201,
    tags=["Subscriptions"],
)
def create_subscription(payload: SubscriptionCreate, db: Session = Depends(get_db)):
    """
    Create a new subscription for a user.

    Validates user existence and positive monthly cost.
    """
    try:
        user_service.require_user(db, payload.user_id)
    except ValueError as e:
        _raise_not_found(e)

    try:
        sub = subscription_service.create_subscription(db, payload.model_dump())
    except ValueError as e:
        _raise_validation(e)
    return {"status": "success", "data": sub}


@router.get(
    "/subscriptions/user/{user_id}",
    response_model=SuccessResponse[List[SubscriptionOut]],
    tags=["Subscriptions"],
)
def list_subscriptions(user_id: int, db: Session = Depends(get_db)):
    """List all subscriptions belonging to a user."""
    try:
        user_service.require_user(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    subs = subscription_service.get_subscriptions_by_user(db, user_id)
    return {"status": "success", "data": subs}


@router.get(
    "/subscriptions/{sub_id}",
    response_model=SuccessResponse[SubscriptionOut],
    tags=["Subscriptions"],
)
def get_subscription(sub_id: int, db: Session = Depends(get_db)):
    """Retrieve a single subscription by ID."""
    try:
        sub = subscription_service.require_subscription(db, sub_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": sub}


# ═════════════════════════════════════════════════════════════
#  USAGE DATA
# ═════════════════════════════════════════════════════════════
@router.post(
    "/usage",
    response_model=SuccessResponse[UsageDataOut],
    status_code=201,
    tags=["Usage"],
)
def record_usage(payload: UsageDataCreate, db: Session = Depends(get_db)):
    """
    Ingest a single usage record.

    Validates user existence and period ordering.
    """
    try:
        user_service.require_user(db, payload.user_id)
    except ValueError as e:
        _raise_not_found(e)

    try:
        record = subscription_service.create_usage_record(db, payload.model_dump())
    except ValueError as e:
        _raise_validation(e)
    return {"status": "success", "data": record}


@router.get(
    "/usage/user/{user_id}",
    response_model=SuccessResponse[List[UsageDataOut]],
    tags=["Usage"],
)
def list_usage(user_id: int, db: Session = Depends(get_db)):
    """List all usage records for a user."""
    try:
        user_service.require_user(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    records = subscription_service.get_usage_by_user(db, user_id)
    return {"status": "success", "data": records}


# ═════════════════════════════════════════════════════════════
#  NEGOTIATION HISTORY
# ═════════════════════════════════════════════════════════════
@router.get(
    "/negotiation/{subscription_id}/history",
    response_model=SuccessResponse[List[NegotiationRoundOut]],
    tags=["Negotiation"],
)
def get_negotiation_history(subscription_id: int, db: Session = Depends(get_db)):
    """Retrieve all negotiation rounds for a subscription, ordered by round."""
    try:
        subscription_service.require_subscription(db, subscription_id)
    except ValueError as e:
        _raise_not_found(e)
    rounds = subscription_service.get_negotiation_rounds(db, subscription_id)
    return {"status": "success", "data": rounds}


# ═════════════════════════════════════════════════════════════
#  AUDIT LOGS
# ═════════════════════════════════════════════════════════════
@router.get(
    "/audit/user/{user_id}",
    response_model=SuccessResponse[List[AuditLogOut]],
    tags=["Audit Logs"],
)
def get_audit_logs(
    user_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """Retrieve audit logs for a user, most recent first, with pagination."""
    try:
        user_service.require_user(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    logs = subscription_service.get_audit_logs_by_user(db, user_id, skip=skip, limit=limit)
    return {"status": "success", "data": logs}


# ═════════════════════════════════════════════════════════════
#  USAGE ANALYZER
# ═════════════════════════════════════════════════════════════
@router.post(
    "/analyze/{user_id}",
    response_model=SuccessResponse[dict],
    tags=["Usage Analyzer"],
)
def analyze_usage(user_id: int, db: Session = Depends(get_db)):
    """
    Analyze a user's subscription usage.

    Evaluates the latest active subscription against historical
    usage data and returns an efficiency score with a recommendation.
    """
    try:
        result = analyze_user_usage(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": result}


# ═════════════════════════════════════════════════════════════
#  PRIVACY MEDIATOR
# ═════════════════════════════════════════════════════════════
@router.post(
    "/sanitize/{user_id}",
    response_model=SuccessResponse[dict],
    tags=["Privacy Mediator"],
)
def sanitize_usage(user_id: int, db: Session = Depends(get_db)):
    """
    Return sanitized (differentially private) usage data for a user.

    Applies Laplace noise to sensitive fields (data_used_gb,
    call_minutes_used) so raw values are never exposed directly.
    """
    try:
        result = get_sanitized_usage(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": result}


# ═════════════════════════════════════════════════════════════
#  NEGOTIATION AGENT
# ═════════════════════════════════════════════════════════════
@router.post(
    "/negotiate/{user_id}",
    response_model=SuccessResponse[dict],
    tags=["Negotiation Agent"],
)
def negotiate_for_user(user_id: int, db: Session = Depends(get_db)):
    """
    Run an autonomous multi-round negotiation for a user.

    Analyzes usage efficiency, sanitizes data via the privacy
    mediator, then simulates offer–counteroffer rounds with
    the service provider.  Results are persisted to the
    negotiation_history table.
    """
    try:
        result = run_negotiation(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": result}


# ═════════════════════════════════════════════════════════════
#  PLAN SWITCHING
# ═════════════════════════════════════════════════════════════
@router.post(
    "/switch/{user_id}",
    response_model=SuccessResponse[dict],
    tags=["Plan Switching"],
)
def switch_user_plan(user_id: int, db: Session = Depends(get_db)):
    """
    Switch the user's plan based on the latest negotiation result.

    Evaluates KPIs (cost reduction, SLA risk) and applies the plan
    change within a transaction.  Rolls back automatically on failure.
    """
    try:
        result = switch_plan(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": result}


# ═════════════════════════════════════════════════════════════
#  AUDIT LOGGER (EXPLAINABILITY)
# ═════════════════════════════════════════════════════════════
@router.get(
    "/audit/{user_id}",
    response_model=SuccessResponse[List[AuditLogOut]],
    tags=["Audit Logger"],
)
def list_audit_logs(
    user_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """
    Retrieve all explainable audit logs for a user.

    Returns human-readable decision explanations for analysis,
    negotiation, and plan-switching actions.
    """
    try:
        logs = get_user_audit_logs(db, user_id, skip=skip, limit=limit)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": logs}


# ═════════════════════════════════════════════════════════════
#  UNIFIED PIPELINE
# ═════════════════════════════════════════════════════════════
@router.post(
    "/run-cycle/{user_id}",
    response_model=SuccessResponse[dict],
    tags=["System Pipeline"],
)
def run_cycle(user_id: int, db: Session = Depends(get_db)):
    """
    Execute the full intelligent pipeline for a user.

    Runs all modules in sequence:
      1. Usage Analysis
      2. Data Sanitization (Privacy Mediator)
      3. Autonomous Negotiation
      4. Plan Switching
      5. Explainable Audit Logging

    Returns a unified summary with analysis, negotiation, switching
    results and a final_status of completed / partial / failed.
    """
    try:
        logger.info("Starting full pipeline for user_id=%d", user_id)
        result = run_full_cycle(db, user_id)
        logger.info(
            "Pipeline completed for user_id=%d — status=%s",
            user_id, result.get("final_status"),
        )
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": result}
