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

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ConsentRequiredError
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
    PlanCatalogOut,
    PlanHistoryOut,
    UserPreferenceUpdate,
    UserPreferenceOut,
    ConsentUpdate,
    ConsentOut,
    RecommendationOut,
    AgentRunOut,
)
from app.services import (
    catalog_service, recommendation_service, run_service, subscription_service, user_service,
)
from app.modules.privacy_mediator.service import list_consents, set_consent
from app.modules.usage_analyzer.service import analyze_user_usage
from app.modules.privacy_mediator.service import get_sanitized_usage
from app.modules.negotiation_agent.service import run_negotiation
from app.modules.plan_switching.service import (
    approve_recommendation, get_alternatives, propose_plan_switch, reject_recommendation,
    rollback_last_change,
)
from app.modules.audit_logger.service import get_user_audit_logs
from app.orchestrator.pipeline import run_cycle as run_orchestrator_cycle

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


def _raise_for(e: ValueError):
    """Convert a service ValueError into 403 (consent), 409 (state conflict) or 404."""
    if isinstance(e, ConsentRequiredError):
        status_code = 403
    elif isinstance(e, ConflictError):
        status_code = 409
    else:
        status_code = 404
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "message": str(e)},
    )


# ── Pagination parameters (bounded to protect the database) ──
_Skip = Query(0, ge=0, description="Number of records to skip")
_UserLimit = Query(50, ge=1, le=200, description="Maximum records to return")
_AuditLimit = Query(100, ge=1, le=500, description="Maximum records to return")


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
def list_users(skip: int = _Skip, limit: int = _UserLimit, db: Session = Depends(get_db)):
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


