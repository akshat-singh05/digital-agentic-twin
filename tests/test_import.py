"""Phase I: CSV import of usage records and catalog plans."""

import json
import pathlib

import pytest

from app.database import SessionLocal
from app.models import AuditLog, PlanCatalog, UsageData, User
from app.modules.data_import.parser import MAX_ROWS, parse_plans_csv, parse_usage_csv
from app.utils import promote_admin
from tests.conftest import as_user, make_subscription, make_user

_SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "docs" / "samples"

USAGE_HEADER = "provider,period_start,period_end,data_used_gb,call_minutes_used,billing_amount\n"


def _usage_csv(months=6, data=20.0, calls=100):
    return USAGE_HEADER + "".join(
        f"Jio,2026-{m:02d}-01,2026-{m:02d}-28,{data},{calls},599\n" for m in range(1, months + 1))


def _import_usage(client, uid, csv_text, dry_run=False):
    return client.post("/api/usage/import", json={"user_id": uid, "csv": csv_text, "dry_run": dry_run})


def _import_plans(client, csv_text, **extra):
    return client.post("/api/plans/import", json={"csv": csv_text, **extra})


# ── Parser (pure) ────────────────────────────────────────────
def test_usage_parser_reads_dates_numbers_and_defaults():
    rows, errors = parse_usage_csv(
        "Provider, Period_Start ,period_end\nJio,2026-01-01,31/01/2026\n")
    assert errors == []
    assert rows[0]["provider"] == "Jio" and rows[0]["period_end"].day == 31
    assert rows[0]["data_used_gb"] == 0.0 and rows[0]["call_minutes_used"] == 0
    assert rows[0]["subscription_id"] is None and rows[0]["line"] == 2


def test_usage_parser_reports_every_bad_cell_with_its_line():
    rows, errors = parse_usage_csv(USAGE_HEADER
        + "Jio,2026-01-01,2026-01-28,20,100,599\n"
        + "Jio,not-a-date,2026-02-28,20,100,599\n"
        + "Jio,2026-03-01,2026-03-28,-5,100,599\n"
        + "Jio,2026-04-28,2026-04-01,20,100,599\n"
        + "Jio,2026-05-01,2026-05-28,20,12.5,599\n")
    assert len(rows) == 1
    assert [(e["line"], e["column"]) for e in errors] == [
        (3, "period_start"), (4, "data_used_gb"), (5, "period_end"), (6, "call_minutes_used")]
    assert all(e["message"] for e in errors)


@pytest.mark.parametrize("text, fragment", [
    ("", "empty"),
    ("provider,period_start\nJio,2026-01-01\n", "Missing column(s): period_end"),
    (USAGE_HEADER.strip() + ",extra\nJio,2026-01-01,2026-01-28,1,1,1,x\n", "Unknown column"),
    ("provider,provider,period_start,period_end\n", "twice"),
    (USAGE_HEADER, "no rows"),
])
def test_usage_parser_rejects_bad_files(text, fragment):
    rows, errors = parse_usage_csv(text)
    assert rows == [] and any(fragment in e["message"] for e in errors)


def test_parser_handles_bom_blank_lines_and_row_limit():
    rows, errors = parse_usage_csv("﻿" + USAGE_HEADER + "\nJio,2026-01-01,2026-01-28,1,1,1\n\n")
    assert errors == [] and len(rows) == 1
    big = USAGE_HEADER + "Jio,2026-01-01,2026-01-28,1,1,1\n" * (MAX_ROWS + 1)
    assert any("More than" in e["message"] for e in parse_usage_csv(big)[1])


def test_plans_parser_reads_features_blanks_and_flags():
    rows, errors = parse_plans_csv(
        "provider,plan_name,category,monthly_cost,data_limit_gb,call_minutes_limit,features,is_available\n"
        'Jio,A,Mobile,349,45,,5g;hotspot,yes\n'
        'Jio,B,mobile,499,,300,"{""5g"": true, ""ott"": ""all""}",false\n')
    assert errors == []
    a, b = rows
    assert a["category"] == "mobile" and a["call_minutes_limit"] is None
    assert a["features"] == {"5g": True, "hotspot": True} and a["is_available"] is True
    assert b["data_limit_gb"] is None and b["features"] == {"5g": True, "ott": "all"}
    assert b["is_available"] is False


def test_plans_parser_validates_category_cost_and_json():
    rows, errors = parse_plans_csv(
        "provider,plan_name,category,monthly_cost,features\n"
        "Jio,A,spaceship,349,\n"
        "Jio,B,mobile,0,\n"
        "Jio,C,mobile,100,{broken\n"
        ",D,mobile,100,\n")
    assert rows == []
    assert [e["column"] for e in errors] == ["category", "monthly_cost", "features", "provider"]


