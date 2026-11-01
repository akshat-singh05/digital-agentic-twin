"""Phase B data-model features through the API: catalog, usage linking,
plan history, preferences, consents, and the demo seeder."""

import app.modules.negotiation_agent.engine as engine
from app.database import SessionLocal
from app.models import Consent, PlanCatalog, Subscription, UsageData
from tests.conftest import add_usage, approve, make_subscription, make_user


def _add_plans(*plans):
    with SessionLocal() as db:
        for provider, name, cost, available in plans:
            db.add(PlanCatalog(provider=provider, plan_name=name, category="mobile",
                               monthly_cost=cost, data_limit_gb=50.0, is_available=available,
                               source="demo_seed"))
        db.commit()
        return {p.plan_name: p.id for p in db.query(PlanCatalog)}


# ── Plan catalog ─────────────────────────────────────────────
def test_catalog_lists_available_plans_cheapest_first(client):
    make_user(client)
    _add_plans(("Jio", "Gold", 599, True), ("Jio", "Silver", 399, True),
               ("Jio", "Retired", 199, False), ("Airtel", "Smart", 299, True))

    jio = client.get("/api/plans?provider=Jio").json()["data"]
    assert [p["plan_name"] for p in jio] == ["Silver", "Gold"]
    assert all(p["source"] == "demo_seed" for p in jio)

    everything = client.get("/api/plans?include_unavailable=true").json()["data"]
    assert len(everything) == 4


def test_subscription_catalog_link_is_validated(client):
    ids = _add_plans(("Jio", "Gold", 599, True))
    uid = make_user(client)
    base = {"user_id": uid, "plan_name": "Gold", "monthly_cost": 599}

    assert client.post("/api/subscriptions", json={**base, "provider": "Jio",
                       "catalog_plan_id": 9999}).status_code == 422
    assert client.post("/api/subscriptions", json={**base, "provider": "Airtel",
                       "catalog_plan_id": ids["Gold"]}).status_code == 422
    ok = client.post("/api/subscriptions", json={**base, "provider": "Jio",
                     "catalog_plan_id": ids["Gold"]})
    assert ok.status_code == 201
    assert ok.json()["data"]["catalog_plan_id"] == ids["Gold"]


# ── Usage ↔ subscription linking ─────────────────────────────
def test_usage_is_linked_to_its_subscription(client):
    uid = make_user(client)
    sub = make_subscription(client, uid)
    add_usage(client, uid, months=1)
    records = client.get(f"/api/usage/user/{uid}").json()["data"]
    assert [r["subscription_id"] for r in records] == [sub]


def test_usage_for_another_users_subscription_is_rejected(client):
    other = make_user(client, email="other@example.com")
    other_sub = make_subscription(client, other)
    uid = make_user(client)
    res = client.post("/api/usage", json={
        "user_id": uid, "provider": "Jio", "subscription_id": other_sub,
        "period_start": "2026-01-01T00:00:00", "period_end": "2026-01-28T00:00:00",
    })
    assert res.status_code == 422


def test_usage_ingested_before_subscription_is_linked_later(client):
    uid = make_user(client)
    add_usage(client, uid, months=2)
    sub = make_subscription(client, uid)
    records = client.get(f"/api/usage/user/{uid}").json()["data"]
    assert {r["subscription_id"] for r in records} == {sub}


def test_two_plans_with_one_provider_are_analyzed_separately(client):
    uid = make_user(client)
    light = make_subscription(client, uid, plan_name="Home")
    heavy = make_subscription(client, uid, plan_name="Work")
    for sub, used in ((light, 20.0), (heavy, 90.0)):
        res = client.post("/api/usage", json={
            "user_id": uid, "provider": "Jio", "subscription_id": sub,
            "period_start": "2026-01-01T00:00:00", "period_end": "2026-01-28T00:00:00",
            "data_used_gb": used,
        })
        assert res.status_code == 201

    analysis = client.post(f"/api/analyze/{uid}").json()["data"]["analysis"]
    efficiency = {a["subscription_id"]: a["efficiency"] for a in analysis}
    assert efficiency == {light: 0.2, heavy: 0.9}


