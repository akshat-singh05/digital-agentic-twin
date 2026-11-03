"""Phase H: rejection cool-down and sign-in auditing."""

from datetime import datetime, timedelta, timezone

import app.api.routes as routes
from app import config
from app.database import SessionLocal
from app.models import Recommendation
from app.modules.plan_switching.service import _recently_rejected
from tests.conftest import TEST_PASSWORD, agree_price, as_user, make_user
from tests.test_orchestrator import _underutilized_user


# ── Rejection cool-down ──────────────────────────────────────
def _rejected_proposal(client):
    uid, sub = _underutilized_user(client)
    agree_price(client, uid)
    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]
    assert client.post(f"/api/recommendations/{rec_id}/reject").status_code == 200
    return uid, sub, rec_id


def test_rejected_proposal_is_not_offered_again_unchanged(client, monkeypatch):
    uid, sub, rec_id = _rejected_proposal(client)

    again = client.post(f"/api/switch/{uid}").json()["data"]

    assert again["proposed"] is False
    assert f"you rejected the same proposal (#{rec_id})" in again["reason"]
    recs = client.get(f"/api/recommendations/user/{uid}").json()["data"]
    assert [r["status"] for r in recs] == ["rejected"]            # nothing new was created


def test_run_cycle_respects_the_cool_down(client, monkeypatch):
    uid, sub, rec_id = _rejected_proposal(client)

    run = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert run["recommendation"] is None and run["final_status"] != "awaiting_approval"
    assert len(client.get(f"/api/recommendations/user/{uid}").json()["data"]) == 1


def test_proposal_returns_after_the_cool_down(client, monkeypatch):
    uid, sub, rec_id = _rejected_proposal(client)
    with SessionLocal() as db:
        rec = db.get(Recommendation, rec_id)
        rec.decided_at = datetime.now(timezone.utc) - timedelta(days=config.REJECTION_COOLDOWN_DAYS + 1)
        db.commit()

    assert client.post(f"/api/switch/{uid}").json()["data"]["proposed"] is True


def test_cool_down_can_be_disabled(client, monkeypatch):
    uid, sub, rec_id = _rejected_proposal(client)
    monkeypatch.setattr(config, "REJECTION_COOLDOWN_DAYS", 0)

    assert client.post(f"/api/switch/{uid}").json()["data"]["proposed"] is True


def test_different_terms_are_never_held_back(client, monkeypatch):
    uid, sub, rec_id = _rejected_proposal(client)
    with SessionLocal() as db:
        rec = db.get(Recommendation, rec_id)
        same = {"action": rec.action, "proposed_cost": rec.proposed_cost,
                "plan": {"plan_id": rec.proposed_plan_id} if rec.proposed_plan_id else None}
        assert _recently_rejected(db, rec.subscription_id, same).id == rec_id
        assert _recently_rejected(db, rec.subscription_id, {**same, "proposed_cost": rec.proposed_cost - 1}) is None
        assert _recently_rejected(db, rec.subscription_id, {**same, "action": "other"}) is None
        assert _recently_rejected(db, rec.subscription_id + 1, same) is None


# ── Sign-in auditing ─────────────────────────────────────────
def _sign_in_logs(client, uid):
    return [l for l in client.get(f"/api/audit/{uid}").json()["data"] if l["action"] == "sign_in"]


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_register_login_and_failed_login_are_audited(client):
    res = client.post("/api/auth/register",
                      json={"name": "A", "email": "a@example.com", "password": TEST_PASSWORD})
    uid = res.json()["data"]["user"]["id"]
    token = res.json()["data"]["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"

    assert _login(client, "a@example.com", TEST_PASSWORD).status_code == 200
    assert _login(client, "a@example.com", "not-the-password").status_code == 401

    descriptions = [l["description"] for l in _sign_in_logs(client, uid)]
    assert sorted(descriptions) == [
        "Account created.", "Failed sign-in attempt: wrong password.", "Signed in."]
    assert all(l["module"] == "authentication" for l in _sign_in_logs(client, uid))


def test_audit_records_neither_password_nor_unknown_emails(client):
    uid = make_user(client, email="a@example.com")
    _login(client, "a@example.com", "guess-number-one")
    _login(client, "nobody@example.com", "guess-number-two")

    raw = client.get(f"/api/audit/{uid}").text
    assert "guess-number-one" not in raw and "guess-number-two" not in raw
    assert len(_sign_in_logs(client, uid)) == 2        # register + the failed attempt on a real account


def test_an_audit_failure_never_blocks_sign_in(client, monkeypatch):
    make_user(client, email="a@example.com")

    def broken(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(routes, "create_audit_log", broken)
    assert _login(client, "a@example.com", TEST_PASSWORD).status_code == 200
