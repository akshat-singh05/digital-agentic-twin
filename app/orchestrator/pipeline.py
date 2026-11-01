"""
Orchestrator — the Digital Twin's decision cycle for one user.

    1. Usage Analyzer    → efficiency per subscription + optimization target
    2. Privacy Mediator  → differentially-private usage for sharing
    3. Negotiation Agent → simulated negotiation on the target
    4. Plan Switching    → evaluates the deal; proposes a Recommendation
    ── the run stops here ("awaiting_approval") until the user decides ──

Rules the orchestrator enforces:
  - Every agent checks consent with the Privacy Mediator; a missing
    consent skips that step (and every step depending on it) — it is not
    an error.
  - Negotiation and switching act only on the analyzer's target, and not
    at all while a proposal for that subscription awaits a decision.
  - Each step's timing, outcome and error are stored in agent_run_steps;
    audit entries and recommendations carry the run id.
  - A failing step is rolled back and recorded; later steps that need its
    output are skipped, independent ones still run.
"""

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.errors import ConsentRequiredError
from app.core.logger import get_logger
from app.core.run_context import current_run_id
from app.models import AgentRun, AgentRunStep
from app.services.recommendation_service import pending_recommendation
from app.services.user_service import require_user
from app.modules.usage_analyzer.service import analyze_user_usage
from app.modules.privacy_mediator.service import get_sanitized_usage
from app.modules.negotiation_agent.service import run_negotiation
from app.modules.plan_switching.service import propose_plan_switch

logger = get_logger(__name__)

_FAILED = object()   # sentinel: step raised an unexpected error


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────
# Step summaries (stored on agent_run_steps.summary)
# ─────────────────────────────────────────────────────────────
def _summarize(agent: str, result: Dict[str, Any]) -> Dict[str, Any]:
    if agent == "usage_analyzer":
        target = result.get("optimization_target") or {}
        return {
            "recommendation": result.get("recommendation"),
            "efficiency": result.get("efficiency"),
            "target_subscription_id": target.get("subscription_id"),
        }
    if agent == "privacy_mediator":
        return {"sanitized_records": len(result.get("sanitized_usage", []))}
    if agent == "negotiation_agent":
        return {k: result.get(k) for k in ("status", "final_price", "total_rounds", "simulated")}
    if agent == "plan_switching":
        return {k: result.get(k) for k in ("proposed", "recommendation_id", "risk_flag")}
    return {}


# ─────────────────────────────────────────────────────────────
# Step runner
# ─────────────────────────────────────────────────────────────
def _skip(db: Session, run: AgentRun, agent: str, reason: str) -> None:
    """Record a step that was not attempted."""
    now = _now()
    db.add(AgentRunStep(
        run_id=run.id, agent=agent, status="skipped", started_at=now,
        finished_at=now, duration_ms=0, summary=json.dumps({"reason": reason}),
    ))
    db.commit()


