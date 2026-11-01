"""Phase D: usage metrics, deterministic negotiation, catalog alternatives,
and evidence-backed proposals."""

from datetime import datetime
from types import SimpleNamespace

import pytest

import app.modules.negotiation_agent.engine as engine
from app.database import SessionLocal
from app.models import PlanCatalog
from app.modules.negotiation_agent.engine import negotiate
from app.modules.plan_switching.alternatives import describe_tradeoffs, rank_alternatives
from app.modules.usage_analyzer.analyzer import analyze_usage
from tests.conftest import approve, get_subscription, make_subscription, make_user


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _plan(cost=599.0, data=100.0, calls=500):
    return SimpleNamespace(monthly_cost=cost, data_limit_gb=data, call_minutes_limit=calls)


def _records(data, calls=None, billing=None):
    calls = calls or [0] * len(data)
    billing = billing or [599.0] * len(data)
    return [
        SimpleNamespace(period_start=datetime(2026, m + 1, 1), data_used_gb=d,
                        call_minutes_used=c, billing_amount=b)
        for m, (d, c, b) in enumerate(zip(data, calls, billing))
    ]


def _catalog(*rows):
    """rows: (id, provider, plan_name, cost, data, calls, features)"""
    return [
        {"id": i, "provider": p, "plan_name": n, "category": "mobile", "monthly_cost": c,
         "data_limit_gb": d, "call_minutes_limit": m, "features": f, "source": "demo_seed"}
        for i, p, n, c, d, m, f in rows
    ]


_JIO_GOLD = {"provider": "Jio", "plan_name": "Gold", "category": "mobile", "monthly_cost": 599.0,
             "data_limit_gb": 100.0, "call_minutes_limit": 500, "features": '{"5g": true}'}
_NO_PREFS = {"required_features": [], "preferred_providers": [], "allow_provider_change": False}


# ── Usage Analyzer ───────────────────────────────────────────
def test_binding_metric_is_the_more_used_one():
    result = analyze_usage(_records([20] * 6, calls=[450] * 6), _plan())
    assert result["binding_metric"] == "calls"
    assert result["efficiency"] == 0.9 and result["recommendation"] == "upgrade"
    assert result["data_utilization"] == 0.2


def test_peaks_trend_and_projection():
    result = analyze_usage(_records([10, 12, 14, 16, 18, 20]), _plan())
    assert result["peak_data_usage"] == 20
    assert result["data_trend"] == "rising" and result["data_trend_gb_per_month"] == 2.0
    assert result["projected_data_usage"] == 22.0


def test_rising_usage_suppresses_a_downgrade():
    # 30 GB average on 100 GB looks underutilized, but it is climbing fast.
    result = analyze_usage(_records([15, 22, 29, 36, 43]), _plan())
    assert result["efficiency"] < 0.4
    assert result["recommendation"] == "keep"
    assert "rising" in result["message"]


def test_uncapped_plan_is_not_measurable():
    result = analyze_usage(_records([5] * 4), _plan(data=None, calls=None))
    assert result["recommendation"] == "no_metric" and result["efficiency"] is None


def test_overage_counts_only_since_the_last_price_change():
    records = _records([120] * 4, billing=[799, 799, 599, 599])
    assert analyze_usage(records, _plan())["avg_overage"] == 100.0
    since = datetime(2026, 3, 1)
    assert analyze_usage(records, _plan(), overage_since=since)["avg_overage"] == 0.0


def test_volatile_usage_lowers_confidence():
    steady = analyze_usage(_records([20] * 6), _plan())
    spiky = analyze_usage(_records([5, 35, 5, 35, 5, 35]), _plan())
    assert steady["confidence_score"] == 1.0
    assert spiky["confidence_score"] < steady["confidence_score"]


def test_unused_spend_is_reported_not_called_savings():
    result = analyze_usage(_records([20] * 6), _plan())
    assert result["unused_spend"] == 479.2
    assert "savings_estimate" not in result


# ── Negotiation engine ───────────────────────────────────────
def test_same_seed_gives_the_same_negotiation():
    assert negotiate(599, 0.2, seed="7:1") == negotiate(599, 0.2, seed="7:1")
    assert negotiate(599, 0.2, seed="7:1") != negotiate(599, 0.2, seed="7:2")