def test_sample_files_are_valid():
    assert parse_usage_csv((_SAMPLES / "usage.csv").read_text(encoding="utf-8"))[1] == []
    assert parse_plans_csv((_SAMPLES / "plans.csv").read_text(encoding="utf-8"))[1] == []


# ── Usage import (API) ───────────────────────────────────────
def test_usage_import_links_records_to_the_subscription_and_feeds_the_analysis(client):
    uid = make_user(client)
    sub = make_subscription(client, uid)

    res = _import_usage(client, uid, _usage_csv())

    assert res.status_code == 200
    assert res.json()["data"] == {"dry_run": False, "rows": 6, "imported": 6,
                                  "skipped_existing": 0, "unlinked_providers": []}
    records = client.get(f"/api/usage/user/{uid}").json()["data"]
    assert len(records) == 6 and {r["subscription_id"] for r in records} == {sub}
    analysis = client.post(f"/api/analyze/{uid}").json()["data"]
    assert analysis["optimization_target"]["subscription_id"] == sub


def test_dry_run_reports_without_writing(client):
    uid = make_user(client)
    make_subscription(client, uid)

    res = _import_usage(client, uid, _usage_csv(), dry_run=True).json()["data"]

    assert res["dry_run"] is True and res["imported"] == 6
    assert client.get(f"/api/usage/user/{uid}").json()["data"] == []


def test_importing_the_same_file_twice_adds_nothing(client):
    uid = make_user(client)
    make_subscription(client, uid)
    _import_usage(client, uid, _usage_csv(months=3))

    again = _import_usage(client, uid, _usage_csv(months=4)).json()["data"]

    assert again["imported"] == 1 and again["skipped_existing"] == 3
    assert len(client.get(f"/api/usage/user/{uid}").json()["data"]) == 4


def test_repeated_rows_inside_one_file_count_once(client):
    uid = make_user(client)
    make_subscription(client, uid)
    row = "Jio,2026-01-01,2026-01-28,20,100,599\n"

    res = _import_usage(client, uid, USAGE_HEADER + row + row).json()["data"]

    assert res["imported"] == 1 and res["skipped_existing"] == 1


def test_one_bad_row_rejects_the_whole_file(client):
    uid = make_user(client)
    make_subscription(client, uid)
    text = _usage_csv(months=3) + "Jio,2026-04-01,2026-04-28,abc,100,599\n"

    res = _import_usage(client, uid, text)

    body = res.json()
    assert res.status_code == 422 and body["status"] == "error"
    assert "1 problem" in body["message"] and "nothing was imported" in body["message"]
    assert body["errors"] == [{"line": 5, "column": "data_used_gb",
                               "message": "data_used_gb: 'abc' is not a number"}]
    assert client.get(f"/api/usage/user/{uid}").json()["data"] == []


def test_usage_without_a_subscription_is_flagged_as_unlinked(client):
    uid = make_user(client)

    res = _import_usage(client, uid, _usage_csv(months=2)).json()["data"]

    assert res["imported"] == 2 and res["unlinked_providers"] == ["Jio"]


def test_explicit_subscription_id_must_be_the_users_own(client):
    alice = make_user(client, email="alice@example.com")
    alice_sub = make_subscription(client, alice)
    bob = make_user(client, email="bob@example.com")

    res = _import_usage(client, bob, USAGE_HEADER.strip() + ",subscription_id\n"
                        f"Jio,2026-01-01,2026-01-28,20,100,599,{alice_sub}\n")

    assert res.status_code == 422
    assert res.json()["errors"][0]["column"] == "subscription_id"
    with SessionLocal() as db:
        assert db.query(UsageData).count() == 0


def test_usage_import_respects_ownership_and_size(client):
    alice = make_user(client, email="alice@example.com")
    bob = make_user(client, email="bob@example.com")
    assert _import_usage(client, alice, _usage_csv()).status_code == 404       # Bob acting for Alice
    assert _import_usage(client, bob, "").status_code == 422                    # empty body
    assert _import_usage(client, bob, "x" * 1_000_001).status_code == 422       # over the 1 MB cap

    make_user(client, email="admin@example.com", role="admin")
    assert _import_usage(client, alice, _usage_csv()).status_code == 403        # admins read, not act


def test_usage_import_needs_a_token(client):
    client.headers.pop("Authorization", None)
    assert _import_usage(client, 1, _usage_csv()).status_code == 401


