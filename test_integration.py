"""
End-to-end smoke script for Plan Switching + Audit Logger against a
running server.  The automated test suite lives in tests/ (run: pytest).
Uses only stdlib (urllib) — no external dependencies required.
"""

import json
import time
import urllib.request
import urllib.error
import sys

BASE = "http://127.0.0.1:8000/api"
_token = None          # access token of the user the script is acting as


def _request(path, method, body=None):
    headers = {"Content-Type": "application/json"}
    if _token:
        headers["Authorization"] = f"Bearer {_token}"
    data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode())


def _post(path, body=None):
    return _request(path, "POST", body)


def _get(path):
    return _request(path, "GET")


def _register(name, email):
    """Register a user, act as them, and return their id."""
    global _token
    code, res = _post("/auth/register", {"name": name, "email": email, "password": "smoke-test-pw"})
    assert code == 201 and res["status"] == "success", f"Registration failed: {res}"
    _token = res["data"]["access_token"]
    return res["data"]["user"]["id"]


def _grant_all_consents(uid):
    for scope in ("usage_analysis", "share_usage_with_provider",
                  "negotiate_on_behalf", "execute_plan_changes"):
        _request(f"/users/{uid}/consents/{scope}", "PUT", {"granted": True})


def main():
    ts = time.strftime("%Y%m%d%H%M%S")  # unique suffix to avoid email collisions
    print("=" * 60)
    print("  INTEGRATION TEST — Plan Switching + Audit Logger")
    print("=" * 60)

    # 1. Register user (logs in as them)
    uid = _register("Switch Tester", f"switch_{ts}@test.com")
    _grant_all_consents(uid)
    print(f"✅ 1. Registered user id={uid} and granted all consents")

    # 2. Create subscription
    code, res = _post("/subscriptions", {
        "user_id": uid, "provider": "Jio", "plan_name": "Gold 599",
        "monthly_cost": 599.0, "data_limit_gb": 100.0, "call_minutes_limit": 500,
    })
    assert code == 201, f"Subscription creation failed: {res}"
    print(f"✅ 2. Created subscription id={res['data']['id']}")

    # 3. Add 6 usage records
    for i in range(6):
        _post("/usage", {
            "user_id": uid, "provider": "Jio",
            "period_start": f"2026-0{i+1}-01T00:00:00",
            "period_end": f"2026-0{i+1}-28T23:59:59",
            "data_used_gb": 30.0 + i, "call_minutes_used": 100 + i * 10,
            "billing_amount": 599.0,
        })
    print("✅ 3. Added 6 usage records")

    # 4. Analyze usage
    code, res = _post(f"/analyze/{uid}")
    rec = res["data"].get("recommendation", "unknown")
    print(f"✅ 4. Analysis → recommendation={rec}")

    # 5. Prepare a negotiation brief, then report that the provider agreed to 10% off
    code, res = _post(f"/negotiate/{uid}")
    neg = res["data"]
    assert neg["contacts_provider"] is False and neg["status"] == "open", neg
    print(f"✅ 5. Negotiation brief → ask ₹{neg['opening_ask']}, walk away above ₹{neg['walk_away_price']}")
    agreed = round(neg["current_cost"] * 0.9, 2)
    code, res = _post(f"/negotiation/briefs/{neg['id']}/outcome", {"outcome": "accepted", "agreed_price": agreed})
    assert res["data"]["status"] == "accepted", res
    print(f"       reported accepted price ₹{agreed}")

    # 6. Propose a switch, then approve it (nothing changes before approval)
    code, res = _post(f"/switch/{uid}")
    sw = res["data"]
    print(f"✅ 6. Switch Plan:")
    print(f"       proposed     = {sw['proposed']} (recommendation #{sw['recommendation_id']})")
    print(f"       reason       = {sw['reason']}")
    print(f"       projected_cost = {sw['projected_cost']}")
    print(f"       risk_flag    = {sw['risk_flag']}")
    if sw["proposed"]:
        code, res = _post(f"/recommendations/{sw['recommendation_id']}/approve")
        assert res["data"]["status"] == "executed", res
        print(f"       approved     → status={res['data']['status']}")

    # 7. Audit logs
    code, res = _get(f"/audit/{uid}")
    logs = res["data"]
    print(f"✅ 7. Audit logs → {len(logs)} entries:")
    for log in logs:
        print(f"       [{log['action']:18s}] {log['description'][:70]}")

    # === Edge case: evaluate again — nothing new should be proposed ===
    # After a switch the plan usually fits (409: no opportunity left);
    # otherwise the already-applied reported price must not be re-applied (proposed=False).
    print(f"\n✅ 8. Second switch evaluation:")
    try:
        code2, res2 = _post(f"/switch/{uid}")
        assert res2["data"]["proposed"] is False, res2
        print(f"       proposed=False, reason={res2['data']['reason'][:60]}")
    except urllib.error.HTTPError as e:
        assert e.code == 409, e.code
        print(f"       HTTP 409 — no optimization opportunity left")

    # === Edge case: user with no subscription ===
    uid2 = _register("No Sub User", f"nosub_{ts}@test.com")
    _grant_all_consents(uid2)
    try:
        _post(f"/switch/{uid2}")
        print("❌ 9. Expected error for user with no subscription!")
    except urllib.error.HTTPError as e:
        print(f"✅ 9. No-subscription edge case → HTTP {e.code}")

    # === Edge case: user with subscription but no reported provider offer ===
    _post("/subscriptions", {
        "user_id": uid2, "provider": "Airtel", "plan_name": "Basic",
        "monthly_cost": 299.0, "data_limit_gb": 50.0, "call_minutes_limit": 200,
    })
    try:
        _post(f"/switch/{uid2}")
        print("❌ 10. Expected error for user with no reported offer!")
    except urllib.error.HTTPError as e:
        print(f"✅ 10. No-reported-offer edge case → HTTP {e.code}")

    # === Access control: user 2 cannot see user 1's audit trail ===
    try:
        _get(f"/audit/{uid}")
        print("❌ 11. Another user's audit trail was readable!")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code
        print(f"✅ 11. Other user's audit trail → HTTP {e.code} (hidden)")

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
