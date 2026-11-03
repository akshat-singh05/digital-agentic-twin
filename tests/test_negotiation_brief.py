"""Phase J: negotiation briefs (the twin contacts nobody) and the user's reported outcome."""

import json

import pytest

from app import config
from app.modules.audit_logger.formatter import format_audit_message
from app.modules.negotiation_agent.brief import build_brief, opening_ask, walk_away_price
from tests.conftest import add_usage, agree_price, approve, make_subscription, make_user

SILVER = {"provider": "Jio", "plan_name": "Silver", "monthly_cost": 399.0,
          "data_limit_gb": 50.0, "call_minutes_limit": 300, "monthly_savings": 200.0}
PLAN = {"provider": "Jio", "plan_name": "Gold", "monthly_cost": 599.0,
        "data_limit_gb": 100.0, "call_minutes_limit": 500}
ANALYSIS = {"efficiency": 0.2, "binding_metric": "calls", "avg_data_usage": 22.3,
            "avg_call_usage": 104, "periods": 6, "unused_spend": 480.0, "avg_overage": 0}


def _brief(alternatives=(), share=True, **analysis):
    return build_brief(plan=PLAN, analysis={**ANALYSIS, **analysis}, alternatives=list(alternatives),
                       min_savings_pct=5.0, share_usage=share)


# ── Pure logic ───────────────────────────────────────────────
def test_walk_away_price_is_the_lower_of_five_percent_off_and_the_best_alternative():
    assert walk_away_price(599, 5.0) == 569.05
    assert walk_away_price(599, 5.0, 399) == 399.0           # a cheaper competitor sets the limit
    assert walk_away_price(599, 5.0, 580) == 569.05          # a dearer one does not


def test_opening_ask_matches_the_competitor_or_asks_for_ten_percent():
    assert opening_ask(599, 399.0, has_alternative=True) == 399.0
    assert opening_ask(599, 569.05, has_alternative=False) == 539.1


def test_brief_cites_the_competitor_and_the_numbers_match_the_draft():
    brief = _brief([SILVER])

    assert (brief["opening_ask"], brief["walk_away_price"]) == (399.0, 399.0)
    assert brief["effective_cost"] == 599.0
    assert any("Jio Silver" in point and "₹399" in point for point in brief["leverage"])
    assert "₹399/month" in brief["draft_message"] and "Jio Silver" in brief["draft_message"]
    assert "switching to Jio Silver would save ₹200" in brief["fallback"]
    assert brief["draft_message"].rstrip().endswith("[Your name]")


def test_draft_quotes_only_rounded_usage_and_only_with_consent():
    shared = _brief(share=True)
    private = _brief(share=False)

    assert shared["included_usage"] is True
    assert "about 20 GB of my 100 GB" in shared["draft_message"]      # 22.3 GB is not quoted exactly
    assert "22.3" not in shared["draft_message"] and "about 100 minutes" in shared["draft_message"]
    assert private["included_usage"] is False
    assert "GB" not in private["draft_message"] and "minutes of" not in private["draft_message"]
    assert "not using all of my plan's capacity" in private["draft_message"]


def test_overage_is_reported_and_added_to_the_effective_cost():
    brief = _brief(avg_overage=120.0)

    assert brief["effective_cost"] == 719.0
    assert any("₹120" in c and "plan price only" in c for c in brief["caveats"])


def test_every_brief_says_the_twin_contacts_nobody():
    assert any("has not contacted anyone" in c for c in _brief()["caveats"])


# ── API: preparing a brief ───────────────────────────────────
def _underutilized(client, **kwargs):
    uid = make_user(client, **kwargs)
    sub = make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=20.0)
    return uid, sub


def test_prepare_a_brief_stores_it_and_audits_it(client):
    uid, sub = _underutilized(client)

    res = client.post(f"/api/negotiate/{uid}")

    assert res.status_code == 201
    brief = res.json()["data"]
    assert brief["status"] == "open" and brief["subscription_id"] == sub
    assert brief["contacts_provider"] is False
    assert (brief["opening_ask"], brief["walk_away_price"]) == (539.1, 569.05)
    assert client.get(f"/api/negotiation/briefs/{brief['id']}").json()["data"]["draft_message"] == brief["draft_message"]
    assert [b["id"] for b in client.get(f"/api/negotiation/briefs/user/{uid}?status=open").json()["data"]] == [brief["id"]]
    audit = [l for l in client.get(f"/api/audit/{uid}").json()["data"] if l["action"] == "negotiation"]
    assert len(audit) == 1 and "Nothing was sent" in audit[0]["description"]
    assert json.loads(audit[0]["details"])["included_usage"] is True


def test_no_brief_without_a_case_to_make(client):
    uid = make_user(client)
    make_subscription(client, uid)
    add_usage(client, uid, data_used_gb=60.0)              # well used: nothing to negotiate

    res = client.post(f"/api/negotiate/{uid}")

    assert res.status_code == 409 and "capacity you are not using" in res.json()["message"]


def test_a_second_brief_waits_for_the_first_answer(client):
    uid, sub = _underutilized(client)
    first = client.post(f"/api/negotiate/{uid}").json()["data"]

    second = client.post(f"/api/negotiate/{uid}")

    assert second.status_code == 409
    assert f"brief #{first['id']} is waiting for the provider's answer" in second.json()["message"]


