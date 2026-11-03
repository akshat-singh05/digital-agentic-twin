"""
Data Import Service — DB side of CSV import.

Both imports are all-or-nothing: the whole file is validated first and a
single problem rejects it, so a file is never half-imported.  ``dry_run``
runs every check and reports what would happen without writing.

  usage  — a user's own records; linked to their subscription like
           ``POST /api/usage``.  Importing the same file again is harmless:
           records that already exist are skipped.
  plans  — the shared plan catalog (admin).  Rows are created or updated by
           (provider, plan_name) and stored with ``source="imported"``.
"""

import json
from typing import Any, Dict, List, Tuple

from sqlalchemy.orm import Session

from app.models import PlanCatalog, UsageData
from app.modules.audit_logger.service import build_audit_log
from app.modules.data_import.parser import parse_plans_csv, parse_usage_csv
from app.services.base_service import save, transactional
from app.services.subscription_service import _resolve_usage_subscription
from app.services.user_service import require_user


class ImportRejected(ValueError):
    """The file has problems; ``errors`` lists them and nothing was imported."""

    def __init__(self, errors: List[Dict[str, Any]]):
        self.errors = errors
        count = len(errors)
        super().__init__(
            f"The file has {count} problem{'s' if count != 1 else ''}; nothing was imported"
        )


def _period_key(subscription_id, provider, start, end) -> Tuple:
    return (subscription_id, provider, start, end)


# ─────────────────────────────────────────────────────────────
# Usage
# ─────────────────────────────────────────────────────────────
def import_usage(db: Session, user_id: int, csv_text: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Import a user's usage records from CSV text.

    Raises:
        ValueError:     If the user does not exist.
        ImportRejected: If any row is invalid (nothing is imported).
    """
    require_user(db, user_id)
    rows, errors = parse_usage_csv(csv_text)

    # Link each row to the user's subscription; an explicit id must be theirs
    linked: List[Tuple[Dict[str, Any], Any]] = []
    for row in rows:
        try:
            sub_id = _resolve_usage_subscription(db, {**row, "user_id": user_id})
        except ValueError as exc:
            errors.append({"line": row["line"], "column": "subscription_id", "message": str(exc)})
            continue
        linked.append((row, sub_id))
    if errors:
        raise ImportRejected(sorted(errors, key=lambda e: e["line"]))

    existing = {
        _period_key(u.subscription_id, u.provider, u.period_start, u.period_end)
        for u in db.query(UsageData).filter(UsageData.user_id == user_id)
    }
    fresh: List[UsageData] = []
    skipped = 0
    for row, sub_id in linked:
        key = _period_key(sub_id, row["provider"], row["period_start"], row["period_end"])
        if key in existing:
            skipped += 1
            continue
        existing.add(key)             # also drops repeated rows inside the file
        fresh.append(UsageData(
            user_id=user_id, subscription_id=sub_id, provider=row["provider"],
            period_start=row["period_start"], period_end=row["period_end"],
            data_used_gb=row["data_used_gb"], call_minutes_used=row["call_minutes_used"],
            billing_amount=row["billing_amount"],
        ))

    unlinked = sorted({u.provider for u in fresh if u.subscription_id is None})
    summary = {
        "dry_run": dry_run,
        "rows": len(rows),
        "imported": len(fresh),
        "skipped_existing": skipped,
        "unlinked_providers": unlinked,
    }
    if dry_run or not fresh:
        return summary

    with transactional(db):
        db.add_all(fresh)
        save(db, build_audit_log(db, user_id, "import", {
            "kind": "usage", "created": len(fresh), "skipped": skipped,
        }))
    return summary


# ─────────────────────────────────────────────────────────────
# Plan catalog
# ─────────────────────────────────────────────────────────────
def import_plans(
    db: Session, admin_id: int, csv_text: str, dry_run: bool = False, retire_demo_plans: bool = False,
) -> Dict[str, Any]:
    """
    Create or update catalog plans from CSV text (admin only; checked by the route).

    With ``retire_demo_plans`` every remaining ``demo_seed`` plan is made
    unavailable, so only real plans are recommended.

    Raises:
        ImportRejected: If any row is invalid, or a plan appears twice in the file.
    """
    require_user(db, admin_id)
    rows, errors = parse_plans_csv(csv_text)

    seen: Dict[Tuple[str, str], int] = {}
    for row in rows:
        key = (row["provider"], row["plan_name"])
        if key in seen:
            errors.append({"line": row["line"], "column": "plan_name",
                           "message": f"{key[0]} {key[1]} already appears on line {seen[key]}"})
        seen.setdefault(key, row["line"])
    if errors:
        raise ImportRejected(sorted(errors, key=lambda e: e["line"]))

    existing = {(p.provider, p.plan_name): p for p in db.query(PlanCatalog)}
    created = [r for r in rows if (r["provider"], r["plan_name"]) not in existing]
    updated = [r for r in rows if (r["provider"], r["plan_name"]) in existing]
    imported_keys = set(seen)
    to_retire = [
        p for key, p in existing.items()
        if retire_demo_plans and p.source == "demo_seed" and p.is_available and key not in imported_keys
    ]
    summary = {
        "dry_run": dry_run,
        "rows": len(rows),
        "created": len(created),
        "updated": len(updated),
        "retired_demo_plans": len(to_retire),
    }
    if dry_run:
        return summary

    with transactional(db):
        for row in rows:
            fields = dict(
                category=row["category"], monthly_cost=row["monthly_cost"],
                data_limit_gb=row["data_limit_gb"], call_minutes_limit=row["call_minutes_limit"],
                features=json.dumps(row["features"]) if row["features"] else None,
                is_available=row["is_available"], source="imported",
            )
            plan = existing.get((row["provider"], row["plan_name"]))
            if plan is None:
                db.add(PlanCatalog(provider=row["provider"], plan_name=row["plan_name"], **fields))
            else:
                for name, value in fields.items():
                    setattr(plan, name, value)
        for plan in to_retire:
            plan.is_available = False
        save(db, build_audit_log(db, admin_id, "import", {
            "kind": "plans", "created": len(created), "updated": len(updated),
            "retired": len(to_retire),
        }))
    return summary