def _step(
    db: Session, run: AgentRun, agent: str, fn: Callable[[], Dict[str, Any]],
) -> Tuple[Any, Optional[str]]:
    """
    Run one agent and record the step.

    Returns:
        (result, None)       on success
        (None, reason)       when consent was missing (step "skipped")
        (_FAILED, error)     on any other error (step "failed", rolled back)
    """
    step = AgentRunStep(run_id=run.id, agent=agent, status="running", started_at=_now())
    db.add(step)
    db.commit()
    started = time.perf_counter()

    try:
        result = fn()
        status, summary, error, outcome = "completed", _summarize(agent, result), None, (result, None)
    except ConsentRequiredError as exc:
        db.rollback()
        reason = f"consent '{exc.scope}' not granted"
        status, summary, error, outcome = "skipped", {"reason": reason}, None, (None, reason)
    except Exception as exc:  # contain the failure to this step
        db.rollback()
        logger.warning("Run %d: %s failed: %s", run.id, agent, exc)
        status, summary, error, outcome = "failed", None, f"{type(exc).__name__}: {exc}", (_FAILED, str(exc))

    step.status = status
    step.finished_at = _now()
    step.duration_ms = int((time.perf_counter() - started) * 1000)
    step.summary = json.dumps(summary, default=str) if summary is not None else None
    step.error = error
    db.commit()
    return outcome


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
def run_cycle(db: Session, user_id: int, trigger: str = "api") -> Dict[str, Any]:
    """
    Execute one decision cycle for a user and record it as an AgentRun.

    Returns:
        Dict with run_id, each agent's output (None if it did not run),
        ``skipped`` reasons, ``errors``, the recommendation awaiting
        approval (if any) and ``final_status``:
          awaiting_approval – a recommendation needs the user's decision
          completed         – every step that ran succeeded, nothing to approve
          partial / failed  – some / all attempted steps failed

    Raises:
        ValueError: If the user does not exist (no run is recorded).
    """
    require_user(db, user_id)

    run = AgentRun(user_id=user_id, trigger=trigger, status="running", started_at=_now())
    db.add(run)
    db.commit()
    token = current_run_id.set(run.id)
    logger.info("Run %d started for user_id=%d", run.id, user_id)

    outputs: Dict[str, Any] = {"analysis": None, "sanitization": None,
                               "negotiation": None, "switching": None}
    skipped: Dict[str, str] = {}
    errors: list = []

    def record(key: str, outcome: Tuple[Any, Optional[str]], label: str) -> Any:
        result, reason = outcome
        if result is _FAILED:
            errors.append(f"{label} failed: {reason}")
            return None
        if result is None:
            skipped[key] = reason
            return None
        outputs[key] = result
        return result

    try:
        # 1 — Usage analysis
        analysis = record("analysis", _step(
            db, run, "usage_analyzer", lambda: analyze_user_usage(db, user_id)), "Usage Analysis")

        # 2 — Privacy mediation (independent of the analysis result)
        sanitized = record("sanitization", _step(
            db, run, "privacy_mediator", lambda: get_sanitized_usage(db, user_id)), "Data Sanitization")

        # 3 — Negotiation on the target, if there is one to act on
        target = (analysis or {}).get("optimization_target")
        reason = None
        if analysis is None:
            reason = "usage analysis did not run"
        elif target is None:
            reason = "no underutilized subscription — nothing to negotiate"
        elif sanitized is None:
            reason = "privacy-protected usage data is not available"
        else:
            pending = pending_recommendation(db, target["subscription_id"])
            if pending is not None:
                reason = f"recommendation #{pending.id} is awaiting your decision"

        negotiation = None
        if reason:
            for agent, key in (("negotiation_agent", "negotiation"), ("plan_switching", "switching")):
                _skip(db, run, agent, reason)
                skipped[key] = reason
        else:
            target_id = target["subscription_id"]
            negotiation = record("negotiation", _step(
                db, run, "negotiation_agent",
                lambda: run_negotiation(db, user_id, subscription_id=target_id)), "Negotiation")

            # 4 — Evaluate and propose (never applies the change)
            if negotiation is None:
                _skip(db, run, "plan_switching", "negotiation did not complete")
                skipped["switching"] = "negotiation did not complete"
            else:
                record("switching", _step(
                    db, run, "plan_switching",
                    lambda: propose_plan_switch(db, user_id, subscription_id=target_id)), "Plan Switching")

        # ── Final status ─────────────────────────────────────
        attempted = len(errors) + sum(v is not None for v in outputs.values())
        switching = outputs["switching"]
        if switching and switching.get("proposed"):
            final_status = "awaiting_approval"
        elif errors and len(errors) == attempted:
            final_status = "failed"
        elif errors:
            final_status = "partial"
        else:
            final_status = "completed"

        run.status = final_status
        run.finished_at = _now()
        run.error = "; ".join(errors) or None
        db.commit()
    finally:
        current_run_id.reset(token)

    logger.info("Run %d finished — status=%s, errors=%d", run.id, final_status, len(errors))

    return {
        "run_id": run.id,
        "user_id": user_id,
        **outputs,
        "recommendation": (
            {k: switching[k] for k in ("recommendation_id", "current_cost",
                                       "projected_cost", "estimated_monthly_savings")}
            if final_status == "awaiting_approval" else None
        ),
        "audit_logged": [
            action for action, key in (("analysis", "analysis"),
                                       ("negotiation", "negotiation"),
                                       ("switching", "switching"))
            if outputs[key] is not None
        ],
        "skipped": skipped or None,
        "errors": errors or None,
        "final_status": final_status,
    }