# ── API: reporting the outcome ───────────────────────────────
def _outcome(client, brief_id, **body):
    return client.post(f"/api/negotiation/briefs/{brief_id}/outcome", json=body)


def test_reporting_an_accepted_price(client):
    uid, sub = _underutilized(client)
    brief = client.post(f"/api/negotiate/{uid}").json()["data"]

    res = _outcome(client, brief["id"], outcome="accepted", agreed_price=520.0, note="chat, agent Ravi")

    done = res.json()["data"]
    assert res.status_code == 200 and done["status"] == "accepted" and done["agreed_price"] == 520.0
    assert done["outcome_note"] == "chat, agent Ravi" and done["reported_at"]
    descriptions = [l["description"] for l in client.get(f"/api/audit/{uid}").json()["data"]]
    assert "You reported that Jio accepted ₹520.0/month on Gold 599" in " ".join(descriptions)


@pytest.mark.parametrize("body, fragment", [
    ({"outcome": "accepted"}, "needs the agreed monthly price"),
    ({"outcome": "accepted", "agreed_price": 599.0}, "lower than the current price"),
    ({"outcome": "accepted", "agreed_price": 700.0}, "lower than the current price"),
    ({"outcome": "declined", "agreed_price": 500.0}, "only be reported with an accepted"),
])
def test_invalid_outcomes_are_rejected(client, body, fragment):
    uid, _ = _underutilized(client)
    brief = client.post(f"/api/negotiate/{uid}").json()["data"]

    res = _outcome(client, brief["id"], **body)

    assert res.status_code == 422 and fragment in res.json()["message"]
    assert client.get(f"/api/negotiation/briefs/{brief['id']}").json()["data"]["status"] == "open"


def test_unknown_outcome_and_bad_price_fail_validation(client):
    uid, _ = _underutilized(client)
    brief = client.post(f"/api/negotiate/{uid}").json()["data"]

    assert _outcome(client, brief["id"], outcome="maybe").status_code == 422
    assert _outcome(client, brief["id"], outcome="accepted", agreed_price=-5).status_code == 422


def test_an_outcome_can_only_be_reported_once(client):
    uid, _ = _underutilized(client)
    brief = client.post(f"/api/negotiate/{uid}").json()["data"]
    _outcome(client, brief["id"], outcome="declined")

    again = _outcome(client, brief["id"], outcome="accepted", agreed_price=500.0)

    assert again.status_code == 409 and "already declined" in again.json()["message"]


# ── The loop: brief → reported price → proposal → approval ───
def test_reported_price_becomes_a_proposal_the_user_approves(client):
    uid, sub = _underutilized(client)
    agree_price(client, uid, factor=0.85)

    run = client.post(f"/api/run-cycle/{uid}").json()["data"]
    rec = run["recommendation"]

    assert run["skipped"]["negotiation"].startswith("you reported an accepted price on brief #")
    assert rec["action"] == "apply_negotiated_price" and rec["projected_cost"] == 509.15
    drawbacks = client.get(f"/api/recommendations/{rec['recommendation_id']}").json()["data"]["evidence"]["explanation"]["drawbacks"]
    assert any("reported the provider agreed to" in d for d in drawbacks)
    approve(client, rec["recommendation_id"])
    assert client.get(f"/api/subscriptions/{sub}").json()["data"]["monthly_cost"] == 509.15


def test_a_declined_brief_blocks_a_new_one_for_the_cool_down(client, monkeypatch):
    uid, _ = _underutilized(client)
    brief = client.post(f"/api/negotiate/{uid}").json()["data"]
    _outcome(client, brief["id"], outcome="declined")

    blocked = client.post(f"/api/negotiate/{uid}")
    run = client.post(f"/api/run-cycle/{uid}").json()["data"]

    assert blocked.status_code == 409 and f"brief #{brief['id']} was declined" in blocked.json()["message"]
    assert "was declined on" in run["skipped"]["negotiation"] and run["negotiation"] is None
    monkeypatch.setattr(config, "REJECTION_COOLDOWN_DAYS", 0)
    assert client.post(f"/api/negotiate/{uid}").status_code == 201


def test_a_dismissed_brief_does_not_count_as_a_deal(client):
    uid, _ = _underutilized(client)
    brief = client.post(f"/api/negotiate/{uid}").json()["data"]
    _outcome(client, brief["id"], outcome="dismissed")

    sw = client.post(f"/api/switch/{uid}").json()["data"]

    assert sw["proposed"] is False and "No provider offer reported" in sw["reason"]


# ── Audit wording ────────────────────────────────────────────
def test_audit_messages_say_nothing_was_sent():
    brief = format_audit_message("negotiation", {
        "kind": "brief", "brief_id": 3, "provider": "Jio", "plan_name": "Gold",
        "opening_ask": 399.0, "walk_away_price": 399.0, "included_usage": False})
    assert "Negotiation brief #3 prepared for Jio Gold" in brief
    assert "without usage figures" in brief and "Nothing was sent" in brief

    assert "declined your request" in format_audit_message("negotiation", {
        "kind": "outcome", "brief_id": 3, "outcome": "declined", "provider": "Jio", "plan_name": "Gold"})
    assert "did not pursue" in format_audit_message("negotiation", {
        "kind": "outcome", "brief_id": 3, "outcome": "dismissed", "provider": "Jio", "plan_name": "Gold"})
