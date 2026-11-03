"""Orchestrator, Privacy Mediator gate, and the approval workflow."""

import app.orchestrator.pipeline as pipeline
from app import config
from app.database import SessionLocal
from app.models import Subscription
from tests.conftest import (
    add_usage, agree_price, approve, get_subscription, make_subscription, make_user,
)


def _underutilized_user(client, consents=True):
    uid = make_user(client, consents=consents)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)
    return uid, sub


def _user_with_proposal(client, consents=True):
    """An underutilized user whose reported deal (10% off) is proposed in a run."""
    uid, sub = _underutilized_user(client, consents)
    agree_price(client, uid)
    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    return uid, sub, data["recommendation"]["recommendation_id"], data


def _steps(client, run_id):
    run = client.get(f"/api/runs/{run_id}").json()["data"]
    return {s["agent"]: s for s in run["steps"]}, run


# ── Run recording ────────────────────────────────────────────
def test_run_records_every_agent_step_and_tags_audit_entries(client):
    uid, _ = _underutilized_user(client)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    steps, run = _steps(client, data["run_id"])

    # A brief is for the user to act on; nothing awaits approval until a deal is reported
    assert run["status"] == data["final_status"] == "completed"
    assert list(steps) == ["usage_analyzer", "privacy_mediator", "negotiation_agent", "plan_switching"]
    assert all(s["status"] == "completed" and s["duration_ms"] is not None for s in steps.values())
    summary = steps["negotiation_agent"]["summary"]
    assert summary["brief_id"] == data["negotiation"]["id"] and summary["walk_away_price"] == 569.05

    logs = [l for l in client.get(f"/api/audit/{uid}").json()["data"] if l["action"] != "sign_in"]
    assert logs and all(l["run_id"] == data["run_id"] for l in logs)
    assert client.get(f"/api/runs/user/{uid}").json()["data"][0]["id"] == data["run_id"]


def test_audit_entries_store_the_run_id(client):
    from app.models import AuditLog

    uid, _ = _underutilized_user(client)
    run_id = client.post(f"/api/run-cycle/{uid}").json()["data"]["run_id"]

    with SessionLocal() as db:
        run_ids = {a.run_id for a in db.query(AuditLog)
                   .filter(AuditLog.user_id == uid, AuditLog.action != "sign_in")}
    assert run_ids == {run_id}


# ── Approval workflow ────────────────────────────────────────
def test_run_proposes_but_never_applies_a_change(client):
    uid, sub, rec_id, data = _user_with_proposal(client)

    assert data["final_status"] == "awaiting_approval"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    rec = client.get(f"/api/recommendations/{rec_id}").json()["data"]
    assert rec["status"] == "proposed" and rec["requires_approval"] is True
    assert rec["run_id"] == data["run_id"]
    assert rec["proposed_cost"] == 539.1 and rec["estimated_monthly_savings"] == 59.9
    assert rec["evidence"]["user_reported_price"] is True
    assert "you reported" in rec["rationale"]
    proposed = client.get(f"/api/recommendations/user/{uid}?status=proposed").json()["data"]
    assert [r["id"] for r in proposed] == [rec_id]


def test_price_does_not_ratchet_down_across_runs(client):
    uid, sub, rec_id, first = _user_with_proposal(client)

    second = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    assert second["negotiation"] is None
    pending = f"recommendation #{rec_id} is awaiting your decision"
    assert second["skipped"] == {"negotiation": pending, "switching": pending}
    assert len(client.get(f"/api/negotiation/briefs/user/{uid}").json()["data"]) == 1


def test_approve_applies_change_and_records_history(client):
    uid, sub, rec_id, _ = _user_with_proposal(client)

    result = approve(client, rec_id)

    assert result["status"] == "executed"
    assert get_subscription(client, sub)["monthly_cost"] == 539.1
    history = client.get(f"/api/subscriptions/{sub}/history").json()["data"]
    assert [h["recommendation_id"] for h in history] == [rec_id]
    assert client.post(f"/api/recommendations/{rec_id}/approve").status_code == 409
    switch_logs = [l for l in client.get(f"/api/audit/{uid}").json()["data"]
                   if l["action"] == "switching" and "Approved by user" in l["description"]]
    assert len(switch_logs) == 1