@router.get(
    "/users/{user_id}/preferences",
    response_model=SuccessResponse[UserPreferenceOut],
    tags=["Users"],
)
def get_preferences(user_id: int, db: Session = Depends(get_db)):
    """Get the constraints the twin must respect for this user."""
    try:
        prefs = user_service.get_preferences(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": prefs}


@router.put(
    "/users/{user_id}/preferences",
    response_model=SuccessResponse[UserPreferenceOut],
    tags=["Users"],
)
def replace_preferences(user_id: int, payload: UserPreferenceUpdate, db: Session = Depends(get_db)):
    """Replace the user's preferences (omitted fields reset to defaults)."""
    try:
        prefs = user_service.replace_preferences(db, user_id, payload.model_dump())
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": prefs}


# ═════════════════════════════════════════════════════════════
#  CONSENTS (PRIVACY MEDIATOR)
# ═════════════════════════════════════════════════════════════
@router.get(
    "/users/{user_id}/consents",
    response_model=SuccessResponse[List[ConsentOut]],
    tags=["Privacy Mediator"],
)
def get_consents(user_id: int, db: Session = Depends(get_db)):
    """List every consent scope and whether the user has granted it (default: no)."""
    try:
        consents = list_consents(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": consents}


@router.put(
    "/users/{user_id}/consents/{scope}",
    response_model=SuccessResponse[ConsentOut],
    tags=["Privacy Mediator"],
)
def update_consent(user_id: int, scope: str, payload: ConsentUpdate, db: Session = Depends(get_db)):
    """Grant or revoke one consent scope.  The change is audited."""
    try:
        consent = set_consent(db, user_id, scope, payload.granted)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": consent}


# ═════════════════════════════════════════════════════════════
#  PLAN CATALOG
# ═════════════════════════════════════════════════════════════
@router.get(
    "/plans",
    response_model=SuccessResponse[List[PlanCatalogOut]],
    tags=["Plan Catalog"],
)
def list_plans(
    provider: Optional[str] = Query(None, max_length=100),
    category: Optional[str] = Query(None, max_length=30),
    include_unavailable: bool = False,
    db: Session = Depends(get_db),
):
    """
    List catalog plans, cheapest first.

    Plans with ``source="demo_seed"`` are illustrative demo data, not live
    provider pricing.
    """
    plans = catalog_service.list_plans(db, provider, category, include_unavailable)
    return {"status": "success", "data": plans}


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


@router.get(
    "/subscriptions/{sub_id}/history",
    response_model=SuccessResponse[List[PlanHistoryOut]],
    tags=["Subscriptions"],
)
def get_subscription_history(sub_id: int, db: Session = Depends(get_db)):
    """List every applied change to a subscription, oldest first."""
    try:
        subscription_service.require_subscription(db, sub_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": subscription_service.get_plan_history(db, sub_id)}


@router.get(
    "/subscriptions/{sub_id}/alternatives",
    response_model=SuccessResponse[dict],
    tags=["Plan Switching"],
)
def get_subscription_alternatives(sub_id: int, db: Session = Depends(get_db)):
    """
    Catalog plans that fit this subscription's measured usage and the
    user's preferences (best saving first), plus every plan that was
    excluded and why.  Requires the ``usage_analysis`` consent.
    """
    try:
        result = get_alternatives(db, sub_id)
    except ValueError as e:
        _raise_for(e)
    return {"status": "success", "data": result}


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
    skip: int = _Skip,
    limit: int = _AuditLimit,
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

    Evaluates every active subscription against its usage history and
    returns efficiency scores plus the optimization target.
    Requires the ``usage_analysis`` consent (403 otherwise).
    """
    try:
        result = analyze_user_usage(db, user_id)
    except ValueError as e:
        _raise_for(e)
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
    Requires the ``share_usage_with_provider`` consent (403 otherwise).
    """
    try:
        result = get_sanitized_usage(db, user_id)
    except ValueError as e:
        _raise_for(e)
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
    Run a multi-round negotiation for the user's most underutilized plan.

    The counterparty is the built-in provider **simulator** (the response
    carries ``simulated: true``).  Rounds are persisted to
    negotiation_history.  Requires the ``usage_analysis``,
    ``negotiate_on_behalf`` and ``share_usage_with_provider`` consents.
    """
    try:
        result = run_negotiation(db, user_id)
    except ValueError as e:
        _raise_for(e)
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
    Propose a plan change based on the latest negotiation result.

    Evaluates KPIs (cost reduction, added value, SLA risk).  If they pass,
    a recommendation is created with status ``proposed`` — **nothing is
    changed** until the user approves it via
    ``POST /recommendations/{id}/approve``.  409 if a proposal for the
    subscription is already awaiting a decision.
    """
    try:
        result = propose_plan_switch(db, user_id)
    except ValueError as e:
        _raise_for(e)
    return {"status": "success", "data": result}


@router.post(
    "/subscriptions/{sub_id}/rollback",
    response_model=SuccessResponse[dict],
    tags=["Plan Switching"],
)
def rollback_subscription(sub_id: int, db: Session = Depends(get_db)):
    """
    Restore the subscription's terms from before its last applied change.
    Requires the ``execute_plan_changes`` consent.
    """
    try:
        result = rollback_last_change(db, sub_id)
    except ValueError as e:
        _raise_for(e)
    return {"status": "success", "data": result}


# ═════════════════════════════════════════════════════════════
#  RECOMMENDATIONS (USER DECISIONS)
# ═════════════════════════════════════════════════════════════
@router.get(
    "/recommendations/user/{user_id}",
    response_model=SuccessResponse[List[RecommendationOut]],
    tags=["Recommendations"],
)
def list_user_recommendations(
    user_id: int,
    status: Optional[str] = Query(None, max_length=20),
    limit: int = _UserLimit,
    db: Session = Depends(get_db),
):
    """List a user's recommendations, newest first (filter with ``?status=proposed``)."""
    try:
        user_service.require_user(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    recs = recommendation_service.list_recommendations(db, user_id, status=status, limit=limit)
    return {"status": "success", "data": recs}


@router.get(
    "/recommendations/{recommendation_id}",
    response_model=SuccessResponse[RecommendationOut],
    tags=["Recommendations"],
)
def get_recommendation(recommendation_id: int, db: Session = Depends(get_db)):
    """Get one recommendation with its rationale and evidence."""
    try:
        rec = recommendation_service.require_recommendation(db, recommendation_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": rec}


@router.post(
    "/recommendations/{recommendation_id}/approve",
    response_model=SuccessResponse[dict],
    tags=["Recommendations"],
)
def approve(recommendation_id: int, db: Session = Depends(get_db)):
    """
    Approve a proposed recommendation and apply it.

    403 without the ``execute_plan_changes`` consent (the proposal stays
    open); 409 if it was already decided or the subscription changed
    since it was proposed (it then expires).
    """
    try:
        result = approve_recommendation(db, recommendation_id)
    except ValueError as e:
        _raise_for(e)
    return {"status": "success", "data": result}


@router.post(
    "/recommendations/{recommendation_id}/reject",
    response_model=SuccessResponse[dict],
    tags=["Recommendations"],
)
def reject(recommendation_id: int, db: Session = Depends(get_db)):
    """Reject a proposed recommendation; nothing changes."""
    try:
        result = reject_recommendation(db, recommendation_id)
    except ValueError as e:
        _raise_for(e)
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
    skip: int = _Skip,
    limit: int = _AuditLimit,
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
    Run one decision cycle for a user through the orchestrator.

      1. Usage Analysis  2. Data Sanitization (Privacy Mediator)
      3. Negotiation (simulated provider)  4. Plan Switching proposal

    Steps without the needed consent are skipped.  If a change is worth
    making, the run ends ``awaiting_approval`` with a recommendation to
    approve or reject; nothing is changed by the run itself.  Every run
    and step is recorded (``GET /runs/{run_id}``).
    """
    try:
        result = run_orchestrator_cycle(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": result}


@router.get(
    "/runs/user/{user_id}",
    response_model=SuccessResponse[List[AgentRunOut]],
    tags=["System Pipeline"],
)
def list_runs(user_id: int, limit: int = _UserLimit, db: Session = Depends(get_db)):
    """List a user's orchestrator runs (with steps), newest first."""
    try:
        user_service.require_user(db, user_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": run_service.list_runs(db, user_id, limit=limit)}


@router.get(
    "/runs/{run_id}",
    response_model=SuccessResponse[AgentRunOut],
    tags=["System Pipeline"],
)
def get_run(run_id: int, db: Session = Depends(get_db)):
    """One run with each agent step's status, timing, summary and error."""
    try:
        run = run_service.require_run(db, run_id)
    except ValueError as e:
        _raise_not_found(e)
    return {"status": "success", "data": run}
