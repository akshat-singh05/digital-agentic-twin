"""API and pipeline integration tests (TestClient + throwaway SQLite)."""

import json

import app.modules.negotiation_agent.engine as engine
from tests.conftest import add_usage, approve, get_subscription, make_subscription, make_user


# ── Error envelope & request limits ──────────────────────────
def test_not_found_uses_standard_error_envelope(client):
    make_user(client)
    res = client.get("/api/users/9999")
    assert res.status_code == 404
    assert res.json() == {"status": "error", "message": "User with id 9999 not found"}


def test_validation_error_uses_standard_error_envelope(client):
    res = client.post("/api/auth/register", json={"name": ""})
    assert res.status_code == 422
    body = res.json()
    assert body["status"] == "error"
    assert "email" in body["message"]


def test_unknown_route_uses_standard_error_envelope(client):
    res = client.get("/api/does-not-exist")
    assert res.status_code == 404
    assert res.json()["status"] == "error"


def test_duplicate_email_is_conflict(client):
    make_user(client)
    res = client.post("/api/auth/register",
                      json={"name": "Again", "email": "user@example.com", "password": "another-pass"})
    assert res.status_code == 409
    assert res.json()["status"] == "error"


def test_pagination_limit_is_capped(client):
    uid = make_user(client, role="admin")
    assert client.get("/api/users?limit=100000000").status_code == 422
    assert client.get("/api/users?skip=-1").status_code == 422
    assert client.get(f"/api/audit/{uid}?limit=100000000").status_code == 422


# ── Pipeline targets one subscription end to end ─────────────
def test_pipeline_acts_on_the_underutilized_subscription(client):
    uid = make_user(client)
    jio = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)
    # Created later and has no usage data: must NOT become the target.
    make_subscription(client, uid, provider="Netflix", plan_name="Premium",
                      monthly_cost=649.0, data_limit_gb=None, call_minutes_limit=None)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert data["analysis"]["optimization_target"]["subscription_id"] == jio
    assert data["negotiation"]["subscription_id"] == jio
    assert data["switching"]["subscription_id"] == jio
    # Negotiation must use the target's own efficiency (20 / 100 GB).
    assert data["negotiation"]["efficiency_used"] == 0.2


def test_pipeline_skips_negotiation_without_an_opportunity(client):
    uid = make_user(client)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=60.0)  # 60% → "keep"

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert data["analysis"]["recommendation"] == "keep"
    assert data["negotiation"] is None
    assert data["switching"] is None
    assert set(data["skipped"]) == {"negotiation", "switching"}
    assert data["errors"] is None
    assert data["final_status"] == "completed"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0


def test_negotiate_without_an_opportunity_is_conflict(client):
    uid = make_user(client)
    make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=60.0)
    res = client.post(f"/api/negotiate/{uid}")
    assert res.status_code == 409
    assert res.json()["status"] == "error"


def test_switch_without_negotiation_is_conflict(client):
    uid = make_user(client)
    make_subscription(client, uid)
    res = client.post(f"/api/switch/{uid}")
    assert res.status_code == 409


# ── Switching applies only an agreed price ───────────────────
def test_switch_only_proposes_and_approval_applies_the_accepted_price(client):
    uid = make_user(client)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    neg = client.post(f"/api/negotiate/{uid}").json()["data"]
    sw = client.post(f"/api/switch/{uid}").json()["data"]

    # Proposing never changes the subscription.
    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    if sw["proposed"]:
        assert neg["status"] == "accepted"
        approve(client, sw["recommendation_id"])
        assert get_subscription(client, sub)["monthly_cost"] == neg["final_price"]


def test_switch_is_not_proposed_when_negotiation_failed(client, monkeypatch):
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: False)
    uid = make_user(client)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    neg = client.post(f"/api/negotiate/{uid}").json()["data"]
    sw = client.post(f"/api/switch/{uid}").json()["data"]

    assert neg["status"] == "final"
    assert sw["proposed"] is False and sw["recommendation_id"] is None
    assert "without agreement" in sw["reason"]
    assert get_subscription(client, sub)["monthly_cost"] == 599.0


def _forced_deal(monkeypatch):
    """Make every negotiation end in a deal at exactly 10% off."""
    monkeypatch.setattr(engine, "_initial_offer", lambda cost, eff, rng: round(cost * 0.9, 2))
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: True)


def test_only_one_open_proposal_per_subscription(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid = make_user(client)
    make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    client.post(f"/api/negotiate/{uid}")
    assert client.post(f"/api/switch/{uid}").json()["data"]["proposed"] is True
    assert client.post(f"/api/switch/{uid}").status_code == 409


def test_same_negotiation_cannot_be_applied_twice(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid = make_user(client)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    client.post(f"/api/negotiate/{uid}")
    first = client.post(f"/api/switch/{uid}").json()["data"]
    approve(client, first["recommendation_id"])
    second = client.post(f"/api/switch/{uid}").json()["data"]

    assert second["proposed"] is False
    assert get_subscription(client, sub)["monthly_cost"] == 539.1


def test_snapshot_is_written_only_when_a_change_is_applied(client, monkeypatch):
    from app.database import SessionLocal
    from app.models import Subscription

    _forced_deal(monkeypatch)
    uid = make_user(client)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    client.post(f"/api/negotiate/{uid}")
    approve(client, client.post(f"/api/switch/{uid}").json()["data"]["recommendation_id"])
    client.post(f"/api/switch/{uid}")   # not proposed: snapshot must be untouched

    with SessionLocal() as db:
        snapshot = json.loads(db.get(Subscription, sub).previous_plan_snapshot)
    assert snapshot["monthly_cost"] == 599.0


# ── Audit trail ──────────────────────────────────────────────
def test_pipeline_writes_one_audit_entry_per_decision(client):
    uid = make_user(client)
    make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    logs = client.get(f"/api/audit/{uid}").json()["data"]

    actions = sorted(log["action"] for log in logs if log["action"] != "sign_in")
    assert actions == ["analysis", "negotiation", "switching"]
    assert sorted(data["audit_logged"]) == actions
    for log in logs:
        assert "₹0 → ₹0" not in log["description"]
        assert "Plan downgraded" not in log["description"]


def test_standalone_agent_calls_are_audited_once(client):
    uid = make_user(client)
    make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)

    client.post(f"/api/analyze/{uid}")
    client.post(f"/api/negotiate/{uid}")
    client.post(f"/api/switch/{uid}")

    actions = sorted(log["action"] for log in client.get(f"/api/audit/{uid}").json()["data"]
                     if log["action"] != "sign_in")
    assert actions == ["analysis", "negotiation", "switching"]
