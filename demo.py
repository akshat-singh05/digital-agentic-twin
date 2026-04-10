"""
🚀 Agentic Digital Twin — Live Demo Script

Runs the complete intelligent pipeline for a user and prints every
stage with formatted, color-coded output.  Perfect for viva demos.

Usage:
    # Start server first:  uvicorn app.main:app --reload
    # Then run:
    python demo.py              # uses user_id=1
    python demo.py 3            # uses user_id=3
    python demo.py --seed       # seed fresh data, then demo user 1
"""

import json
import sys
import os
import urllib.request
import urllib.error
import textwrap
from datetime import datetime

BASE_URL = "http://127.0.0.1:8000/api"


# ═════════════════════════════════════════════════════════════
#  Terminal colors (works on Windows 10+ and all Unix terminals)
# ═════════════════════════════════════════════════════════════
class C:
    """ANSI color codes for terminal output."""
    BOLD      = "\033[1m"
    DIM       = "\033[2m"
    CYAN      = "\033[96m"
    GREEN     = "\033[92m"
    YELLOW    = "\033[93m"
    RED       = "\033[91m"
    MAGENTA   = "\033[95m"
    BLUE      = "\033[94m"
    WHITE     = "\033[97m"
    RESET     = "\033[0m"

# Enable ANSI on Windows
if os.name == "nt":
    os.system("")