def test_usage_import_is_audited_and_the_file_is_not_stored(client):
    uid = make_user(client)
    make_subscription(client, uid)
    _import_usage(client, uid, _usage_csv(months=2))

    logs = [l for l in client.get(f"/api/audit/{uid}").json()["data"] if l["action"] == "import"]

    assert [l["description"] for l in logs] == ["Usage import: 2 record(s) added, 0 already present."]
    assert logs[0]["module"] == "data_import" and "csv" not in logs[0]["details"]


# ── Plan import (API) ────────────────────────────────────────
PLANS = ("provider,plan_name,category,monthly_cost,data_limit_gb,call_minutes_limit,features\n"
         "Jio,Real 349,mobile,349,45,300,5g;hotspot\n"
         "Airtel,Real 399,mobile,399,50,,5g\n")


def test_only_admins_can_import_plans(client):
    make_user(client)
    assert _import_plans(client, PLANS).status_code == 403
    make_user(client, email="admin@example.com", role="admin")
    assert _import_plans(client, PLANS).status_code == 200


def test_plan_import_creates_then_updates(client):
    make_user(client, role="admin")

    first = _import_plans(client, PLANS).json()["data"]
    changed = PLANS.replace("mobile,349,", "mobile,329,")
    second = _import_plans(client, changed).json()["data"]

    assert (first["created"], first["updated"]) == (2, 0)
    assert (second["created"], second["updated"]) == (0, 2)
    plans = {p["plan_name"]: p for p in client.get("/api/plans").json()["data"]}
    assert plans["Real 349"]["monthly_cost"] == 329.0
    assert plans["Real 349"]["source"] == "imported"
    assert json.loads(plans["Real 349"]["features"]) == {"5g": True, "hotspot": True}
    assert plans["Real 399"]["call_minutes_limit"] is None


def test_plan_import_dry_run_and_rejection_change_nothing(client):
    make_user(client, role="admin")

    dry = _import_plans(client, PLANS, dry_run=True).json()["data"]
    bad = _import_plans(client, PLANS + "Jio,Real 349,mobile,350\n")
    broken = _import_plans(client, PLANS + "Jio,Oops,flying,100\n")

    assert dry["dry_run"] is True and dry["created"] == 2
    assert bad.status_code == 422 and "already appears on line 2" in bad.json()["errors"][0]["message"]
    assert broken.status_code == 422 and broken.json()["errors"][0]["column"] == "category"
    assert client.get("/api/plans").json()["data"] == []


def test_retiring_demo_plans_keeps_only_real_ones_in_play(client):
    make_user(client, role="admin")
    with SessionLocal() as db:
        db.add(PlanCatalog(provider="Jio", plan_name="Demo 599", category="mobile",
                           monthly_cost=599.0, source="demo_seed"))
        db.commit()

    kept = _import_plans(client, PLANS).json()["data"]
    assert kept["retired_demo_plans"] == 0
    retired = _import_plans(client, PLANS, retire_demo_plans=True).json()["data"]

    assert retired["retired_demo_plans"] == 1
    names = {p["plan_name"] for p in client.get("/api/plans").json()["data"]}
    assert names == {"Real 349", "Real 399"}
    with SessionLocal() as db:       # hidden, not deleted: old proposals still point at it
        assert db.query(PlanCatalog).filter_by(plan_name="Demo 599").one().is_available is False


def test_imported_plans_become_the_recommended_alternative(client):
    uid = make_user(client)
    sub = make_subscription(client, uid, plan_name="Gold 599", monthly_cost=599.0)
    _import_usage(client, uid, _usage_csv(data=20.0, calls=100))
    make_user(client, email="admin@example.com", role="admin")
    _import_plans(client, PLANS)

    as_user(client, uid)
    alts = client.get(f"/api/subscriptions/{sub}/alternatives").json()["data"]

    assert [a["plan_name"] for a in alts["fits"]][0] == "Real 349"
    assert alts["fits"][0]["monthly_savings"] == 250.0


def test_plan_import_is_audited_under_the_admin(client):
    admin = make_user(client, role="admin")
    _import_plans(client, PLANS)

    logs = [l for l in client.get(f"/api/audit/{admin}").json()["data"] if l["action"] == "import"]

    assert [l["description"] for l in logs] == ["Plan catalog import: 2 added, 0 updated."]


# ── Admin promotion (command line) ───────────────────────────
def test_promote_admin_command(client, capsys):
    make_user(client, email="me@example.com")

    assert promote_admin.main(["ME@example.com"]) == 0
    with SessionLocal() as db:
        assert db.query(User).filter_by(email="me@example.com").one().role == "admin"
    assert promote_admin.main(["me@example.com"]) == 0
    assert "already" in capsys.readouterr().out
    assert promote_admin.main(["nobody@example.com"]) == 1
    assert promote_admin.main([]) == 2
