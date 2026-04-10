"""
Privacy Mediator Service — DB orchestration for usage-data sanitization.

Validates the user, fetches their usage records, passes them through
the pure sanitizer logic, and returns the sanitized output.

Follows the same service-layer pattern as usage_analyzer/service.py.
"""

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models import UsageData
from app.services.user_service import require_user
from app.modules.privacy_mediator.sanitizer import sanitize_records


def _usage_to_dict(record: UsageData) -> Dict[str, Any]:
    """Convert a UsageData ORM instance to a plain dictionary."""
    return {
        "id": record.id,
        "user_id": record.user_id,
        "provider": record.provider,
        "period_start": record.period_start.isoformat() if record.period_start else None,
        "period_end": record.period_end.isoformat() if record.period_end else None,
        "data_used_gb": record.data_used_gb,
        "call_minutes_used": record.call_minutes_used,
        "billing_amount": record.billing_amount,
    }


def get_sanitized_usage(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Fetch a user's usage records and return sanitized (noised) versions.

    Steps:
        1. Validate user exists (raises ValueError if not).
        2. Fetch all usage records for the user.
        3. Convert ORM objects to plain dicts.
        4. Pass through the sanitizer (Laplace noise).
        5. Return the sanitized list.

    Args:
        db:      Active SQLAlchemy session.
        user_id: Primary key of the user.

    Returns:
        Dict with ``sanitized_usage`` list.

    Raises:
        ValueError: If user not found.
    """
    # Step 1 — Validate user
    require_user(db, user_id)

    # Step 2 — Fetch usage records
    records = (
        db.query(UsageData)
        .filter(UsageData.user_id == user_id)
        .order_by(UsageData.period_start.desc())
        .all()
    )

    # Step 3 — Edge case: no usage data
    if not records:
        return {"sanitized_usage": []}

    # Step 4 — Convert to dicts and sanitize
    raw_dicts = [_usage_to_dict(r) for r in records]
    sanitized = sanitize_records(raw_dicts)

    # Step 5 — Return
    return {"sanitized_usage": sanitized}
