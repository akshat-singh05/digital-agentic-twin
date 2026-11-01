"""
Privacy Mediator Service — usage-data sanitization and consent management.

Sanitization: validates the user, fetches their usage records, passes
them through the pure sanitizer logic, and returns the sanitized output.

Consent: records which scopes (see consent.py) a user has granted.
Every change is written to the audit log.

Follows the same service-layer pattern as usage_analyzer/service.py.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.core.errors import ConsentRequiredError
from app.models import Consent, UsageData
from app.services.base_service import save, transactional
from app.services.user_service import require_user
from app.modules.audit_logger.service import build_audit_log, create_audit_log
from app.modules.privacy_mediator.consent import CONSENT_SCOPES, is_known_scope
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

    Sanitized data exists only to be shared outside the twin, so this
    requires the ``share_usage_with_provider`` consent.

    Steps:
        1. Validate user exists and has consented to sharing.
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
        ValueError:           If user not found.
        ConsentRequiredError: If sharing has not been consented to.
    """
    # Step 1 — Validate user and consent
    require_user(db, user_id)
    require_consent(db, user_id, "share_usage_with_provider", "privacy_mediator")

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


# ─────────────────────────────────────────────────────────────
# Consent management
# ─────────────────────────────────────────────────────────────
def _consent_out(scope: str, row) -> Dict[str, Any]:
    return {
        "scope": scope,
        "description": CONSENT_SCOPES[scope],
        "granted": bool(row and row.granted),
        "updated_at": row.updated_at if row else None,
    }


def list_consents(db: Session, user_id: int) -> List[Dict[str, Any]]:
    """
    Return the user's state for every known scope (ungranted → False).

    Raises:
        ValueError: If user not found.
    """
    require_user(db, user_id)
    rows = {c.scope: c for c in db.query(Consent).filter(Consent.user_id == user_id)}
    return [_consent_out(scope, rows.get(scope)) for scope in CONSENT_SCOPES]


def has_consent(db: Session, user_id: int, scope: str) -> bool:
    """True when the user has granted ``scope``."""
    row = (
        db.query(Consent)
        .filter(Consent.user_id == user_id, Consent.scope == scope)
        .first()
    )
    return bool(row and row.granted)


def require_consent(db: Session, user_id: int, scope: str, agent: str) -> None:
    """
    The single permission gate every agent calls before using user data
    or acting for the user.  A denial is written to the audit log.

    Raises:
        ConsentRequiredError: If ``scope`` has not been granted.
    """
    if has_consent(db, user_id, scope):
        return
    create_audit_log(db, user_id, "access_denied", {"scope": scope, "agent": agent})
    raise ConsentRequiredError(scope, agent)


def set_consent(db: Session, user_id: int, scope: str, granted: bool) -> Dict[str, Any]:
    """
    Grant or revoke one scope and audit the change in the same transaction.

    Raises:
        ValueError: If user not found or scope is unknown.
    """
    require_user(db, user_id)
    if not is_known_scope(scope):
        raise ValueError(
            f"Unknown consent scope '{scope}'. Known: {', '.join(CONSENT_SCOPES)}"
        )

    row = (
        db.query(Consent)
        .filter(Consent.user_id == user_id, Consent.scope == scope)
        .first()
    )
    previous = bool(row and row.granted)

    with transactional(db):
        if row is None:
            row = Consent(user_id=user_id, scope=scope)
        row.granted = granted
        row.updated_at = datetime.now(timezone.utc)
        save(db, row)
        save(db, build_audit_log(db, user_id, "consent", {
            "scope": scope, "granted": granted, "previous": previous,
        }))

    return _consent_out(scope, row)
