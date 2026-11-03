"""
CSV parsing and validation for data import — pure logic, no DB or framework.

Both parsers read the whole file and return ``(rows, errors)``.  Nothing is
half-accepted: callers import only when ``errors`` is empty.  Every error
names the file line (the header is line 1), the column and the problem.

Usage CSV   provider, period_start, period_end
            [data_used_gb, call_minutes_used, billing_amount, subscription_id]
Plans CSV   provider, plan_name, category, monthly_cost
            [data_limit_gb, call_minutes_limit, features, is_available]
"""

import csv
import io
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Sequence, Tuple

MAX_ROWS = 5000

USAGE_REQUIRED = ("provider", "period_start", "period_end")
USAGE_OPTIONAL = ("data_used_gb", "call_minutes_used", "billing_amount", "subscription_id")
PLANS_REQUIRED = ("provider", "plan_name", "category", "monthly_cost")
PLANS_OPTIONAL = ("data_limit_gb", "call_minutes_limit", "features", "is_available")

Row = Dict[str, Any]
Error = Dict[str, Any]


class _Invalid(Exception):
    """One bad cell; args are (message, column)."""


# ── Cell converters ──────────────────────────────────────────
def _text(value: str, max_len: int = 100) -> str:
    if not value:
        raise _Invalid("is required")
    if len(value) > max_len:
        raise _Invalid(f"is longer than {max_len} characters")
    return value


def _number(value: str, minimum: float = 0.0, exclusive: bool = False) -> float:
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        raise _Invalid(f"'{value}' is not a number")
    if number != number or number in (float("inf"), float("-inf")):
        raise _Invalid(f"'{value}' is not a finite number")
    if number < minimum or (exclusive and number == minimum):
        raise _Invalid(f"must be {'greater than' if exclusive else 'at least'} {minimum:g}")
    return number


def _whole(value: str) -> int:
    number = _number(value)
    if number != int(number):
        raise _Invalid(f"'{value}' must be a whole number")
    return int(number)


def _date(value: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise _Invalid(f"'{value}' is not a date (use YYYY-MM-DD)")


def _bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in ("true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    raise _Invalid(f"'{value}' must be true or false")


def _features(value: str) -> Dict[str, Any]:
    """'5g;hotspot' becomes {"5g": true, "hotspot": true}; a JSON object is kept as is."""
    if value.startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            raise _Invalid("is not valid JSON")
        if not isinstance(parsed, dict):
            raise _Invalid("must be a JSON object")
        return parsed
    keys = [k.strip() for k in value.replace(",", ";").split(";") if k.strip()]
    return {key: True for key in keys}


def _cell(record: Dict[str, str], column: str, convert: Callable, default: Any = None,
          required: bool = False) -> Any:
    """Convert one cell; blank optional cells give ``default``."""
    raw = record.get(column, "")
    if raw == "" and not required:
        return default
    try:
        return convert(raw)
    except _Invalid as exc:
        raise _Invalid(f"{column}: {exc.args[0]}", column)


# ── Shared reader ────────────────────────────────────────────
def _read(
    text: str,
    required: Sequence[str],
    optional: Sequence[str],
    build: Callable[[Dict[str, str]], Row],
) -> Tuple[List[Row], List[Error]]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = next(reader)
    except StopIteration:
        return [], [{"line": 1, "column": None, "message": "The file is empty"}]

    names = [h.strip().lower() for h in header]
    known = set(required) | set(optional)
    errors: List[Error] = []
    missing = [c for c in required if c not in names]
    if missing:
        errors.append({"line": 1, "column": None,
                       "message": f"Missing column(s): {', '.join(missing)}"})
    for name in names:
        if name not in known:
            errors.append({"line": 1, "column": name, "message": "Unknown column"})
    if len(set(names)) != len(names):
        errors.append({"line": 1, "column": None, "message": "A column name appears twice"})
    if errors:
        return [], errors

    rows: List[Row] = []
    for line, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(rows) + len(errors) >= MAX_ROWS:
            errors.append({"line": line, "column": None,
                           "message": f"More than {MAX_ROWS} rows; split the file"})
            break
        if len(cells) > len(names):
            errors.append({"line": line, "column": None, "message": "More cells than columns"})
            continue
        record = {n: (cells[i].strip() if i < len(cells) else "") for i, n in enumerate(names)}
        try:
            rows.append({**build(record), "line": line})
        except _Invalid as exc:
            column = exc.args[1] if len(exc.args) > 1 else None
            errors.append({"line": line, "column": column, "message": str(exc.args[0])})
    if not rows and not errors:
        errors.append({"line": 2, "column": None, "message": "The file has a header but no rows"})
    return rows, errors


# ── Public parsers ───────────────────────────────────────────
def parse_usage_csv(text: str) -> Tuple[List[Row], List[Error]]:
    """Parse a usage CSV into rows ready for ``UsageData``."""

    def build(r: Dict[str, str]) -> Row:
        row = {
            "provider": _cell(r, "provider", _text, required=True),
            "period_start": _cell(r, "period_start", _date, required=True),
            "period_end": _cell(r, "period_end", _date, required=True),
            "data_used_gb": _cell(r, "data_used_gb", _number, 0.0),
            "call_minutes_used": _cell(r, "call_minutes_used", _whole, 0),
            "billing_amount": _cell(r, "billing_amount", _number, 0.0),
            "subscription_id": _cell(r, "subscription_id", _whole),
        }
        if row["period_end"] <= row["period_start"]:
            raise _Invalid("period_end: must be later than period_start", "period_end")
        return row

    return _read(text, USAGE_REQUIRED, USAGE_OPTIONAL, build)


def parse_plans_csv(text: str) -> Tuple[List[Row], List[Error]]:
    """Parse a plans CSV into rows ready for ``PlanCatalog``."""
    from app.models import PLAN_CATEGORIES

    def category(value: str) -> str:
        lowered = value.lower()
        if lowered not in PLAN_CATEGORIES:
            raise _Invalid(f"'{value}' is not one of: {', '.join(PLAN_CATEGORIES)}")
        return lowered

    def cost(value: str) -> float:
        return _number(value, 0.0, exclusive=True)

    def build(r: Dict[str, str]) -> Row:
        return {
            "provider": _cell(r, "provider", _text, required=True),
            "plan_name": _cell(r, "plan_name", _text, required=True),
            "category": _cell(r, "category", category, required=True),
            "monthly_cost": _cell(r, "monthly_cost", cost, required=True),
            "data_limit_gb": _cell(r, "data_limit_gb", _number),
            "call_minutes_limit": _cell(r, "call_minutes_limit", _whole),
            "features": _cell(r, "features", _features),
            "is_available": _cell(r, "is_available", _bool, True),
        }

    return _read(text, PLANS_REQUIRED, PLANS_OPTIONAL, build)