@pytest.mark.parametrize("seed", range(25))
def test_agent_never_offers_above_its_walk_away_price(seed):
    result = negotiate(599, 0.2, reservation_price=500, seed=seed)
    assert all(r["agent_offer"] <= 500 for r in result["rounds"])
    if result["status"] == "accepted":
        assert result["final_price"] <= 500
    else:
        assert result["savings_pct"] == 0.0


def test_unreachable_walk_away_price_means_no_deal():
    result = negotiate(599, 0.2, reservation_price=200, seed=1)
    assert result["status"] == "final" and result["reservation_price"] == 200
    assert "walk-away" in result["rounds"][-1]["notes"]


# ── Alternatives ranker ──────────────────────────────────────
def _profile(peak=20, projected=20, calls=100, overage=0):
    return {"peak_data_usage": peak, "projected_data_usage": projected,
            "peak_call_usage": calls, "projected_call_usage": calls, "avg_overage": overage}


def test_cheaper_plan_covering_peak_plus_headroom_fits():
    catalog = _catalog((1, "Jio", "Silver", 399, 50, 300, '{"5g": false}'),
                       (2, "Jio", "Mini", 199, 20, 300, None))
    result = rank_alternatives(_JIO_GOLD, catalog, _profile(), _NO_PREFS, 5.0)

    assert result["required"] == {"data_gb": 22.0, "call_minutes": 110.0}
    assert [p["plan_name"] for p in result["fits"]] == ["Silver"]
    assert result["fits"][0]["monthly_savings"] == 200.0
    assert "below the 22.0 GB needed" in result["excluded"][0]["reasons"][0]


def test_preferences_exclude_plans_with_reasons():
    catalog = _catalog((1, "Jio", "Silver", 399, 50, 300, '{"5g": false}'),
                       (2, "Airtel", "Smart", 299, 40, 300, '{"5g": true}'))
    prefs = {**_NO_PREFS, "required_features": ["5g"], "max_monthly_budget": 350}

    result = rank_alternatives(_JIO_GOLD, catalog, _profile(), prefs, 5.0)
    reasons = {p["plan_name"]: p["reasons"] for p in result["excluded"]}

    assert result["fits"] == []
    assert "missing required feature(s): 5g" in reasons["Silver"]
    assert any("over your budget" in r for r in reasons["Silver"])
    assert reasons["Smart"] == ["different provider (not allowed in your preferences)"]

    allowed = rank_alternatives(_JIO_GOLD, catalog, _profile(),
                                {**prefs, "allow_provider_change": True}, 5.0)
    assert [p["plan_name"] for p in allowed["fits"]] == ["Smart"]


def test_overage_makes_a_bigger_plan_the_cheaper_option():
    silver = {**_JIO_GOLD, "plan_name": "Silver", "monthly_cost": 399.0, "data_limit_gb": 50.0}
    catalog = _catalog((1, "Jio", "Gold", 599, 100, 500, None))
    result = rank_alternatives(silver, catalog, _profile(peak=60, projected=60, overage=300), _NO_PREFS, 5.0)

    assert result["effective_current_cost"] == 699.0
    assert result["fits"][0]["monthly_savings"] == 100.0
    tradeoffs = describe_tradeoffs(silver, result["fits"][0], _profile(overage=300))
    assert any("removes the average ₹300" in b for b in tradeoffs["benefits"])


def test_small_saving_is_not_worth_a_switch():
    catalog = _catalog((1, "Jio", "Almost", 590, 100, 500, None))
    result = rank_alternatives(_JIO_GOLD, catalog, _profile(), _NO_PREFS, 5.0)
    assert result["fits"] == [] and "below the 5.0% minimum" in result["excluded"][0]["reasons"][0]


def test_tradeoffs_name_the_drawbacks():
    catalog = _catalog((1, "Jio", "Silver", 399, 50, 300, '{"5g": false}'))
    fit = rank_alternatives(_JIO_GOLD, catalog, _profile(), _NO_PREFS, 5.0)["fits"][0]
    t = describe_tradeoffs(_JIO_GOLD, fit, _profile())
    assert any("drops from 100.0 GB to 50" in d for d in t["drawbacks"])
    assert "Loses feature(s): 5g." in t["drawbacks"]
    assert any("demo data" in d for d in t["drawbacks"])


