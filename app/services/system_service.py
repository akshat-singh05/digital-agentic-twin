"""
System Service — Unified end-to-end pipeline orchestrator.

Executes the full Agentic Digital Twin cycle in sequence:
    1. Analyze usage          (Usage Analyzer)
    2. Sanitize data          (Privacy Mediator)
    3. Run negotiation        (Negotiation Agent)
    4. Attempt plan switching  (Plan Switching)
    5. Log all actions         (Audit Logger)

The analyzer picks one optimization target (the most underutilized
subscription); negotiation and switching both act on that subscription.
With no target they are skipped.  Each agent writes its own audit entry.
If any step fails the pipeline continues with partial results and the
final_status reflects the outcome.
"""

from typing import Any, Dict

from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.services.user_service import require_user
from app.modules.usage_analyzer.service import analyze_user_usage
from app.modules.privacy_mediator.service import get_sanitized_usage
from app.modules.negotiation_agent.service import run_negotiation
from app.modules.plan_switching.service import switch_plan

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# Pipeline step runner
# ─────────────────────────────────────────────────────────────
def _run_step(
    step_name: str,
    callable_fn,
    errors: list,
) -> Any:
    """
    Execute a single pipeline step with error containment.

    Args:
        step_name:   Human-readable label for logging/error tracking.
        callable_fn: Zero-argument callable that performs the work.
        errors:      Mutable list that collects error descriptions.

    Returns:
        The result of callable_fn on success, or None on failure.
    """
    try:
        logger.info("  → Step: %s ...", step_name)
        result = callable_fn()
        logger.info("  ✓ Step: %s completed", step_name)
        return result
    except Exception as exc:
        msg = f"{step_name} failed: {exc}"
        logger.warning("  ✗ %s", msg)
        errors.append(msg)
        return None


def _determine_final_status(
    step_results: Dict[str, Any],
    errors: list,
) -> str:
    """
    Determine the overall pipeline outcome.

    Skipped steps are left out of ``step_results`` and do not count.

    Returns:
        "completed" – every step that ran succeeded
        "partial"   – some steps succeeded, some failed
        "failed"    – all steps failed
    """
    total = len(step_results)
    succeeded = sum(1 for v in step_results.values() if v is not None)

    if succeeded == total:
        return "completed"
    if succeeded > 0:
        return "partial"
    return "failed"


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
def run_full_cycle(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Execute the full intelligent pipeline for a user.

    Pipeline order:
        1. Usage analysis   → efficiency, recommendation, optimization target
        2. Data sanitization → differentially-private usage records
        3. Negotiation       → multi-round price negotiation on the target
        4. Plan switching    → KPI-validated plan change with rollback
        Each agent writes its own explainable audit entry.

    Args:
        db:      Active SQLAlchemy session.
        user_id: Primary key of the user.

    Returns:
        Dictionary with analysis, negotiation, switching results and
        a final_status of "completed", "partial", or "failed".

    Raises:
        ValueError: If the user does not exist (fail-fast — the
                    entire pipeline is meaningless without a valid user).
    """
    # ── Fail-fast: user must exist ───────────────────────────
    logger.info("Pipeline started for user_id=%d", user_id)
    require_user(db, user_id)

    errors: list = []
    step_results: Dict[str, Any] = {}
    skipped: Dict[str, str] = {}
    negotiation = switching = None

    # ── Step 1: Analyze usage ────────────────────────────────
    analysis = _run_step(
        "Usage Analysis",
        lambda: analyze_user_usage(db, user_id),
        errors,
    )
    step_results["analysis"] = analysis
    target = (analysis or {}).get("optimization_target")

    # ── Step 2: Sanitize data ────────────────────────────────
    sanitized = _run_step(
        "Data Sanitization",
        lambda: get_sanitized_usage(db, user_id),
        errors,
    )
    step_results["sanitization"] = sanitized

    if target is None:
        reason = (
            "Usage analysis failed." if analysis is None
            else "No underutilized subscription — nothing to negotiate."
        )
        skipped = {"negotiation": reason, "switching": reason}
    else:
        target_id = target["subscription_id"]

        # ── Step 3: Run negotiation on the target ────────────
        negotiation = _run_step(
            "Negotiation",
            lambda: run_negotiation(db, user_id, subscription_id=target_id),
            errors,
        )
        step_results["negotiation"] = negotiation

        # ── Step 4: Plan switching on the same target ────────
        if negotiation is None:
            skipped["switching"] = "Negotiation failed."
        else:
            switching = _run_step(
                "Plan Switching",
                lambda: switch_plan(db, user_id, subscription_id=target_id),
                errors,
            )
            step_results["switching"] = switching

    # Each agent audited itself; report which decisions were recorded.
    audit_entries = [
        action for action, result in (
            ("analysis", analysis),
            ("negotiation", negotiation),
            ("switching", switching),
        ) if result is not None
    ]

    final_status = _determine_final_status(step_results, errors)

    logger.info(
        "Pipeline finished for user_id=%d — final_status=%s, errors=%d",
        user_id, final_status, len(errors),
    )

    # ── Build response ───────────────────────────────────────
    return {
        "user_id": user_id,
        "analysis": analysis,
        "sanitization": sanitized,
        "negotiation": negotiation,
        "switching": switching,
        "audit_logged": audit_entries,
        "skipped": skipped or None,
        "errors": errors if errors else None,
        "final_status": final_status,
    }
