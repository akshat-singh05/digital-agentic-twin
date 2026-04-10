"""
Audit Formatter — Pure message generation for explainable audit logs.

Generates concise, deterministic, human-readable explanations for
every major decision:
  - analysis   → usage efficiency and recommendations
  - negotiation → round count, discount achieved, outcome
  - switching   → approval/rejection, risk, cost change

This module contains ONLY stateless computation.
No database access, no framework imports.
"""

from typing import Any, Dict


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _safe_get(payload: Dict[str, Any], key: str, default: Any = 0) -> Any:
    """Safely get a value from a dict, returning default on KeyError or None."""
    val = payload.get(key, default)
    return val if val is not None else default


# ─────────────────────────────────────────────────────────────
# Formatters by action type
# ─────────────────────────────────────────────────────────────
def _format_analysis(payload: Dict[str, Any]) -> str:
    """
    Generate an explanation for a usage analysis decision.

    Expected payload keys:
        recommendation, efficiency, savings_estimate,
        provider, plan_name, usage_category
    """
    recommendation = _safe_get(payload, "recommendation", "unknown")
    efficiency = _safe_get(payload, "efficiency", 0)
    savings = _safe_get(payload, "savings_estimate", 0)
    provider = _safe_get(payload, "provider", "unknown")
    plan_name = _safe_get(payload, "plan_name", "unknown")
    usage_pct = round(efficiency * 100, 1)

    if recommendation == "downgrade":
        return (
            f"Plan downgraded: usage at {usage_pct}% on {provider} ({plan_name}), "
            f"estimated savings ₹{savings}/month."
        )
    elif recommendation == "upgrade":
        return (
            f"Plan upgrade recommended: usage at {usage_pct}% on {provider} ({plan_name}), "
            f"approaching or exceeding plan limits."
        )
    elif recommendation == "keep":
        return (
            f"Plan retained: usage is optimal at {usage_pct}% on {provider} ({plan_name}). "
            f"No change needed."
        )
    elif recommendation == "no_data":
        return (
            f"Analysis skipped for {provider} ({plan_name}): "
            f"no usage data available."
        )
    else:
        return (
            f"Analysis completed for {provider} ({plan_name}): "
            f"usage at {usage_pct}%, recommendation={recommendation}."
        )


def _format_negotiation(payload: Dict[str, Any]) -> str:
    """
    Generate an explanation for a negotiation outcome.

    Expected payload keys:
        total_rounds, savings_pct, status, original_cost,
        final_price, provider
    """
    total_rounds = _safe_get(payload, "total_rounds", 0)
    savings_pct = _safe_get(payload, "savings_pct", 0)
    status = _safe_get(payload, "status", "unknown")
    original = _safe_get(payload, "original_cost", 0)
    final = _safe_get(payload, "final_price", 0)
    provider = _safe_get(payload, "provider", "unknown")

    if status == "accepted":
        return (
            f"Negotiation accepted after {total_rounds} round(s) with {provider}, "
            f"{savings_pct}% discount achieved "
            f"(₹{original} → ₹{final})."
        )
    else:
        return (
            f"Negotiation {status} after {total_rounds} round(s) with {provider}. "
            f"Final offer ₹{final} vs original ₹{original} "
            f"({savings_pct}% difference)."
        )


def _format_switching(payload: Dict[str, Any]) -> str:
    """
    Generate an explanation for a plan-switching decision.

    Expected payload keys:
        applied, risk_flag, previous_cost, proposed_cost,
        projected_cost, reason
    """
    applied = _safe_get(payload, "applied", False)
    risk = _safe_get(payload, "risk_flag", "unknown")
    prev_cost = _safe_get(payload, "previous_cost", 0)
    new_cost = _safe_get(payload, "proposed_cost", 0)

    if applied:
        savings = round(prev_cost - new_cost, 2)
        return (
            f"Switch applied: cost reduced ₹{prev_cost} → ₹{new_cost} "
            f"(saving ₹{savings}/month), risk level {risk}."
        )
    else:
        return (
            f"Switch rejected due to {risk} risk of limit breach. "
            f"Proposed cost ₹{new_cost} vs current ₹{prev_cost}."
        )


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
_FORMATTERS = {
    "analysis": _format_analysis,
    "negotiation": _format_negotiation,
    "switching": _format_switching,
}


def format_audit_message(
    action_type: str,
    payload: Dict[str, Any],
) -> str:
    """
    Generate a human-readable audit message for a given action.

    Args:
        action_type: One of "analysis", "negotiation", "switching".
        payload:     Structured data specific to the action type.

    Returns:
        Concise, deterministic plain-English explanation string.

    Raises:
        ValueError: If action_type is not recognised.
    """
    formatter = _FORMATTERS.get(action_type)
    if formatter is None:
        raise ValueError(
            f"Unknown action_type '{action_type}'. "
            f"Supported: {', '.join(_FORMATTERS.keys())}"
        )
    return formatter(payload)
