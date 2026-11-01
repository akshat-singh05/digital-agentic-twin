"""
Negotiation Engine — Pure offer–counteroffer logic against a SIMULATED provider.

There is no real provider: ``_provider_counter`` is a stochastic model
of one.  Results are reproducible — the same ``seed`` gives the same
rounds — so every outcome can be replayed and audited.

Agent strategy:
  - Opening offer depends on utilisation (underutilized → ask for more).
  - Each round the agent moves toward the provider's counter, but never
    above its reservation (walk-away) price.  The caller sets that price
    from the user's best alternative (BATNA) and the minimum saving worth
    a change; without one, the agent never offers more than today's price.
  - Deal when the two sides are within 3 % and the price is at or below
    the reservation price; otherwise the session ends without agreement.

This module contains ONLY stateless computation.
No database access, no framework imports.
"""

import random
from typing import Any, Dict, List, Optional

# ── Tunables ─────────────────────────────────────────────────
_MIN_ROUNDS = 3
_MAX_ROUNDS = 5
_ACCEPTANCE_THRESHOLD = 0.03   # ≤ 3 % gap → deal accepted


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _initial_offer(current_cost: float, efficiency: float, rng: random.Random) -> float:
    """
    Generate the agent's opening offer based on plan utilisation.

    Low efficiency  → ask for a big discount (25–35 %).
    Mid efficiency  → ask for a modest discount (5–10 %).
    High efficiency → offer the current price (or a small premium).
    """
    if efficiency < 0.4:
        discount = 0.25 + rng.uniform(0, 0.10)
    elif efficiency <= 0.8:
        discount = 0.05 + rng.uniform(0, 0.05)
    else:
        discount = -rng.uniform(0, 0.05)

    offer = current_cost * (1 - discount)
    return round(max(offer, 0), 2)


def _provider_counter(
    current_cost: float,
    agent_offer: float,
    round_number: int,
    rng: random.Random,
) -> float:
    """
    Simulate the provider's counter-offer.

    The provider starts close to the current price and concedes
    gradually over successive rounds (up to 5 % per round), never
    going below the agent's offer.
    """
    max_concession = 0.05 * round_number
    concession = rng.uniform(0, max_concession)
    counter = max(current_cost * (1 - concession), agent_offer)
    return round(counter, 2)


def _adjust_offer(
    previous_offer: float,
    provider_counter: float,
    round_number: int,
) -> float:
    """
    Agent adjusts its offer upward toward the provider's counter.

    The step size increases with each round so that the negotiation
    converges (30 % → 60 % of the gap).
    """
    gap = provider_counter - previous_offer
    step = gap * (0.2 + 0.1 * round_number)
    return round(previous_offer + step, 2)


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
    reservation_price: Optional[float] = None,
    seed: Any = None,
) -> Dict[str, Any]:
    """
    Run a full negotiation session and return the result.

    Args:
        current_cost:      The subscription's current monthly price.
        efficiency:        Usage-efficiency ratio (0–1+) from the analyzer.
        reservation_price: Highest price the agent will agree to
                           (default: the current price).
        seed:              Seed for the simulated provider (reproducibility).

    Returns:
        Dictionary with keys:
            rounds            – list of per-round dicts
            final_price       – agreed price, or the provider's last counter
            original_cost     – the starting price
            reservation_price – the walk-away price used
            savings_pct       – percentage saved (0 or negative without a deal)
            total_rounds      – how many rounds were played
            status            – "accepted" or "final" (no agreement)
    """
    rng = random.Random(seed)
    reservation = round(current_cost if reservation_price is None else reservation_price, 2)

    rounds: List[Dict[str, Any]] = []
    agent_offer = min(_initial_offer(current_cost, efficiency, rng), reservation)

    for rnd in range(1, _MAX_ROUNDS + 1):
        counter = _provider_counter(current_cost, agent_offer, rnd, rng)
        accepted = _is_accepted(agent_offer, counter) and agent_offer <= reservation

        status = "accepted" if accepted else ("final" if rnd == _MAX_ROUNDS else "pending")
        if accepted:
            note = "Deal accepted."
        elif rnd == _MAX_ROUNDS:
            note = f"No agreement within the walk-away price of ₹{reservation}."
        else:
            note = "Continuing."

        rounds.append({
            "round_number": rnd,
            "agent_offer": agent_offer,
            "provider_counter": counter,
            "status": status,
            "notes": (
                f"Round {rnd}: agent offered ₹{agent_offer}, "
                f"provider countered ₹{counter}. {note}"
            ),
        })

        if accepted:
            break
        # Concede toward the counter, but never past the walk-away price
        agent_offer = min(_adjust_offer(agent_offer, counter, rnd), reservation)

    # ── Build summary ────────────────────────────────────────
    last = rounds[-1]
    final_price = last["agent_offer"] if last["status"] == "accepted" else last["provider_counter"]
    savings_pct = (
        round((1 - final_price / current_cost) * 100, 2)
        if current_cost > 0 and last["status"] == "accepted" else 0.0
    )

    return {
        "rounds": rounds,
        "final_price": final_price,
        "original_cost": current_cost,
        "reservation_price": reservation,
        "savings_pct": savings_pct,
        "total_rounds": len(rounds),
        "status": last["status"],
    }
