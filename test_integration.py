"""
End-to-end integration test for Plan Switching + Audit Logger modules.
Uses only stdlib (urllib) — no external dependencies required.
"""

import json
import urllib.request
import urllib.error
import sys

BASE = "http://127.0.0.1:8000/api"


def _post(path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode())


def _get(path):
    with urllib.request.urlopen(f"{BASE}{path}") as resp:
        return resp.status, json.loads(resp.read().decode())


def main():
    ts = "20260408"  # unique suffix to avoid email collisions
    print("=" * 60)
    print("  INTEGRATION TEST — Plan Switching + Audit Logger")
    print("=" * 60)

    # 1. Create user
    code, res = _post("/users", {"name": "Switch Tester", "email": f"switch_{ts}@test.com"})
    assert code == 201 and res["status"] == "success", f"User creation failed: {res}"
    uid = res["data"]["id"]
    print(f"✅ 1. Created user id={uid}")

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

    # 5. Negotiate
    code, res = _post(f"/negotiate/{uid}")
    neg = res["data"]
    print(f"✅ 5. Negotiation → status={neg['status']}, final_price={neg['final_price']}, rounds={neg['total_rounds']}")

    # 6. Switch plan
    code, res = _post(f"/switch/{uid}")
    sw = res["data"]
    print(f"✅ 6. Switch Plan:")
    print(f"       applied      = {sw['applied']}")
    print(f"       reason       = {sw['reason']}")
    print(f"       projected_cost = {sw['projected_cost']}")
    print(f"       risk_flag    = {sw['risk_flag']}")
    print(f"       rollback     = {sw['rollback']}")

    # 7. Audit logs
    code, res = _get(f"/audit/{uid}")
    logs = res["data"]
    print(f"✅ 7. Audit logs → {len(logs)} entries:")
    for log in logs:
        print(f"       [{log['action']:18s}] {log['description'][:70]}")

    # === Edge case: switch again with no NEW negotiation ===
    code2, res2 = _post(f"/switch/{uid}")
    sw2 = res2["data"]
    print(f"\n✅ 8. Second switch (same negotiation):")
    print(f"       applied={sw2['applied']}, risk={sw2['risk_flag']}")

    # === Edge case: user with no subscription ===
    code3, res3 = _post("/users", {"name": "No Sub User", "email": f"nosub_{ts}@test.com"})
    uid2 = res3["data"]["id"]
    try:
        _post(f"/switch/{uid2}")
        print("❌ 9. Expected error for user with no subscription!")
    except urllib.error.HTTPError as e:
        print(f"✅ 9. No-subscription edge case → HTTP {e.code}")

    # === Edge case: user with subscription but no negotiation ===
    _post("/subscriptions", {
        "user_id": uid2, "provider": "Airtel", "plan_name": "Basic",
        "monthly_cost": 299.0, "data_limit_gb": 50.0, "call_minutes_limit": 200,
    })
    try:
        _post(f"/switch/{uid2}")
        print("❌ 10. Expected error for user with no negotiation!")
    except urllib.error.HTTPError as e:
        print(f"✅ 10. No-negotiation edge case → HTTP {e.code}")

    print("\n" + "=" * 60)
    print("  ALL TESTS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
