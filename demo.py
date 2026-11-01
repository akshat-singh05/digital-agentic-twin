"""
Agentic Digital Twin — Live Demo Script

Runs the complete intelligent pipeline for a user and prints every
stage with clean, structured output.  No ANSI terminal color codes.

Usage:
    # Seed and start the server first:
    #   python seed_data.py --reset
    #   uvicorn app.main:app --reload
    python demo.py              # demo user 2 (has a plan-switch opportunity)
    python demo.py 3            # demo user 3
    python demo.py --seed       # seed fresh data, then demo user 2

Logs in as the seeded demo user with DEMO_PASSWORD (default
"demo-pass-2026"), so it only works against the seeded demo data.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import textwrap
from datetime import datetime

BASE_URL = "http://127.0.0.1:8000/api"
_TOKEN = None


# ═════════════════════════════════════════════════════════════
#  HTTP helpers
# ═════════════════════════════════════════════════════════════
def _request(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if _TOKEN:
        req.add_header("Authorization", f"Bearer {_TOKEN}")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _post(path, body=None):
    """POST to the API and return parsed JSON."""
    return _request(path, "POST", body)


def _get(path):
    """GET from the API and return parsed JSON."""
    return _request(path)


# ═════════════════════════════════════════════════════════════
#  Clean structured printers (no ANSI codes)
# ═════════════════════════════════════════════════════════════
def banner(text):
    """Print a section banner."""
    width = 62
    print(f"\n{'=' * width}")
    print(f"  {text.center(width - 4)}")
    print(f"{'=' * width}")


def section(icon, title):
    """Print a subsection header."""
    print(f"\n  {icon}  {title}")
    print(f"  {'─' * 56}")


def kv(key, value, indent=6):
    """Print a key-value pair with aligned formatting."""
    pad = " " * indent
    print(f"{pad}{key:<28s} {value}")


def status_label(status):
    """Return a clean text label for a pipeline status."""
    labels = {
        "completed": "[COMPLETED]",
        "partial":   "[PARTIAL]",
        "failed":    "[FAILED]",
        "accepted":  "[ACCEPTED]",
        "rejected":  "[REJECTED]",
        "final":     "[FINAL]",
    }
    return labels.get(status, f"[{status.upper()}]")


def print_analysis(data):
    """Print analysis results as structured output."""
    section("MODULE 1", "USAGE ANALYSIS")
    kv("Provider", data.get("provider", "—"))
    kv("Plan", data.get("plan_name", "—"))
    kv("Monthly Cost", f"Rs.{data.get('monthly_cost', 0)}")
    eff = data.get("efficiency")
    kv("Utilisation", f"{eff * 100:.1f}% ({data.get('binding_metric')})" if eff is not None else "not measurable")
    kv("Category", data.get("usage_category", "—"))
    kv("Recommendation", data.get("recommendation", "—").upper())
    kv("Unused Spend", f"Rs.{data.get('unused_spend', 0)}/month (not a saving)")
    kv("Confidence Score", f"{(data.get('confidence_score') or 0) * 100:.0f}%")
    kv("Data (avg / peak)", f"{data.get('avg_data_usage', 0)} / {data.get('peak_data_usage', 0)} GB, trend {data.get('data_trend', '—')}")
    kv("Calls (avg / peak)", f"{data.get('avg_call_usage', 0)} / {data.get('peak_call_usage', 0)} min")
    kv("Overage Billed", f"Rs.{data.get('avg_overage', 0)}/month")
    msg = data.get("message", "")
    if msg:
        print(f"\n      Note: {msg}")


def print_negotiation(data):
    """Print negotiation results with round-by-round breakdown."""
    section("MODULE 3", "AUTONOMOUS NEGOTIATION")
    kv("Provider", data.get("provider", "—"))
    kv("Original Cost", f"Rs.{data.get('original_cost', 0)}")
    kv("Final Price", f"Rs.{data.get('final_price', 0)}")
    kv("Savings", f"{data.get('savings_pct', 0)}%")
    kv("Walk-away Price", f"Rs.{data.get('reservation_price', '—')}")
    kv("Total Rounds", str(data.get("total_rounds", 0)))
    kv("Outcome", status_label(data.get("status", "unknown")) + " (simulated provider)")

    rounds = data.get("rounds", [])
    if rounds:
        print(f"\n      Round-by-Round Breakdown:")
        print(f"      {'Round':>5s}  {'Agent Offer':>12s}  {'Provider':>12s}  {'Status':>10s}")
        print(f"      {'─'*5}  {'─'*12}  {'─'*12}  {'─'*10}")
        for r in rounds:
            rnd = r.get("round_number", "?")
            offer = f"Rs.{r.get('agent_offer', 0)}"
            counter = f"Rs.{r.get('provider_counter', 0)}"
            st = r.get("status", "?")
            print(f"      {rnd:>5}  {offer:>12s}  {counter:>12s}  {st:>10s}")


def print_switching(data):
    """Print plan switching results."""
    section("MODULE 4", "PLAN SWITCHING")
    proposed = data.get("proposed", False)
    decision = (
        f"[PROPOSED #{data.get('recommendation_id')} — awaiting approval]"
        if proposed else "[NOT PROPOSED]"
    )
    kv("Decision", decision)
    kv("Action", data.get("action") or "—")
    kv("Projected Cost", f"Rs.{data.get('projected_cost', 0)}")
    kv("Monthly Saving", f"Rs.{data.get('estimated_monthly_savings', 0)}")
    kv("Risk Level", data.get("risk_flag", "—").upper())
    explanation = data.get("explanation")
    if explanation:
        print()
        print("      " + textwrap.fill(explanation["what"], width=56, subsequent_indent="      "))
        for item in explanation["benefits"]:
            print("        + " + textwrap.fill(item, width=52, subsequent_indent="          "))
        for item in explanation["drawbacks"]:
            print("        - " + textwrap.fill(item, width=52, subsequent_indent="          "))
    reason = data.get("reason", "") if not explanation else ""
    if reason:
        wrapped = textwrap.fill(reason, width=52, initial_indent="", subsequent_indent="        ")
        print(f"\n      Reason: {wrapped}")


def print_audit_logs(user_id):
    """Fetch and print the audit trail."""
    section("MODULE 5", "EXPLAINABLE AUDIT TRAIL")
    try:
        res = _get(f"/audit/{user_id}")
        logs = res.get("data", [])
        if not logs:
            print("      No audit logs recorded yet.")
            return

        for i, log in enumerate(logs[:10]):
            action = log.get("action", "?")
            desc = log.get("description", "")
            ts = log.get("created_at", "")
            if ts:
                ts = ts[:19].replace("T", " ")

            icon_map = {
                "analysis": "ANALYSIS", "negotiation": "NEGOTIATION",
                "switching": "SWITCHING", "switch": "SWITCHING",
                "switch_rejected": "SWITCH_REJECTED",
            }
            label = icon_map.get(action, action.upper())

            print(f"      [{label:^18s}]  {ts}")
            wrapped = textwrap.fill(desc, width=52, initial_indent="        ", subsequent_indent="        ")
            print(wrapped)
            if i < len(logs[:10]) - 1:
                print()
    except Exception as e:
        print(f"      Failed to fetch audit logs: {e}")


# ═════════════════════════════════════════════════════════════
#  Main demo
# ═════════════════════════════════════════════════════════════
def main():
    global _TOKEN

    # Parse args
    user_id = 2
    do_seed = False

    for arg in sys.argv[1:]:
        if arg == "--seed":
            do_seed = True
        elif arg.isdigit():
            user_id = int(arg)

    # ── Optional: seed fresh data ────────────────────────────
    if do_seed:
        print("Seeding database with demo data...")
        from app.utils.seed_data import seed
        seed(reset=True)
        print()

    # ── Header ───────────────────────────────────────────────
    banner("AGENTIC DIGITAL TWIN — LIVE DEMO")
    print(f"\n  Timestamp : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  User ID   : {user_id}")
    print(f"  Server    : {BASE_URL}")

    # ── Verify server is running ─────────────────────────────
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/")
    except Exception:
        print(f"\n  [ERROR] Server not reachable!")
        print(f"  Start the server first:  uvicorn app.main:app --reload")
        sys.exit(1)

    # ── Log in as the seeded demo user ───────────────────────
    from app.utils.seed_data import USERS
    if not 1 <= user_id <= len(USERS):
        print(f"\n  [ERROR] Demo users are 1–{len(USERS)}.")
        sys.exit(1)
    email = USERS[user_id - 1]["email"]
    try:
        login = _post("/auth/login", {
            "email": email, "password": os.getenv("DEMO_PASSWORD", "demo-pass-2026"),
        })
        _TOKEN = login["data"]["access_token"]
        user = login["data"]["user"]
        user_id = user["id"]
        print(f"\n  [OK] Logged in as {user['name']} ({user['email']})")
    except urllib.error.HTTPError:
        print(f"\n  [ERROR] Could not log in as {email}.")
        print(f"  Run: python seed_data.py --reset")
        sys.exit(1)

    # ── Run full pipeline ────────────────────────────────────
    banner("EXECUTING FULL PIPELINE")
    print(f"\n  Analyze -> Sanitize -> Negotiate -> Switch -> Audit")
    print(f"  Running...", end="", flush=True)

    try:
        result = _post(f"/run-cycle/{user_id}")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.fp else {}
        print(f"\n\n  [ERROR] Pipeline failed: {body}")
        sys.exit(1)

    data = result["data"]
    final = data.get("final_status", "unknown")
    print(f" {status_label(final)}")

    # ── Print each stage ─────────────────────────────────────
    if data.get("analysis"):
        print_analysis(data["analysis"])

    if data.get("negotiation"):
        print_negotiation(data["negotiation"])

    if data.get("switching"):
        print_switching(data["switching"])

    # ── Audit trail (fetched separately for completeness) ────
    print_audit_logs(user_id)

    # ── Errors (if any) ──────────────────────────────────────
    errors = data.get("errors")
    if errors:
        section("WARNING", "PIPELINE WARNINGS")
        for err in errors:
            print(f"      * {err}")

    # ── Final summary ────────────────────────────────────────
    banner("DEMO SUMMARY")
    kv("Pipeline Status", status_label(final), indent=4)
    kv("Run ID", str(data.get("run_id", "—")), indent=4)
    kv("Audit Entries Logged", str(len(data.get("audit_logged", []))), indent=4)
    if data.get("analysis"):
        kv("Recommendation", data["analysis"].get("recommendation", "—").upper(), indent=4)
    if data.get("negotiation"):
        kv("Negotiated Savings", f"{data['negotiation'].get('savings_pct', 0)}%", indent=4)
    if data.get("switching"):
        kv("Change Proposed", "Yes — approve or reject it" if data["switching"].get("proposed") else "No", indent=4)

    print(f"\n  {'─' * 56}")
    print(f"  Demo complete — Full automation demonstrated!")
    print(f"  Swagger UI: http://127.0.0.1:8000/docs\n")


if __name__ == "__main__":
    main()
