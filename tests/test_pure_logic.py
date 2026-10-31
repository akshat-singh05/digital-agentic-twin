"""Unit tests for the stateless agent logic (no DB, no HTTP)."""

import random
import statistics

import pytest

from app.modules.audit_logger.formatter import format_audit_message
from app.modules.plan_switching.executor import evaluate_switch
from app.modules.privacy_mediator.sanitizer import _laplace_noise, sanitize_record
from app.modules.usage_analyzer.analyzer import pick_optimization_target


# ── Privacy Mediator ─────────────────────────────────────────
def test_laplace_noise_matches_laplace_distribution():
    random.seed(7)
    scale = 2.0
    samples = [_laplace_noise(scale) for _ in range(40_000)]
    magnitudes = [abs(x) for x in samples]

    # For Laplace(0, b): E|X| = b, P(|X| < 0.1b) = 1 - e^-0.1 ≈ 0.095,
    # P(|X| > 3b) = e^-3 ≈ 0.05, and the distribution is symmetric.
    assert statistics.mean(magnitudes) == pytest.approx(scale, rel=0.05)
    small = sum(m < 0.1 * scale for m in magnitudes) / len(magnitudes)
    assert small == pytest.approx(0.095, abs=0.01)
    tail = sum(m > 3 * scale for m in magnitudes) / len(magnitudes)
    assert tail == pytest.approx(0.05, abs=0.01)
    assert abs(statistics.mean(samples)) < 0.05 * scale


def test_sanitize_record_clamps_and_does_not_mutate():
    record = {"id": 1, "data_used_gb": 0.0, "call_minutes_used": 0}
    for _ in range(200):
        out = sanitize_record(record)
        assert out["data_used_gb"] >= 0
        assert out["call_minutes_used"] >= 0
    assert record == {"id": 1, "data_used_gb": 0.0, "call_minutes_used": 0}


# ── Plan Switching executor ──────────────────────────────────
_CURRENT = {"monthly_cost": 500.0, "data_limit_gb": 100.0, "call_minutes_limit": 500}
_LOW_USAGE = {"avg_data_used_gb": 30.0, "avg_call_minutes": 100.0}


def test_price_increase_without_added_capacity_is_rejected():
    proposed = dict(_CURRENT, monthly_cost=520.0)
    result = evaluate_switch(_CURRENT, proposed, _LOW_USAGE)
    assert result["applied"] is False
    assert "no additional capacity" in result["reason"]


def test_same_price_same_plan_is_rejected():
    result = evaluate_switch(_CURRENT, dict(_CURRENT), _LOW_USAGE)
    assert result["applied"] is False


def test_price_increase_with_more_capacity_is_allowed():
    proposed = dict(_CURRENT, monthly_cost=520.0, data_limit_gb=150.0)
    assert evaluate_switch(_CURRENT, proposed, _LOW_USAGE)["applied"] is True


def test_unlimited_counts_as_more_capacity():
    proposed = dict(_CURRENT, monthly_cost=520.0, call_minutes_limit=None)
    assert evaluate_switch(_CURRENT, proposed, _LOW_USAGE)["applied"] is True


def test_downgrade_meeting_threshold_is_approved():
    proposed = dict(_CURRENT, monthly_cost=450.0)
    assert evaluate_switch(_CURRENT, proposed, _LOW_USAGE)["applied"] is True


def test_downgrade_below_threshold_is_rejected():
    proposed = dict(_CURRENT, monthly_cost=490.0)
    result = evaluate_switch(_CURRENT, proposed, _LOW_USAGE)
    assert result["applied"] is False
    assert "below the minimum threshold" in result["reason"]


def test_high_sla_risk_is_rejected():
    proposed = dict(_CURRENT, monthly_cost=300.0, data_limit_gb=20.0)
    result = evaluate_switch(_CURRENT, proposed, _LOW_USAGE)
    assert result["applied"] is False
    assert result["risk_flag"] == "high"


# ── Usage Analyzer target selection ──────────────────────────
def test_optimization_target_is_largest_underutilized_opportunity():
    analyses = [
        {"subscription_id": 1, "recommendation": "keep", "savings_estimate": 900},
        {"subscription_id": 2, "recommendation": "downgrade", "savings_estimate": 100},
        {"subscription_id": 3, "recommendation": "downgrade", "savings_estimate": 300},
        {"subscription_id": 4, "recommendation": "no_data", "savings_estimate": 0},
    ]
    assert pick_optimization_target(analyses)["subscription_id"] == 3


def test_no_optimization_target_without_underutilized_plan():
    analyses = [
        {"subscription_id": 1, "recommendation": "keep", "savings_estimate": 200},
        {"subscription_id": 2, "recommendation": "upgrade", "savings_estimate": 0},
    ]
    assert pick_optimization_target(analyses) is None
    assert pick_optimization_target([]) is None


# ── Audit formatter ──────────────────────────────────────────
def test_analysis_message_recommends_rather_than_claims_a_downgrade():
    msg = format_audit_message("analysis", {
        "recommendation": "downgrade", "efficiency": 0.2, "savings_estimate": 479.2,
        "provider": "Jio", "plan_name": "Gold",
    })
    assert "recommended" in msg.lower()
    assert "downgraded" not in msg.lower()


def test_switching_message_uses_real_costs():
    msg = format_audit_message("switching", {
        "applied": True, "risk_flag": "low", "previous_cost": 599.0, "proposed_cost": 540.0,
    })
    assert "₹599.0 → ₹540.0" in msg
    assert "₹0" not in msg


def test_switching_rejection_message_states_actual_reason():
    reason = "Switch rejected: cost reduction of 2.0% is below the minimum threshold of 5.0%."
    msg = format_audit_message("switching", {
        "applied": False, "risk_flag": "low", "previous_cost": 500.0,
        "proposed_cost": 490.0, "reason": reason,
    })
    assert "below the minimum threshold" in msg
    assert "risk of limit breach" not in msg