# ─────────────────────────────────────────────────────────────
# Integration through the API
# ─────────────────────────────────────────────────────────────
def _seed_catalog(*rows):
    with SessionLocal() as db:
        for provider, name, cost, data, calls, features in rows:
            db.add(PlanCatalog(provider=provider, plan_name=name, category="mobile",
                               monthly_cost=cost, data_limit_gb=data, call_minutes_limit=calls,
                               features=features, source="demo_seed"))
        db.commit()
        return {p.plan_name: p.id for p in db.query(PlanCatalog)}


def _add_usage(client, uid, sub, months=6, data=20.0, calls=100, billing=599.0):
    for m in range(1, months + 1):
        res = client.post("/api/usage", json={
            "user_id": uid, "provider": "Jio", "subscription_id": sub,
            "period_start": f"2026-{m:02d}-01T00:00:00", "period_end": f"2026-{m:02d}-28T00:00:00",
            "data_used_gb": data, "call_minutes_used": calls, "billing_amount": billing,
        })
        assert res.status_code == 201, res.text


@pytest.fixture
def jio_catalog():
    return _seed_catalog(("Jio", "Gold", 599.0, 100.0, 500, '{"5g": true}'),
                         ("Jio", "Silver", 399.0, 50.0, 300, '{"5g": false}'))


def test_cycle_proposes_a_cheaper_catalog_plan_with_explanation(client, monkeypatch, jio_catalog):
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: False)   # no deal
    uid = make_user(client)
    sub = client.post("/api/subscriptions", json={   # linked: inherits Gold's 5G feature
        "user_id": uid, "provider": "Jio", "plan_name": "Gold", "monthly_cost": 599.0,
        "data_limit_gb": 100.0, "call_minutes_limit": 500, "catalog_plan_id": jio_catalog["Gold"],
    }).json()["data"]["id"]
    _add_usage(client, uid, sub)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    rec = client.get(f"/api/recommendations/{data['recommendation']['recommendation_id']}").json()["data"]

    assert data["negotiation"]["status"] == "final"
    assert data["negotiation"]["reservation_price"] == 399.0          # BATNA = Silver
    assert data["switching"]["action"] == "switch_plan"
    assert data["switching"]["proposed_plan"]["plan_name"] == "Silver"
    assert rec["proposed_plan_id"] == jio_catalog["Silver"]
    assert rec["estimated_monthly_savings"] == 200.0
    explanation = rec["evidence"]["explanation"]
    assert explanation["what"].startswith("Switch from Jio Gold to Jio Silver")
    assert "Saves ₹200.0/month (33.39%)." in explanation["benefits"]
    assert "Loses feature(s): 5g." in explanation["drawbacks"]
    assert explanation["data_used"]["peak_data_usage"] == 20.0
    assert get_subscription(client, sub)["plan_name"] == "Gold"       # nothing applied yet


def test_catalog_alternative_caps_the_negotiated_price(client, monkeypatch, jio_catalog):
    monkeypatch.setattr(engine, "_initial_offer", lambda cost, eff, rng: round(cost * 0.9, 2))
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: True)
    uid = make_user(client)
    sub = make_subscription(client, uid, plan_name="Gold")
    _add_usage(client, uid, sub)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]

    # The negotiated ₹539.1 was capped by the ₹399 walk-away price, so the deal
    # is ₹399 — the same saving as Silver, so keeping Gold's limits wins the tie.
    considered = {o["action"]: o["monthly_savings"] for o in data["switching"]["options_considered"]}
    assert considered == {"apply_negotiated_price": 200.0, "switch_plan": 200.0}
    assert data["switching"]["action"] == "apply_negotiated_price"


