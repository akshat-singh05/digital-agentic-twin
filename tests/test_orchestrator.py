"""Orchestrator, Privacy Mediator gate, and the approval workflow."""

import app.modules.negotiation_agent.engine as engine
import app.orchestrator.pipeline as pipeline
from app.database import SessionLocal
from app.models import Subscription
from tests.conftest import add_usage, approve, get_subscription, make_subscription, make_user


def _forced_deal(monkeypatch):
    """Make every negotiation end in a deal at exactly 10% off."""
    monkeypatch.setattr(engine, "_initial_offer", lambda cost, eff: round(cost * 0.9, 2))
    monkeypatch.setattr(engine, "_is_accepted", lambda offer, counter: True)


def _underutilized_user(client, consents=True):
    uid = make_user(client, consents=consents)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)
    return uid, sub


def _steps(client, run_id):
    run = client.get(f"/api/runs/{run_id}").json()["data"]
    return {s["agent"]: s for s in run["steps"]}, run


# ── Run recording ────────────────────────────────────────────
def test_run_records_every_agent_step_and_tags_audit_entries(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, _ = _underutilized_user(client)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    steps, run = _steps(client, data["run_id"])

    assert run["status"] == data["final_status"] == "awaiting_approval"
    assert list(steps) == ["usage_analyzer", "privacy_mediator", "negotiation_agent", "plan_switching"]
    assert all(s["status"] == "completed" and s["duration_ms"] is not None for s in steps.values())
    assert steps["negotiation_agent"]["summary"]["simulated"] is True

    logs = client.get(f"/api/audit/{uid}").json()["data"]
    assert logs and all(l["run_id"] == data["run_id"] for l in logs)
    assert client.get(f"/api/runs/user/{uid}").json()["data"][0]["id"] == data["run_id"]


def test_audit_entries_store_the_run_id(client, monkeypatch):
    from app.models import AuditLog

    _forced_deal(monkeypatch)
    uid, _ = _underutilized_user(client)
    run_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["run_id"]

    with SessionLocal() as db:
        run_ids = {a.run_id for a in db.query(AuditLog).filter(AuditLog.user_id == uid)}
    assert run_ids == {run_id}


# ── Approval workflow ────────────────────────────────────────
def test_run_proposes_but_never_applies_a_change(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, sub = _underutilized_user(client)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    rec_id = data["recommendation"]["recommendation_id"]

    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    rec = client.get(f"/api/recommendations/{rec_id}").json()["data"]
    assert rec["status"] == "proposed" and rec["requires_approval"] is True
    assert rec["run_id"] == data["run_id"]
    assert rec["proposed_cost"] == 539.1 and rec["estimated_monthly_savings"] == 59.9
    assert rec["evidence"]["simulated_negotiation"] is True
    assert "simulator" in rec["rationale"]
    proposed = client.get(f"/api/recommendations/user/{uid}?status=proposed").json()["data"]
    assert [r["id"] for r in proposed] == [rec_id]


def test_price_does_not_ratchet_down_across_runs(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, sub = _underutilized_user(client)

    first = client.post(f"/api/run-cycle/{uid}").json()["data"]
    second = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    assert second["negotiation"] is None
    pending = f"recommendation #{first['recommendation']['recommendation_id']} is awaiting your decision"
    assert second["skipped"] == {"negotiation": pending, "switching": pending}
    assert len(client.get(f"/api/negotiation/{sub}/history").json()["data"]) == 1


def test_approve_applies_change_and_records_history(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, sub = _underutilized_user(client)
    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]

    result = approve(client, rec_id)

    assert result["status"] == "executed"
    assert get_subscription(client, sub)["monthly_cost"] == 539.1
    history = client.get(f"/api/subscriptions/{sub}/history").json()["data"]
    assert [h["recommendation_id"] for h in history] == [rec_id]
    assert client.post(f"/api/recommendations/{rec_id}/approve").status_code == 409
    switch_logs = [l for l in client.get(f"/api/audit/{uid}").json()["data"]
                   if l["action"] == "switching" and "Approved by user" in l["description"]]
    assert len(switch_logs) == 1


def test_reject_leaves_the_plan_unchanged(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, sub = _underutilized_user(client)
    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]

    res = client.post(f"/api/recommendations/{rec_id}/reject")

    assert res.json()["data"]["status"] == "rejected"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    assert client.post(f"/api/recommendations/{rec_id}/approve").status_code == 409
    descriptions = [l["description"] for l in client.get(f"/api/audit/{uid}").json()["data"]]
    assert f"Recommendation #{rec_id} rejected by user; no change made." in descriptions


def test_approval_needs_execute_consent(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, sub = _underutilized_user(
        client, consents=["usage_analysis", "share_usage_with_provider", "negotiate_on_behalf"])
    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]

    res = client.post(f"/api/recommendations/{rec_id}/approve")
    assert res.status_code == 403
    assert "execute_plan_changes" in res.json()["message"]
    assert client.get(f"/api/recommendations/{rec_id}").json()["data"]["status"] == "proposed"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0

    client.put(f"/api/users/{uid}/consents/execute_plan_changes", json={"granted": True})
    assert approve(client, rec_id)["status"] == "executed"


def test_stale_recommendation_expires_instead_of_applying(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, sub = _underutilized_user(client)
    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]

    with SessionLocal() as db:            # the plan changed after the proposal
        db.get(Subscription, sub).monthly_cost = 649.0
        db.commit()

    res = client.post(f"/api/recommendations/{rec_id}/approve")
    assert res.status_code == 409
    assert client.get(f"/api/recommendations/{rec_id}").json()["data"]["status"] == "expired"
    assert get_subscription(client, sub)["monthly_cost"] == 649.0


def test_rollback_restores_previous_terms_once(client, monkeypatch):
    _forced_deal(monkeypatch)
    uid, sub = _underutilized_user(client)
    assert client.post(f"/api/subscriptions/{sub}/rollback").status_code == 409   # nothing yet

    rec_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["recommendation"]["recommendation_id"]
    approve(client, rec_id)
    res = client.post(f"/api/subscriptions/{sub}/rollback")

    assert res.status_code == 200
    assert res.json()["data"]["restored_cost"] == 599.0
    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    history = client.get(f"/api/subscriptions/{sub}/history").json()["data"]
    assert [h["change_type"] for h in history] == ["price_change", "rollback"]
    assert client.post(f"/api/subscriptions/{sub}/rollback").status_code == 409


# ── Privacy Mediator gate ────────────────────────────────────
def test_missing_analysis_consent_skips_the_cycle_and_is_audited(client):
    uid, sub = _underutilized_user(client, consents=["share_usage_with_provider"])

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    steps, _ = _steps(client, data["run_id"])

    assert data["analysis"] is None and data["negotiation"] is None
    assert data["skipped"]["analysis"] == "consent 'usage_analysis' not granted"
    assert steps["usage_analyzer"]["status"] == "skipped"
    assert steps["privacy_mediator"]["status"] == "completed"   # independent step still ran
    assert data["final_status"] == "completed" and data["errors"] is None
    denied = [l for l in client.get(f"/api/audit/{uid}").json()["data"] if l["action"] == "access_denied"]
    assert [l["module"] for l in denied] == ["privacy_mediator"]
    assert "usage_analyzer" in denied[0]["description"]
    assert client.post(f"/api/analyze/{uid}").status_code == 403


def test_missing_sharing_consent_blocks_negotiation(client):
    uid, sub = _underutilized_user(client, consents=["usage_analysis", "negotiate_on_behalf"])

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert data["analysis"] is not None
    assert data["skipped"]["sanitization"] == "consent 'share_usage_with_provider' not granted"
    assert data["skipped"]["negotiation"] == "privacy-protected usage data is not available"
    assert client.post(f"/api/negotiate/{uid}").status_code == 403
    assert client.get(f"/api/negotiation/{sub}/history").json()["data"] == []


# ── Failure containment ──────────────────────────────────────
def test_failing_agent_is_recorded_and_dependents_skipped(client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("provider simulator unavailable")

    monkeypatch.setattr(pipeline, "run_negotiation", broken)
    uid, sub = _underutilized_user(client)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    steps, run = _steps(client, data["run_id"])

    assert data["final_status"] == run["status"] == "partial"
    assert data["errors"] == ["Negotiation failed: provider simulator unavailable"]
    assert steps["negotiation_agent"]["status"] == "failed"
    assert "provider simulator unavailable" in steps["negotiation_agent"]["error"]
    assert steps["plan_switching"]["status"] == "skipped"
    assert steps["usage_analyzer"]["status"] == "completed"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0


def test_unknown_user_records_no_run(client):
    assert client.post("/api/run-cycle/9999").status_code == 404
    assert client.get("/api/runs/1").status_code == 404
