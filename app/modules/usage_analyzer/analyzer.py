"""
Usage Analyzer — Pure analysis logic.

This module contains ONLY stateless computation.
No database access, no framework imports.

For one subscription it measures, from the actual usage records:
  - utilisation of each capped metric (data GB, call minutes); the
    higher one is the binding constraint and becomes ``efficiency``
  - average and peak usage, a month-over-month data trend and a
    next-month projection (least-squares line)
  - overage actually billed above the plan price
  - ``unused_spend``: the share of the price paying for capacity the user
    does not use — an indicator, NOT a saving (real savings come from
    catalog alternatives or negotiation)
  - a confidence score from history length and usage stability
"""

from datetime import datetime
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence

# ── Tunables ─────────────────────────────────────────────────
DOWNGRADE_BELOW = 0.4        # binding utilisation < 40 % → underutilized
UPGRADE_ABOVE = 0.8          # binding utilisation > 80 % → overutilized
TREND_STABLE_BAND = 0.05     # |slope| < 5 % of the mean per month → stable
FULL_CONFIDENCE_PERIODS = 6  # periods of history for full confidence


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _utilisation(avg: float, limit: Optional[float]) -> Optional[float]:
    """avg / limit, or None when the metric is uncapped (NULL or 0 limit)."""
    if not limit or limit <= 0:
        return None
    return avg / limit


def _linear_trend(values: Sequence[float]) -> Dict[str, Any]:
    """
    Least-squares slope over equally spaced periods (oldest first) and the
    projection for the next period.  Needs at least 3 points.
    """
    n = len(values)
    if n < 3:
        avg = mean(values) if values else 0.0
        return {"trend": "insufficient_data", "slope": 0.0, "projected": avg}

    xs = range(n)
    x_mean, y_mean = (n - 1) / 2, mean(values)
    denom = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values)) / denom
    projected = max(0.0, y_mean + slope * (n - x_mean))

    if y_mean == 0 or abs(slope) < TREND_STABLE_BAND * y_mean:
        trend = "stable"
    else:
        trend = "rising" if slope > 0 else "falling"
    return {"trend": trend, "slope": slope, "projected": projected}


def _stability(values: Sequence[float]) -> float:
    """1 for perfectly steady usage, down to 0.5 for very volatile usage."""
    if len(values) < 2 or mean(values) == 0:
        return 1.0
    cv = pstdev(values) / mean(values)
    return 1 - min(cv, 0.5)


