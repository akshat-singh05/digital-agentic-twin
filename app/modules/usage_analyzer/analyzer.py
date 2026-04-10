"""
Usage Analyzer — Pure analysis logic.

This module contains ONLY stateless computation.
No database access, no framework imports.
Designed to be easily replaced with an ML model in the future.
"""

from statistics import mean
from typing import Any, Dict, List


def analyze_usage(
    usage_records: List[Any],
    subscription: Any,
) -> Dict[str, Any]:
    """
    Analyze a user's usage records against their current subscription plan.

    Args:
        usage_records: List of UsageData ORM objects.
        subscription:  Subscription ORM object (the active plan).

    Returns:
        Dictionary with efficiency score, averages, recommendation, and message.

    Recommendation logic (rule-based):
        - efficiency < 0.4  → "downgrade"  (paying for capacity you don't use)
        - efficiency 0.4–0.8 → "keep"      (healthy utilisation)
        - efficiency > 0.8  → "upgrade"    (approaching or exceeding limits)
    """

    # ── No data edge case ────────────────────────────────────
    if not usage_records:
        return {
            "efficiency": 0,
            "avg_data_usage": 0,
            "avg_call_usage": 0,
            "recommendation": "no_data",
            "message": "No usage data available for analysis.",
            "savings_estimate": 0,
            "usage_category": "unknown",
            "confidence_score": 0,
        }

    # ── Compute averages ─────────────────────────────────────
    avg_data = mean([r.data_used_gb or 0 for r in usage_records])
    avg_calls = mean([r.call_minutes_used or 0 for r in usage_records])

    # ── Compute efficiency ratio ─────────────────────────────
    efficiency = 0.5  # default when plan has no data limit

    if subscription.data_limit_gb and subscription.data_limit_gb > 0:
        efficiency = avg_data / subscription.data_limit_gb

    # ── Rule-based recommendation ────────────────────────────
    if efficiency < 0.4:
        recommendation = "downgrade"
        usage_category = "underutilized"
    elif efficiency <= 0.8:
        recommendation = "keep"
        usage_category = "optimal"
    else:
        recommendation = "upgrade"
        usage_category = "overutilized"

    # ── Savings estimation ───────────────────────────────────
    savings = subscription.monthly_cost * (1 - efficiency)
    savings_estimate = round(savings, 2)

    # ── Confidence score ─────────────────────────────────────
    # Reaches 1.0 once we have ≥ 6 billing periods of data
    confidence_score = round(min(1, len(usage_records) / 6), 2)

    # ── Human-readable message ───────────────────────────────
    pct = round(efficiency * 100, 2)
    if usage_category == "underutilized":
        message = (
            f"User is underutilizing plan ({pct}% usage). "
            f"Estimated savings ₹{savings_estimate}/month."
        )
    elif usage_category == "overutilized":
        message = (
            f"User is overutilizing plan ({pct}% usage). "
            f"Consider upgrading to avoid throttling or overage charges."
        )
    else:
        message = (
            f"Usage is optimal ({pct}% of plan capacity). "
            f"Current plan is a good fit."
        )

    return {
        "efficiency": round(efficiency, 2),
        "avg_data_usage": round(avg_data, 2),
        "avg_call_usage": round(avg_calls, 2),
        "recommendation": recommendation,
        "message": message,
        "savings_estimate": savings_estimate,
        "usage_category": usage_category,
        "confidence_score": confidence_score,
    }


def pick_best_plan(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Choose the best plan from a list of per-subscription analysis results.

    Priority logic:
      1. Plans recommended "keep" are preferred (stable, good fit).
      2. Among "downgrade" plans, pick the cheapest (save money).
      3. Among "upgrade" plans, pick the one with highest efficiency
         (closest match to actual usage).
      4. "no_data" plans are deprioritised.

    Args:
        analyses: List of dicts, each the output of ``analyze_usage`` with
                  added ``subscription_id``, ``monthly_cost`` etc.

    Returns:
        The single best analysis dict, or None if list is empty.
    """
    if not analyses:
        return None

    # Filter out no_data entries unless that's all we have
    actionable = [a for a in analyses if a["recommendation"] != "no_data"]
    if not actionable:
        return analyses[0]  # all are no_data; return first

    # Priority order: keep > downgrade > upgrade
    priority = {"keep": 0, "downgrade": 1, "upgrade": 2}

    def sort_key(a):
        rec = a["recommendation"]
        pri = priority.get(rec, 99)

        if rec == "downgrade":
            # cheapest plan first
            secondary = a.get("monthly_cost", 0)
        elif rec == "upgrade":
            # highest efficiency first (descending → negative)
            secondary = -a.get("efficiency", 0)
        else:
            # "keep" — most efficient first
            secondary = -a.get("efficiency", 0)

        return (pri, secondary)

    actionable.sort(key=sort_key)
    return actionable[0]