def test_reject_leaves_the_plan_unchanged(client):
    uid, sub, rec_id, _ = _user_with_proposal(client)

    res = client.post(f"/api/recommendations/{rec_id}/reject")

    assert res.json()["data"]["status"] == "rejected"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0
    assert client.post(f"/api/recommendations/{rec_id}/approve").status_code == 409
    descriptions = [l["description"] for l in client.get(f"/api/audit/{uid}").json()["data"]]
    assert f"Recommendation #{rec_id} rejected by user; no change made." in descriptions


def test_approval_needs_execute_consent(client):
    uid, sub, rec_id, _ = _user_with_proposal(
        client, consents=["usage_analysis", "share_usage_with_provider", "negotiate_on_behalf"])

    res = client.post(f"/api/recommendations/{rec_id}/approve")
    assert res.status_code == 403
    assert "execute_plan_changes" in res.json()["message"]
    assert client.get(f"/api/recommendations/{rec_id}").json()["data"]["status"] == "proposed"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0

    client.put(f"/api/users/{uid}/consents/execute_plan_changes", json={"granted": True})
    assert approve(client, rec_id)["status"] == "executed"


def test_stale_recommendation_expires_instead_of_applying(client):
    uid, sub, rec_id, _ = _user_with_proposal(client)

    with SessionLocal() as db:            # the plan changed after the proposal
        db.get(Subscription, sub).monthly_cost = 649.0
        db.commit()

    res = client.post(f"/api/recommendations/{rec_id}/approve")
    assert res.status_code == 409
    assert client.get(f"/api/recommendations/{rec_id}").json()["data"]["status"] == "expired"
    assert get_subscription(client, sub)["monthly_cost"] == 649.0


def test_rollback_restores_previous_terms_once(client):
    uid, sub = _underutilized_user(client)
    assert client.post(f"/api/subscriptions/{sub}/rollback").status_code == 409   # nothing yet

    agree_price(client, uid)
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


def test_missing_negotiate_consent_blocks_the_brief(client):
    uid, sub = _underutilized_user(client, consents=["usage_analysis", "share_usage_with_provider"])

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert data["analysis"] is not None
    assert data["skipped"]["negotiation"] == "consent 'negotiate_on_behalf' not granted"
    assert client.post(f"/api/negotiate/{uid}").status_code == 403
    assert client.get(f"/api/negotiation/briefs/user/{uid}").json()["data"] == []


def test_usage_figures_reach_the_draft_only_with_sharing_consent(client, monkeypatch):
    uid, sub = _underutilized_user(client, consents=["usage_analysis", "negotiate_on_behalf"])

    without = client.post(f"/api/negotiate/{uid}")

    assert without.status_code == 201
    brief = without.json()["data"]
    assert brief["included_usage"] is False
    assert " GB" not in brief["draft_message"] and "minutes of" not in brief["draft_message"]
    client.post(f"/api/negotiation/briefs/{brief['id']}/outcome", json={"outcome": "dismissed"})

    client.put(f"/api/users/{uid}/consents/share_usage_with_provider", json={"granted": True})
    monkeypatch.setattr(config, "REJECTION_COOLDOWN_DAYS", 0)   # the dismissed brief would otherwise wait
    with_usage = client.post(f"/api/negotiate/{uid}").json()["data"]
    assert with_usage["included_usage"] is True
    assert "about 20 GB of my 100 GB" in with_usage["draft_message"]


# ── Failure containment ──────────────────────────────────────
def test_failing_agent_is_recorded_and_independent_steps_still_run(client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("brief store unavailable")

    monkeypatch.setattr(pipeline, "prepare_brief", broken)
    uid, sub = _underutilized_user(client)

    data = client.post(f"/api/run-cycle/{uid}").json()["data"]
    steps, run = _steps(client, data["run_id"])

    assert data["final_status"] == run["status"] == "partial"
    assert data["errors"] == ["Negotiation Brief failed: brief store unavailable"]
    assert steps["negotiation_agent"]["status"] == "failed"
    assert "brief store unavailable" in steps["negotiation_agent"]["error"]
    # Catalog alternatives don't depend on the brief, so switching still runs
    assert steps["plan_switching"]["status"] == "completed"
    assert data["switching"]["proposed"] is False
    assert steps["usage_analyzer"]["status"] == "completed"
    assert get_subscription(client, sub)["monthly_cost"] == 599.0


def test_unknown_user_records_no_run(client):
    make_user(client)
    assert client.post("/api/run-cycle/9999").status_code == 404
    assert client.get("/api/runs/1").status_code == 404
