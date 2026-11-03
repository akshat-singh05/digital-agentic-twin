"""
Negotiation brief — pure logic, no DB or framework imports.

The twin never talks to a provider.  It prepares what the *user* can say:
what to ask for, the price beyond which switching is better, the facts that
support the request, and a draft message.  The prices are rules of thumb,
not predictions of what a provider will accept.

  walk-away price  the most worth paying: at least ``min_savings_pct`` below
                   today's price, and never above the best catalog alternative
                   (the user's best alternative to a deal)
  opening ask      the best alternative's price when there is one to cite
                   (that is the walk-away price), otherwise a 10% discount
"""

from typing import Any, Dict, List, Optional

OPENING_DISCOUNT_PCT = 10   # asked for when there is no competing plan to cite


def walk_away_price(
    current_cost: float, min_savings_pct: float, best_alternative_cost: Optional[float] = None,
) -> float:
    ceiling = current_cost * (1 - min_savings_pct / 100)
    if best_alternative_cost is not None:
        ceiling = min(ceiling, best_alternative_cost)
    return round(ceiling, 2)


def opening_ask(current_cost: float, walk_away: float, has_alternative: bool) -> float:
    if has_alternative:
        return walk_away
    return round(min(walk_away, current_cost * (1 - OPENING_DISCOUNT_PCT / 100)), 2)


def _round_to(value: float, step: float) -> float:
    """Round to a coarse figure so a draft never quotes exact usage."""
    return max(step, round(value / step) * step)


def _limit_text(limit: Optional[float], unit: str) -> str:
    return f"{limit:g} {unit}" if limit else f"unlimited {unit}"


def _alternative_text(alt: Dict[str, Any]) -> str:
    return (f"{alt['provider']} {alt['plan_name']} "
            f"({_limit_text(alt.get('data_limit_gb'), 'GB')}, "
            f"{_limit_text(alt.get('call_minutes_limit'), 'min')}) at ₹{alt['monthly_cost']:g}/month")


def build_brief(
    *,
    plan: Dict[str, Any],
    analysis: Dict[str, Any],
    alternatives: List[Dict[str, Any]],
    min_savings_pct: float,
    share_usage: bool,
) -> Dict[str, Any]:
    """
    Build a negotiation brief for one subscription.

    Args:
        plan:             provider, plan_name, monthly_cost, data_limit_gb, call_minutes_limit.
        analysis:         The Usage Analyzer's result for the subscription.
        alternatives:     Catalog plans that fit, best saving first.
        min_savings_pct:  Smallest saving worth acting on.
        share_usage:      Whether the user allowed usage figures to leave the app;
                          if not, the draft contains none.
    """
    cost = plan["monthly_cost"]
    best = alternatives[0] if alternatives else None
    walk_away = walk_away_price(cost, min_savings_pct, best["monthly_cost"] if best else None)
    ask = opening_ask(cost, walk_away, best is not None)
    overage = analysis.get("avg_overage") or 0
    periods = analysis.get("periods") or 0

    evidence = {
        "efficiency": analysis.get("efficiency"),
        "binding_metric": analysis.get("binding_metric"),
        "avg_data_gb": analysis.get("avg_data_usage"),
        "avg_call_minutes": analysis.get("avg_call_usage"),
        "periods": periods,
        "unused_spend": analysis.get("unused_spend"),
        "avg_overage": overage,
    }

    leverage: List[str] = []
    if analysis.get("efficiency") is not None:
        leverage.append(
            f"You use about {round(analysis['efficiency'] * 100)}% of the capacity you pay for "
            f"(over {periods} billing period{'s' if periods != 1 else ''}).")
    for alt in alternatives[:2]:
        leverage.append(f"A competing plan: {_alternative_text(alt)}.")
    if not alternatives:
        leverage.append("No catalog plan beats yours, so you have no competing offer to cite; "
                        "ask for a loyalty discount instead.")

    if best:
        fallback = (f"If they decline, switching to {best['provider']} {best['plan_name']} would save "
                    f"₹{best['monthly_savings']:g}/month; the twin can propose it for your approval.")
    else:
        fallback = "If they decline, keep your current plan; no catalog plan is a better deal."

    caveats = [
        "These figures are rules of thumb. A provider may offer less or decline.",
        "The twin has not contacted anyone; you send the message and report the answer.",
    ]
    if overage:
        caveats.append(f"You were billed about ₹{overage:g}/month above the plan price; "
                       "the walk-away price compares against the plan price only.")

    lines = [
        "Hello,", "",
        f"I have been a customer on {plan['plan_name']} at ₹{cost:g}/month and would like to ask "
        "whether you can offer me a better price.",
    ]
    if share_usage:
        data = _round_to(analysis.get("avg_data_usage") or 0, 5)
        calls = _round_to(analysis.get("avg_call_usage") or 0, 10)
        lines.append(
            f"Over the last {periods} months I have used about {data:g} GB of my "
            f"{_limit_text(plan.get('data_limit_gb'), 'GB')} and about {calls:g} minutes of my "
            f"{_limit_text(plan.get('call_minutes_limit'), 'min')}, so I am paying for capacity I do not use.")
    else:
        lines.append("I am not using all of my plan's capacity, so I am paying for more than I need.")
    if best:
        lines.append(f"I have been comparing options, and {_alternative_text(best)} suits my usage.")
    lines += [
        f"Could you offer me this plan, or an equivalent one, at ₹{ask:g}/month? "
        f"I would prefer to stay with {plan['provider']} if we can agree on a price.",
        "", "Thank you,", "[Your name]",
    ]

    return {
        "current_cost": cost,
        "effective_cost": round(cost + overage, 2),
        "opening_ask": ask,
        "walk_away_price": walk_away,
        "evidence": evidence,
        "leverage": leverage,
        "fallback": fallback,
        "caveats": caveats,
        "draft_message": "\n".join(lines),
        "included_usage": share_usage,
    }
