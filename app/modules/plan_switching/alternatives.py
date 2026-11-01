"""
Plan Alternatives — Pure, deterministic ranking of catalog plans.

Given the user's current plan, measured usage profile and preferences,
each catalog plan of the same category is checked against hard rules.
A plan "fits" only if every rule passes; otherwise every failed rule is
reported, so the user can see why it was not recommended.

Rules (all deterministic — money and limits are never left to guesswork):
  - capacity covers the higher of peak and projected usage + 10 % headroom
  - user minimums (data, minutes), budget and required features are met
  - provider: same provider unless the user allows changing; a non-empty
    preferred-provider list restricts other providers
  - it saves at least MINIMUM_SAVINGS_PCT against the *effective* current
    cost (plan price + average billed overage)

This module contains ONLY stateless computation.
No database access, no framework imports.
"""

import json
from typing import Any, Dict, List, Optional

HEADROOM = 0.10   # required capacity = max(peak, projected) × 1.10


def _features(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def required_capacity(profile: Dict[str, Any]) -> Dict[str, float]:
    """Data GB and call minutes a plan must offer for this usage profile."""
    data = max(profile.get("peak_data_usage", 0) or 0, profile.get("projected_data_usage", 0) or 0)
    calls = max(profile.get("peak_call_usage", 0) or 0, profile.get("projected_call_usage", 0) or 0)
    return {
        "data_gb": round(data * (1 + HEADROOM), 2),
        "call_minutes": round(calls * (1 + HEADROOM), 2),
    }


def _limit_ok(limit: Optional[float], needed: float) -> bool:
    """NULL limit = unlimited."""
    return limit is None or limit >= needed


def rank_alternatives(
    current: Dict[str, Any],
    catalog: List[Dict[str, Any]],
    profile: Dict[str, Any],
    preferences: Dict[str, Any],
    min_savings_pct: float,
) -> Dict[str, Any]:
    """
    Check every comparable catalog plan and rank the ones that fit.

    Args:
        current:     provider, plan_name, category (may be None), monthly_cost,
                     data_limit_gb, call_minutes_limit
        catalog:     catalog plan dicts (id, provider, plan_name, category,
                     monthly_cost, data_limit_gb, call_minutes_limit, features, source)
        profile:     usage analysis (peak/projected usage, avg_overage)
        preferences: stored user preferences
        min_savings_pct: minimum saving for a plan to be worth switching to

    Returns:
        {"required": {...}, "effective_current_cost": float,
         "fits": [...best first], "excluded": [... with "reasons"]}
    """
    need = required_capacity(profile)
    effective_cost = round(current["monthly_cost"] + (profile.get("avg_overage") or 0), 2)
    allow_change = bool(preferences.get("allow_provider_change"))
    preferred = set(preferences.get("preferred_providers") or [])
    required_features = preferences.get("required_features") or []

    fits, excluded = [], []
    for plan in catalog:
        same_plan = (plan["provider"], plan["plan_name"]) == (current["provider"], current["plan_name"])
        if same_plan:
            continue
        if current.get("category") is None:
            if plan["provider"] != current["provider"]:
                continue                       # unknown category: compare like with like
        elif plan["category"] != current["category"]:
            continue

        reasons = []
        if plan["provider"] != current["provider"]:
            if not allow_change:
                reasons.append("different provider (not allowed in your preferences)")
            elif preferred and plan["provider"] not in preferred:
                reasons.append("provider is not one of your preferred providers")
        if not _limit_ok(plan["data_limit_gb"], need["data_gb"]):
            reasons.append(
                f"{plan['data_limit_gb']} GB is below the {need['data_gb']} GB needed "
                f"(peak/projected usage + {int(HEADROOM * 100)}%)"
            )
        if not _limit_ok(plan["call_minutes_limit"], need["call_minutes"]):
            reasons.append(
                f"{plan['call_minutes_limit']} min is below the {need['call_minutes']} min needed"
            )
        if preferences.get("min_data_gb") is not None and not _limit_ok(plan["data_limit_gb"], preferences["min_data_gb"]):
            reasons.append(f"below your minimum of {preferences['min_data_gb']} GB")
        if preferences.get("min_call_minutes") is not None and not _limit_ok(plan["call_minutes_limit"], preferences["min_call_minutes"]):
            reasons.append(f"below your minimum of {preferences['min_call_minutes']} min")
        if preferences.get("max_monthly_budget") is not None and plan["monthly_cost"] > preferences["max_monthly_budget"]:
            reasons.append(f"₹{plan['monthly_cost']} is over your budget of ₹{preferences['max_monthly_budget']}")
        feats = _features(plan.get("features"))
        missing = [f for f in required_features if not feats.get(f)]
        if missing:
            reasons.append(f"missing required feature(s): {', '.join(missing)}")

        savings = round(effective_cost - plan["monthly_cost"], 2)
        savings_pct = round(savings / effective_cost * 100, 2) if effective_cost > 0 else 0.0
        if savings_pct < min_savings_pct:
            reasons.append(
                f"saves {savings_pct}% (₹{savings}/month), below the {min_savings_pct}% minimum"
            )

        entry = {
            "plan_id": plan["id"],
            "provider": plan["provider"],
            "plan_name": plan["plan_name"],
            "monthly_cost": plan["monthly_cost"],
            "data_limit_gb": plan["data_limit_gb"],
            "call_minutes_limit": plan["call_minutes_limit"],
            "features": feats,
            "source": plan.get("source"),
            "monthly_savings": savings,
            "savings_pct": savings_pct,
        }
        (excluded if reasons else fits).append({**entry, "reasons": reasons} if reasons else entry)

    # Best first: biggest saving, then most data (unlimited counts as most)
    fits.sort(key=lambda p: (-p["monthly_savings"],
                             -(p["data_limit_gb"] if p["data_limit_gb"] is not None else float("inf"))))
    excluded.sort(key=lambda p: (len(p["reasons"]), -p["monthly_savings"]))

    return {
        "required": need,
        "effective_current_cost": effective_cost,
        "fits": fits,
        "excluded": excluded,
    }


def describe_tradeoffs(current: Dict[str, Any], plan: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, List[str]]:
    """Plain-English benefits and drawbacks of moving from ``current`` to ``plan``."""
    benefits, drawbacks = [], []
    overage = profile.get("avg_overage") or 0

    if plan["monthly_savings"] > 0:
        benefits.append(f"Saves ₹{plan['monthly_savings']}/month ({plan['savings_pct']}%).")
    if overage and plan["monthly_cost"] > current["monthly_cost"]:
        benefits.append(f"Larger allowance removes the average ₹{overage}/month overage.")

    for key, unit, label in (("data_limit_gb", "GB", "Data"), ("call_minutes_limit", "min", "Call minutes")):
        old, new = current.get(key), plan.get(key)
        if old is not None and (new is None or new > old):
            benefits.append(f"{label} allowance rises from {old} {unit} to {'unlimited' if new is None else f'{new} {unit}'}.")
        elif new is not None and (old is None or new < old):
            peak = profile.get("peak_data_usage" if key == "data_limit_gb" else "peak_call_usage")
            drawbacks.append(
                f"{label} allowance drops from {'unlimited' if old is None else f'{old} {unit}'} "
                f"to {new} {unit} (your peak: {peak} {unit})."
            )

    old_feats, new_feats = _features(current.get("features")), plan.get("features") or {}
    lost = [k for k, v in old_feats.items() if v and not new_feats.get(k)]
    if lost:
        drawbacks.append(f"Loses feature(s): {', '.join(lost)}.")
    if plan["provider"] != current["provider"]:
        drawbacks.append(f"Changes provider from {current['provider']} to {plan['provider']}.")
    if plan.get("source") == "demo_seed":
        drawbacks.append("Catalog price is demo data, not live provider pricing.")
    return {"benefits": benefits, "drawbacks": drawbacks}