# ═════════════════════════════════════════════════════════════
#  HTTP helpers
# ═════════════════════════════════════════════════════════════
def _post(path):
    """POST to the API and return parsed JSON."""
    req = urllib.request.Request(f"{BASE_URL}{path}", data=b"", method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _get(path):
    """GET from the API and return parsed JSON."""
    with urllib.request.urlopen(f"{BASE_URL}{path}") as resp:
        return json.loads(resp.read().decode())


# ═════════════════════════════════════════════════════════════
#  Pretty printers
# ═════════════════════════════════════════════════════════════
def banner(text, color=C.CYAN):
    width = 62
    print(f"\n{color}{C.BOLD}{'═' * width}")
    print(f"  {text.center(width - 4)}")
    print(f"{'═' * width}{C.RESET}")


def section(icon, title, color=C.YELLOW):
    print(f"\n{color}{C.BOLD}  {icon}  {title}{C.RESET}")
    print(f"  {C.DIM}{'─' * 56}{C.RESET}")


def kv(key, value, indent=6):
    """Print a key-value pair with aligned formatting."""
    pad = " " * indent
    print(f"{pad}{C.DIM}{key:.<28s}{C.RESET} {C.WHITE}{value}{C.RESET}")


def status_badge(status):
    """Return a colored badge for a status string."""
    badges = {
        "completed": f"{C.GREEN}✅ COMPLETED{C.RESET}",
        "partial":   f"{C.YELLOW}⚠️  PARTIAL{C.RESET}",
        "failed":    f"{C.RED}❌ FAILED{C.RESET}",
        "accepted":  f"{C.GREEN}✅ ACCEPTED{C.RESET}",
        "rejected":  f"{C.RED}❌ REJECTED{C.RESET}",
        "final":     f"{C.YELLOW}⏹  FINAL{C.RESET}",
    }
    return badges.get(status, status)


def print_analysis(data):
    """Print analysis results."""
    section("📊", "USAGE ANALYSIS")
    kv("Provider", data.get("provider", "—"))
    kv("Plan", data.get("plan_name", "—"))
    kv("Monthly Cost", f"₹{data.get('monthly_cost', 0)}")
    kv("Efficiency", f"{data.get('efficiency', 0) * 100:.1f}%")
    kv("Category", data.get("usage_category", "—"))
    kv("Recommendation", data.get("recommendation", "—").upper())
    kv("Savings Estimate", f"₹{data.get('savings_estimate', 0)}/month")
    kv("Confidence Score", f"{data.get('confidence_score', 0) * 100:.0f}%")
    kv("Avg Data Usage", f"{data.get('avg_data_usage', 0)} GB")
    kv("Avg Call Usage", f"{data.get('avg_call_usage', 0)} min")
    msg = data.get("message", "")
    if msg:
        print(f"\n      {C.DIM}💬 {msg}{C.RESET}")


def print_negotiation(data):
    """Print negotiation results with round-by-round breakdown."""
    section("🤝", "AUTONOMOUS NEGOTIATION")
    kv("Provider", data.get("provider", "—"))
    kv("Original Cost", f"₹{data.get('original_cost', 0)}")
    kv("Final Price", f"₹{data.get('final_price', 0)}")
    kv("Savings", f"{data.get('savings_pct', 0)}%")
    kv("Total Rounds", str(data.get("total_rounds", 0)))
    kv("Outcome", status_badge(data.get("status", "unknown")))

    rounds = data.get("rounds", [])
    if rounds:
        print(f"\n      {C.BOLD}{C.BLUE}Round-by-Round Breakdown:{C.RESET}")
        print(f"      {'Round':>5s}  {'Agent Offer':>12s}  {'Provider':>12s}  {'Status':>10s}")
        print(f"      {'─'*5}  {'─'*12}  {'─'*12}  {'─'*10}")
        for r in rounds:
            rnd = r.get("round_number", "?")
            offer = f"₹{r.get('agent_offer', 0)}"
            counter = f"₹{r.get('provider_counter', 0)}"
            st = r.get("status", "?")
            color = C.GREEN if st == "accepted" else C.DIM
            print(f"      {color}{rnd:>5}  {offer:>12s}  {counter:>12s}  {st:>10s}{C.RESET}")


def print_switching(data):
    """Print plan switching results."""
    section("🔄", "PLAN SWITCHING")
    applied = data.get("applied", False)
    badge = f"{C.GREEN}✅ APPLIED{C.RESET}" if applied else f"{C.YELLOW}⏹  SKIPPED{C.RESET}"
    kv("Decision", badge)
    kv("Projected Cost", f"₹{data.get('projected_cost', 0)}")
    kv("Risk Level", data.get("risk_flag", "—").upper())
    kv("Rollback Occurred", "Yes" if data.get("rollback") else "No")
    reason = data.get("reason", "")
    if reason:
        wrapped = textwrap.fill(reason, width=52, initial_indent="", subsequent_indent="        ")
        print(f"\n      {C.DIM}💬 {wrapped}{C.RESET}")


def print_audit_logs(user_id):
    """Fetch and print the audit trail."""
    section("📝", "EXPLAINABLE AUDIT TRAIL")
    try:
        res = _get(f"/audit/{user_id}")
        logs = res.get("data", [])
        if not logs:
            print(f"      {C.DIM}No audit logs recorded yet.{C.RESET}")
            return

        # Show most recent first, limit to 10
        for i, log in enumerate(logs[:10]):
            action = log.get("action", "?")
            module = log.get("module", "?")
            desc = log.get("description", "")
            ts = log.get("created_at", "")
            if ts:
                # Trim to readable format
                ts = ts[:19].replace("T", " ")

            icon_map = {
                "analysis": "📊", "negotiation": "🤝",
                "switching": "🔄", "switch": "🔄",
                "switch_rejected": "⏹ ",
            }
            icon = icon_map.get(action, "📎")

            print(f"      {C.CYAN}{icon} [{action:^18s}]{C.RESET}  {C.DIM}{ts}{C.RESET}")
            wrapped = textwrap.fill(desc, width=52, initial_indent="        ", subsequent_indent="        ")
            print(f"{C.WHITE}{wrapped}{C.RESET}")
            if i < len(logs[:10]) - 1:
                print()
    except Exception as e:
        print(f"      {C.RED}Failed to fetch audit logs: {e}{C.RESET}")


# ═════════════════════════════════════════════════════════════
#  Main demo
# ═════════════════════════════════════════════════════════════
def main():
    # Parse args
    user_id = 1
    do_seed = False

    for arg in sys.argv[1:]:
        if arg == "--seed":
            do_seed = True
        elif arg.isdigit():
            user_id = int(arg)

    # ── Optional: seed fresh data ────────────────────────────
    if do_seed:
        print(f"{C.YELLOW}🌱 Seeding database with demo data...{C.RESET}")
        from app.utils.seed_data import seed
        seed(reset=True)
        print()

    # ── Header ───────────────────────────────────────────────
    banner("🤖  AGENTIC DIGITAL TWIN — LIVE DEMO")
    print(f"\n  {C.DIM}Timestamp : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  User ID  : {user_id}")
    print(f"  Server   : {BASE_URL}{C.RESET}")

    # ── Verify server is running ─────────────────────────────
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/")
    except Exception:
        print(f"\n  {C.RED}{C.BOLD}❌ Server not reachable!{C.RESET}")
        print(f"  {C.DIM}Start the server first:  uvicorn app.main:app --reload{C.RESET}")
        sys.exit(1)

    # ── Verify user exists ───────────────────────────────────
    try:
        user_res = _get(f"/users/{user_id}")
        user = user_res["data"]
        print(f"\n  {C.GREEN}✓ User found: {user['name']} ({user['email']}){C.RESET}")
    except urllib.error.HTTPError:
        print(f"\n  {C.RED}{C.BOLD}❌ User {user_id} not found!{C.RESET}")
        print(f"  {C.DIM}Run: python seed_data.py --reset{C.RESET}")
        sys.exit(1)

    # ── Run full pipeline ────────────────────────────────────
    banner("⚡  EXECUTING FULL PIPELINE", C.MAGENTA)
    print(f"\n  {C.DIM}Analyze → Sanitize → Negotiate → Switch → Audit{C.RESET}")
    print(f"  {C.DIM}Running...{C.RESET}", end="", flush=True)

    try:
        result = _post(f"/run-cycle/{user_id}")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode()) if e.fp else {}
        print(f"\n\n  {C.RED}{C.BOLD}❌ Pipeline failed: {body}{C.RESET}")
        sys.exit(1)

    data = result["data"]
    final = data.get("final_status", "unknown")
    print(f" {status_badge(final)}")

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
        section("⚠️ ", "PIPELINE WARNINGS", C.RED)
        for err in errors:
            print(f"      {C.RED}• {err}{C.RESET}")

    # ── Final summary ────────────────────────────────────────
    banner("📋  DEMO SUMMARY", C.GREEN)
    kv("Pipeline Status", status_badge(final), indent=4)
    kv("Modules Executed", "5 / 5" if final == "completed" else "partial", indent=4)
    kv("Audit Entries Logged", str(len(data.get("audit_logged", []))), indent=4)
    if data.get("analysis"):
        kv("Recommendation", data["analysis"].get("recommendation", "—").upper(), indent=4)
    if data.get("negotiation"):
        kv("Negotiated Savings", f"{data['negotiation'].get('savings_pct', 0)}%", indent=4)
    if data.get("switching"):
        kv("Plan Switch Applied", "Yes ✓" if data["switching"].get("applied") else "No", indent=4)

    print(f"\n  {C.DIM}{'─' * 56}{C.RESET}")
    print(f"  {C.GREEN}{C.BOLD}✅ Demo complete — Full automation demonstrated!{C.RESET}")
    print(f"  {C.DIM}Swagger UI: http://127.0.0.1:8000/docs{C.RESET}\n")


if __name__ == "__main__":
    main()
