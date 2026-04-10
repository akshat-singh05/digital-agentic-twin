"""
Privacy Sanitizer — Pure differential-privacy logic.

Applies controlled Laplace noise to sensitive usage fields so that
raw user data is never exposed directly during negotiation or
external sharing.

This module contains ONLY stateless computation.
No database access, no framework imports.
Designed to be easily swapped for a stronger DP mechanism later.
"""

import random
from typing import Any, Dict, List


# ── Default noise scale (Laplace β) ─────────────────────────
_DEFAULT_DATA_NOISE_SCALE = 0.5   # GB
_DEFAULT_CALL_NOISE_SCALE = 10.0  # minutes


def _laplace_noise(scale: float) -> float:
    """
    Sample a single value from a Laplace(0, scale) distribution.

    Uses the inverse-CDF method so we avoid extra dependencies
    (no numpy / scipy required).
    """
    u = random.uniform(-0.5, 0.5)
    return -scale * (u / abs(u)) * (1 + abs(u)) if u != 0 else 0.0


def sanitize_record(
    record: Dict[str, Any],
    data_noise_scale: float = _DEFAULT_DATA_NOISE_SCALE,
    call_noise_scale: float = _DEFAULT_CALL_NOISE_SCALE,
) -> Dict[str, Any]:
    """
    Sanitize a single usage record by adding Laplace noise.

    Args:
        record:           Dictionary with at least ``data_used_gb`` and
                          ``call_minutes_used`` keys.
        data_noise_scale: Laplace β for data usage noise (GB).
        call_noise_scale: Laplace β for call minutes noise.

    Returns:
        A **new** dictionary with noised values clamped to ≥ 0.
        All other fields are passed through unchanged.
    """
    sanitized = dict(record)  # shallow copy — don't mutate original

    raw_data = record.get("data_used_gb") or 0.0
    raw_calls = record.get("call_minutes_used") or 0

    noised_data = raw_data + _laplace_noise(data_noise_scale)
    noised_calls = raw_calls + _laplace_noise(call_noise_scale)

    # Clamp: values must never go below zero
    sanitized["data_used_gb"] = round(max(0.0, noised_data), 4)
    sanitized["call_minutes_used"] = max(0, int(round(noised_calls)))

    return sanitized


def sanitize_records(
    records: List[Dict[str, Any]],
    data_noise_scale: float = _DEFAULT_DATA_NOISE_SCALE,
    call_noise_scale: float = _DEFAULT_CALL_NOISE_SCALE,
) -> List[Dict[str, Any]]:
    """
    Sanitize a list of usage records.

    Convenience wrapper around ``sanitize_record`` that processes an
    entire batch.

    Args:
        records:          List of usage-record dicts.
        data_noise_scale: Laplace β for data usage noise (GB).
        call_noise_scale: Laplace β for call minutes noise.

    Returns:
        New list of sanitized record dicts (originals are not mutated).
    """
    return [
        sanitize_record(r, data_noise_scale, call_noise_scale)
        for r in records
    ]
