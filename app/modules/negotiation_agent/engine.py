"""
Negotiation Engine — Pure offer–counteroffer logic.

Simulates multi-round price negotiation between the digital-twin
agent and a service provider.  The engine is entirely stateless:
no database access, no framework imports.

Strategy:
  - Underutilized plans → push aggressively for a discount.
  - Optimally used plans → push gently or accept current price.
  - Overutilized plans   → accept current or propose slight increase
    in exchange for more capacity.
"""

import random
from typing import Any, Dict, List


# ── Tunables ─────────────────────────────────────────────────
_MIN_ROUNDS = 3
_MAX_ROUNDS = 5
_ACCEPTANCE_THRESHOLD = 0.03   # ≤ 3 % gap → deal accepted


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _initial_offer(current_cost: float, efficiency: float) -> float:
    """
    Generate the agent's opening offer based on plan utilisation.

    Low efficiency  → ask for a big discount (up to 35 %).
    Mid efficiency  → ask for a modest discount (5–10 %).
    High efficiency → offer current price (or a small premium).
    """
    if efficiency < 0.4:
        # Underutilized — push hard
        discount = 0.25 + random.uniform(0, 0.10)       # 25–35 %
    elif efficiency <= 0.8:
        # Optimal — gentle nudge
        discount = 0.05 + random.uniform(0, 0.05)       # 5–10 %
    else:
        # Overutilized — willing to pay current or slightly more
        discount = -random.uniform(0, 0.05)              # 0 to +5 %

    offer = current_cost * (1 - discount)
    return round(max(offer, 0), 2)


def _provider_counter(
    current_cost: float,
    agent_offer: float,
    round_number: int,
) -> float:
    """
    Simulate the provider's counter-offer.

    The provider starts close to the current price and concedes
    gradually over successive rounds.  A small random element makes
    each negotiation feel realistic.
    """
    # Provider concession grows with each round
    max_concession = 0.05 * round_number          # 5 % per round
    concession = random.uniform(0, max_concession)

    # Provider counter = current cost minus its concession,
    # but never below the agent's offer.
    counter = current_cost * (1 - concession)
    counter = max(counter, agent_offer)
    return round(counter, 2)


def _adjust_offer(
    previous_offer: float,
    provider_counter: float,
    round_number: int,
) -> float:
    """
    Agent adjusts its offer upward toward the provider's counter.

    The step size increases with each round so that the negotiation
    converges.
    """
    gap = provider_counter - previous_offer
    step = gap * (0.2 + 0.1 * round_number)      # 30 %→60 % of gap
    new_offer = previous_offer + step
    return round(new_offer, 2)


def _is_accepted(agent_offer: float, provider_counter: float) -> bool:
    """True when the two sides are within the acceptance threshold."""
    if provider_counter == 0:
        return True
    gap_pct = abs(provider_counter - agent_offer) / provider_counter
    return gap_pct <= _ACCEPTANCE_THRESHOLD


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────
def negotiate(
    current_cost: float,
    efficiency: float,
) -> Dict[str, Any]:
    """
    Run a full negotiation session and return the result.

    Args:
        current_cost: The subscription's current monthly price.
        efficiency:   Usage-efficiency ratio (0–1+) from the analyzer.

    Returns:
        Dictionary with keys:
            rounds        – list of per-round dicts
            final_price   – agreed or last-offered price
            original_cost – the starting price
            savings_pct   – percentage saved (can be 0 or negative)
            total_rounds  – how many rounds were played
            status        – "accepted" or "rejected"
    """
    rounds: List[Dict[str, Any]] = []
    agent_offer = _initial_offer(current_cost, efficiency)

    for rnd in range(1, _MAX_ROUNDS + 1):
        counter = _provider_counter(current_cost, agent_offer, rnd)
        accepted = _is_accepted(agent_offer, counter)

        status = "accepted" if accepted else ("final" if rnd == _MAX_ROUNDS else "pending")

        rounds.append({
            "round_number": rnd,
            "agent_offer": agent_offer,
            "provider_counter": counter,
            "status": status,
            "notes": (
                f"Round {rnd}: agent offered ₹{agent_offer}, "
                f"provider countered ₹{counter}. "
                f"{'Deal accepted.' if accepted else 'Continuing.'}"
            ),
        })

        if accepted:
            break

        # Minimum rounds guard — keep negotiating
        if rnd < _MIN_ROUNDS or rnd < _MAX_ROUNDS:
            agent_offer = _adjust_offer(agent_offer, counter, rnd)

    # ── Build summary ────────────────────────────────────────
    last = rounds[-1]
    final_price = last["agent_offer"] if last["status"] == "accepted" else last["provider_counter"]
    savings_pct = round((1 - final_price / current_cost) * 100, 2) if current_cost > 0 else 0

    return {
        "rounds": rounds,
        "final_price": final_price,
        "original_cost": current_cost,
        "savings_pct": savings_pct,
        "total_rounds": len(rounds),
        "status": last["status"],
    }