def _empty(recommendation: str, category: str, message: str) -> Dict[str, Any]:
    return {
        "efficiency": None,
        "binding_metric": None,
        "data_utilization": None,
        "call_utilization": None,
        "avg_data_usage": 0,
        "avg_call_usage": 0,
        "peak_data_usage": 0,
        "peak_call_usage": 0,
        "data_trend": "insufficient_data",
        "data_trend_gb_per_month": 0,
        "projected_data_usage": 0,
        "projected_call_usage": 0,
        "avg_overage": 0,
        "unused_spend": 0,
        "periods": 0,
        "recommendation": recommendation,
        "usage_category": category,
        "message": message,
        "confidence_score": 0,
    }


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
def analyze_usage(
    usage_records: List[Any],
    subscription: Any,
    overage_since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Analyze a subscription's usage records against its plan.

    Args:
        usage_records: UsageData-like objects (any order).
        subscription:  Subscription-like object (the current plan).
        overage_since: Only bill periods starting at/after this time count
                       towards overage (the price may have changed before).

    Recommendation (rule-based, on the binding utilisation):
        < 0.4   → "downgrade"  — unless the projection says usage will
                                 reach 40 %, then "keep"
        0.4–0.8 → "keep"
        > 0.8   → "upgrade"
        no cap  → "no_metric"  (nothing to measure utilisation against)
    """
    if not usage_records:
        return _empty("no_data", "unknown", "No usage data available for analysis.")

    records = sorted(usage_records, key=lambda r: r.period_start)
    data = [r.data_used_gb or 0.0 for r in records]
    calls = [r.call_minutes_used or 0 for r in records]
    n = len(records)

    avg_data, avg_calls = mean(data), mean(calls)
    data_trend = _linear_trend(data)
    call_trend = _linear_trend(calls)

    data_util = _utilisation(avg_data, subscription.data_limit_gb)
    call_util = _utilisation(avg_calls, subscription.call_minutes_limit)

    # Overage actually billed above the plan price
    billed = [
        max(0.0, (r.billing_amount or 0.0) - subscription.monthly_cost)
        for r in records
        if overage_since is None or r.period_start >= overage_since
    ]
    avg_overage = round(mean(billed), 2) if billed else 0.0

    base = {
        "data_utilization": round(data_util, 3) if data_util is not None else None,
        "call_utilization": round(call_util, 3) if call_util is not None else None,
        "avg_data_usage": round(avg_data, 2),
        "avg_call_usage": round(avg_calls, 2),
        "peak_data_usage": round(max(data), 2),
        "peak_call_usage": max(calls),
        "data_trend": data_trend["trend"],
        "data_trend_gb_per_month": round(data_trend["slope"], 2),
        "projected_data_usage": round(data_trend["projected"], 2),
        "projected_call_usage": round(call_trend["projected"], 2),
        "avg_overage": avg_overage,
        "periods": n,
    }

    # ── Binding constraint ───────────────────────────────────
    utils = {k: v for k, v in (("data", data_util), ("calls", call_util)) if v is not None}
    if not utils:
        return {
            **_empty("no_metric", "unmeasured",
                     "Plan has no data or call cap — utilisation cannot be measured."),
            **base,
        }
    binding = max(utils, key=utils.get)
    efficiency = utils[binding]

    projected_util = (
        _utilisation(data_trend["projected"], subscription.data_limit_gb)
        if binding == "data" else
        _utilisation(call_trend["projected"], subscription.call_minutes_limit)
    )

    # ── Rule-based recommendation ────────────────────────────
    pct = round(efficiency * 100, 1)
    if efficiency < DOWNGRADE_BELOW and projected_util < DOWNGRADE_BELOW:
        recommendation, category = "downgrade", "underutilized"
        message = f"Underutilized: {binding} usage at {pct}% of the plan."
    elif efficiency < DOWNGRADE_BELOW:
        recommendation, category = "keep", "optimal"
        message = (
            f"{binding.capitalize()} usage is low ({pct}%) but rising — projected "
            f"{round(projected_util * 100, 1)}% next month, so the plan is kept."
        )
    elif efficiency <= UPGRADE_ABOVE:
        recommendation, category = "keep", "optimal"
        message = f"Usage is healthy ({pct}% of {binding} capacity). Current plan fits."
    else:
        recommendation, category = "upgrade", "overutilized"
        message = f"Overutilized: {binding} usage at {pct}% of the plan."
    if avg_overage > 0:
        message += f" Average overage billed: ₹{avg_overage}/month."

    # Share of the price paying for unused capacity (an indicator, not a saving)
    unused_spend = round(subscription.monthly_cost * max(0.0, 1 - efficiency), 2)

    binding_series = data if binding == "data" else calls
    confidence = min(1.0, n / FULL_CONFIDENCE_PERIODS) * _stability(binding_series)

    return {
        **base,
        "efficiency": round(efficiency, 3),
        "binding_metric": binding,
        "unused_spend": unused_spend,
        "recommendation": recommendation,
        "usage_category": category,
        "message": message,
        "confidence_score": round(confidence, 2),
    }


def opportunity_value(analysis: Dict[str, Any]) -> float:
    """
    ₹/month at stake for one subscription: unused spend on an
    underutilized plan, or billed overage on an overutilized one.
    """
    if analysis["recommendation"] == "downgrade":
        return analysis.get("unused_spend", 0) or 0
    if analysis["recommendation"] == "upgrade":
        return analysis.get("avg_overage", 0) or 0
    return 0


def pick_optimization_target(analyses: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Choose the single subscription the downstream agents should act on.

    Qualifies: underutilized plans (unused spend) and overutilized plans
    that actually incurred overage.  The largest ₹/month at stake wins.

    Returns:
        The chosen analysis dict, or None when there is no opportunity.
    """
    candidates = [a for a in analyses if opportunity_value(a) > 0]
    if not candidates:
        return None
    return max(candidates, key=opportunity_value)


def pick_best_plan(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Choose the best-fitting plan from a list of per-subscription analyses.

    Priority logic:
      1. Plans recommended "keep" are preferred (stable, good fit).
      2. Among "downgrade" plans, pick the cheapest (save money).
      3. Among "upgrade" plans, pick the one with highest efficiency
         (closest match to actual usage).
      4. Unmeasurable ("no_data" / "no_metric") plans are deprioritised.

    Returns:
        The single best analysis dict, or None if list is empty.
    """
    if not analyses:
        return None

    actionable = [a for a in analyses if a["recommendation"] not in ("no_data", "no_metric")]
    if not actionable:
        return analyses[0]

    priority = {"keep": 0, "downgrade": 1, "upgrade": 2}

    def sort_key(a):
        rec = a["recommendation"]
        secondary = a.get("monthly_cost", 0) if rec == "downgrade" else -(a.get("efficiency") or 0)
        return (priority.get(rec, 99), secondary)

    return sorted(actionable, key=sort_key)[0]
