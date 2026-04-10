"""
Plan Switching Executor — Pure KPI validation and risk assessment.

Simulates applying a negotiated plan and decides whether to proceed
based on cost-reduction targets and SLA-breach risk.

This module contains ONLY stateless computation.
No database access, no framework imports.

KPI rules:
  - Downgrade → cost must drop by ≥ MINIMUM_SAVINGS_PCT %
  - Usage must not exceed new plan limits by more than SLA_BREACH_MARGIN
  - Risk flag:  low / medium / high  (high → auto-reject)
"""

from typing import Any, Dict, Optional


# ── Tunables ─────────────────────────────────────────────────
MINIMUM_SAVINGS_PCT = 5.0       # downgrade must save at least 5 %
SLA_BREACH_MARGIN   = 0.15      # usage within 15 % of new limit → medium risk
SLA_CRITICAL_MARGIN = 0.30      # usage > 30 % above new limit → high risk


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _is_downgrade(current_cost: float, proposed_cost: float) -> bool:
    """True when the proposed plan is cheaper than the current one."""
    return proposed_cost < current_cost


def _cost_reduction_pct(current_cost: float, proposed_cost: float) -> float:
    """Percentage cost reduction (positive = savings)."""
    if current_cost <= 0:
        return 0.0
    return round((1 - proposed_cost / current_cost) * 100, 2)


def _assess_data_risk(
    avg_data_used_gb: float,
    new_data_limit_gb: Optional[float],
) -> str:
    """
    Assess SLA risk for data usage against the proposed plan's limit.

    Returns:
        "low", "medium", or "high"
    """
    if new_data_limit_gb is None or new_data_limit_gb <= 0:
        return "low"  # unlimited plan → no risk

    ratio = avg_data_used_gb / new_data_limit_gb

    if ratio > (1 + SLA_CRITICAL_MARGIN):
        return "high"
    if ratio > (1 - SLA_BREACH_MARGIN):
        return "medium"
    return "low"


def _assess_call_risk(
    avg_call_minutes: float,
    new_call_limit: Optional[int],
) -> str:
    """
    Assess SLA risk for call minutes against the proposed plan's limit.

    Returns:
        "low", "medium", or "high"
    """
    if new_call_limit is None or new_call_limit <= 0:
        return "low"  # unlimited → no risk

    ratio = avg_call_minutes / new_call_limit

    if ratio > (1 + SLA_CRITICAL_MARGIN):
        return "high"
    if ratio > (1 - SLA_BREACH_MARGIN):
        return "medium"
    return "low"


def _worst_risk(*flags: str) -> str:
    """Return the highest-severity risk flag from a set of flags."""
    order = {"high": 3, "medium": 2, "low": 1}
    return max(flags, key=lambda f: order.get(f, 0))


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
def evaluate_switch(
    current_plan: Dict[str, Any],
    proposed_plan: Dict[str, Any],
    usage_stats: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Evaluate whether switching from the current plan to the proposed
    plan is safe and beneficial.

    Args:
        current_plan:  Dict with keys: monthly_cost, data_limit_gb,
                       call_minutes_limit, plan_name, provider.
        proposed_plan: Dict with same keys representing the negotiated plan.
        usage_stats:   Dict with keys: avg_data_used_gb, avg_call_minutes.

    Returns:
        Dictionary with:
            applied        – True if the switch is approved
            reason         – human-readable explanation
            projected_cost – expected monthly cost after switch
            risk_flag      – "low" / "medium" / "high"
    """
    current_cost  = current_plan.get("monthly_cost", 0)
    proposed_cost = proposed_plan.get("monthly_cost", 0)
    projected_cost = round(proposed_cost, 2)

    avg_data  = usage_stats.get("avg_data_used_gb", 0)
    avg_calls = usage_stats.get("avg_call_minutes", 0)

    # ── Cost analysis ────────────────────────────────────────
    reduction_pct = _cost_reduction_pct(current_cost, proposed_cost)
    downgrade = _is_downgrade(current_cost, proposed_cost)

    # ── SLA risk analysis ────────────────────────────────────
    data_risk = _assess_data_risk(
        avg_data, proposed_plan.get("data_limit_gb")
    )
    call_risk = _assess_call_risk(
        avg_calls, proposed_plan.get("call_minutes_limit")
    )
    risk_flag = _worst_risk(data_risk, call_risk)

    # ── Decision logic ───────────────────────────────────────
    # Rule 1: High risk → reject immediately
    if risk_flag == "high":
        return {
            "applied": False,
            "reason": (
                f"Switch rejected: high risk of SLA breach. "
                f"Data risk={data_risk}, call risk={call_risk}. "
                f"Usage would significantly exceed new plan limits."
            ),
            "projected_cost": projected_cost,
            "risk_flag": risk_flag,
        }

    # Rule 2: Downgrade must meet minimum savings threshold
    if downgrade and reduction_pct < MINIMUM_SAVINGS_PCT:
        return {
            "applied": False,
            "reason": (
                f"Switch rejected: cost reduction of {reduction_pct}% "
                f"is below the minimum threshold of {MINIMUM_SAVINGS_PCT}%. "
                f"Savings too small to justify plan change."
            ),
            "projected_cost": projected_cost,
            "risk_flag": risk_flag,
        }

    # ── Approved ─────────────────────────────────────────────
    savings_msg = (
        f"Cost reduced by {reduction_pct}% "
        f"(₹{current_cost} → ₹{proposed_cost})."
    ) if downgrade else (
        f"Plan change approved. "
        f"New cost ₹{proposed_cost}/month (was ₹{current_cost})."
    )

    return {
        "applied": True,
        "reason": (
            f"Switch approved with {risk_flag} risk. {savings_msg}"
        ),
        "projected_cost": projected_cost,
        "risk_flag": risk_flag,
    }