# ── Plan history ─────────────────────────────────────────────
def test_applied_switch_is_recorded_in_plan_history(client, monkeypatch):
    monkeypatch.setattr(engine, "_initial_offer", lambda cost, eff, rng: round(cost * 0.9, 2))
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: True)
    uid = make_user(client)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    client.post(f"/api/negotiate/{uid}")
    rec_id = client.post(f"/api/switch/{uid}").json()["data"]["recommendation_id"]
    assert client.get(f"/api/subscriptions/{sub}/history").json()["data"] == []  # proposal only
    approve(client, rec_id)
    client.post(f"/api/switch/{uid}")   # not proposed: must not add history

    history = client.get(f"/api/subscriptions/{sub}/history").json()["data"]
    assert len(history) == 1
    assert history[0]["change_type"] == "price_change"
    assert history[0]["recommendation_id"] == rec_id
    assert (history[0]["previous_cost"], history[0]["new_cost"]) == (599.0, 539.1)
    assert client.get("/api/subscriptions/9999/history").status_code == 404


# ── Preferences ──────────────────────────────────────────────
def test_preferences_default_then_replace(client):
    uid = make_user(client)
    default = client.get(f"/api/users/{uid}/preferences").json()["data"]
    assert default["required_features"] == [] and default["allow_provider_change"] is False

    put = client.put(f"/api/users/{uid}/preferences", json={
        "max_monthly_budget": 500, "min_data_gb": 40, "required_features": ["5g"],
    })
    assert put.status_code == 200
    stored = client.get(f"/api/users/{uid}/preferences").json()["data"]
    assert stored["max_monthly_budget"] == 500
    assert stored["required_features"] == ["5g"]

    # PUT replaces: omitted fields go back to defaults
    client.put(f"/api/users/{uid}/preferences", json={"min_data_gb": 10})
    stored = client.get(f"/api/users/{uid}/preferences").json()["data"]
    assert stored["max_monthly_budget"] is None and stored["required_features"] == []


def test_preferences_are_validated(client):
    uid = make_user(client)
    assert client.put(f"/api/users/{uid}/preferences", json={"min_data_gb": -1}).status_code == 422
    assert client.get("/api/users/9999/preferences").status_code == 404


# ── Consents ─────────────────────────────────────────────────
def test_consents_default_to_denied(client):
    uid = make_user(client, consents=False)
    consents = client.get(f"/api/users/{uid}/consents").json()["data"]
    assert {c["scope"] for c in consents} == {
        "usage_analysis", "share_usage_with_provider",
        "negotiate_on_behalf", "execute_plan_changes",
    }
    assert not any(c["granted"] for c in consents)


def test_consent_grant_and_revoke_are_audited(client):
    uid = make_user(client, consents=False)
    url = f"/api/users/{uid}/consents/negotiate_on_behalf"

    assert client.put(url, json={"granted": True}).json()["data"]["granted"] is True
    assert client.put(url, json={"granted": False}).json()["data"]["granted"] is False

    logs = client.get(f"/api/audit/{uid}").json()["data"]
    consent_logs = [l for l in logs if l["action"] == "consent"]
    assert len(consent_logs) == 2
    assert all(l["module"] == "privacy_mediator" for l in consent_logs)
    assert {l["description"] for l in consent_logs} == {
        "Consent granted for 'negotiate_on_behalf'.",
        "Consent revoked for 'negotiate_on_behalf'.",
    }


def test_unknown_consent_scope_is_rejected(client):
    uid = make_user(client)
    res = client.put(f"/api/users/{uid}/consents/sell_my_data", json={"granted": True})
    assert res.status_code == 404
    assert "Unknown consent scope" in res.json()["message"]


# ── Demo seeder ──────────────────────────────────────────────
def test_seed_reset_builds_linked_labelled_demo_data(client, capsys):
    from app.utils.seed_data import seed

    seed(reset=True)

    with SessionLocal() as db:
        assert db.query(PlanCatalog).count() > 0
        assert {p.source for p in db.query(PlanCatalog)} == {"demo_seed"}
        assert db.query(Subscription).filter(Subscription.catalog_plan_id.is_(None)).count() == 0
        assert db.query(UsageData).filter(UsageData.subscription_id.is_(None)).count() == 0
        assert db.query(Consent).filter(Consent.granted == False).count() == 0