def test_approving_a_plan_switch_moves_to_the_catalog_terms(client, monkeypatch, jio_catalog):
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: False)
    uid = make_user(client)
    sub = make_subscription(client, uid, plan_name="Gold")
    _add_usage(client, uid, sub)
    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]

    approve(client, rec_id)
    after = get_subscription(client, sub)
    assert (after["plan_name"], after["monthly_cost"], after["data_limit_gb"]) == ("Silver", 399.0, 50.0)
    assert after["catalog_plan_id"] == jio_catalog["Silver"]
    history = client.get(f"/api/subscriptions/{sub}/history").json()["data"]
    assert history[-1]["change_type"] == "plan_change"

    client.post(f"/api/subscriptions/{sub}/rollback")
    restored = get_subscription(client, sub)
    assert (restored["plan_name"], restored["monthly_cost"], restored["data_limit_gb"]) == ("Gold", 599.0, 100.0)


def test_catalog_price_change_expires_a_plan_switch(client, monkeypatch, jio_catalog):
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: False)
    uid = make_user(client)
    sub = make_subscription(client, uid, plan_name="Gold")
    _add_usage(client, uid, sub)
    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]

    with SessionLocal() as db:
        db.get(PlanCatalog, jio_catalog["Silver"]).monthly_cost = 449.0
        db.commit()

    assert client.post(f"/api/recommendations/{rec_id}/approve").status_code == 409
    assert client.get(f"/api/recommendations/{rec_id}").json()["data"]["status"] == "expired"


def test_required_feature_blocks_the_switch(client, monkeypatch, jio_catalog):
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: False)
    uid = make_user(client)
    client.put(f"/api/users/{uid}/preferences", json={"required_features": ["5g"]})
    sub = make_subscription(client, uid, plan_name="Gold")
    _add_usage(client, uid, sub)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    assert data["switching"]["proposed"] is False
    assert "missing required feature(s): 5g" in data["switching"]["reason"]


def test_overutilized_plan_with_overage_gets_an_upgrade_proposal(client, jio_catalog):
    uid = make_user(client)
    sub = make_subscription(client, uid, plan_name="Silver", monthly_cost=399.0,
                            data_limit_gb=50.0, call_minutes_limit=300)
    _add_usage(client, uid, sub, data=60.0, billing=699.0)   # ₹300/month overage

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert data["analysis"]["recommendation"] == "upgrade"
    assert "overutilized" in data["skipped"]["negotiation"]
    assert data["switching"]["action"] == "switch_plan"
    assert data["switching"]["proposed_plan"]["plan_name"] == "Gold"
    assert data["switching"]["estimated_monthly_savings"] == 100.0


def test_alternatives_endpoint_explains_every_plan(client, jio_catalog):
    uid = make_user(client)
    sub = make_subscription(client, uid, plan_name="Gold")
    _add_usage(client, uid, sub, data=45.0)

    alt = client.get(f"/api/subscriptions/{sub}/alternatives").json()["data"]
    assert alt["required"]["data_gb"] == 49.5
    assert [p["plan_name"] for p in alt["fits"]] == ["Silver"]
    assert alt["usage_profile"]["peak_data_usage"] == 45.0

    other = make_user(client, email="nope@example.com", consents=False)
    other_sub = make_subscription(client, other)
    assert client.get(f"/api/subscriptions/{other_sub}/alternatives").status_code == 403


def test_negotiation_without_catalog_walks_away_below_five_percent(client):
    uid = make_user(client)
    sub = make_subscription(client, uid)
    _add_usage(client, uid, sub)
    neg = client.post(f"/api/negotiate/{uid}").json()["data"]
    assert neg["reservation_price"] == 569.05 and neg["best_alternative"] is None
    assert all(r["agent_offer"] <= 569.05 for r in neg["rounds"])


def test_uncapped_subscription_is_not_a_target(client):
    uid = make_user(client)
    make_subscription(client, uid, provider="Netflix", plan_name="Premium",
                      monthly_cost=649.0, data_limit_gb=None, call_minutes_limit=None)
    client.post("/api/usage", json={
        "user_id": uid, "provider": "Netflix", "data_used_gb": 30,
        "period_start": "2026-01-01T00:00:00", "period_end": "2026-01-28T00:00:00",
    })
    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    assert data["analysis"]["recommendation"] == "no_metric"
    assert data["skipped"]["negotiation"] == "no subscription with an optimization opportunity"
