"""
Audit Logger Service — DB persistence for explainable audit logs.

Integrates:
  - Audit Formatter → pure message generation
  - AuditLog model  → persists every decision
  - transactional() → atomic write

Follows the same service-layer pattern as the other modules.
"""

import json
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.services.user_service import require_user
from app.services.base_service import save, transactional
from app.modules.audit_logger.formatter import format_audit_message


# ── Action → module mapping ─────────────────────────────────
_MODULE_MAP = {
    "analysis": "usage_analyzer",
    "negotiation": "negotiation_agent",
    "switching": "plan_switching",
}


def create_audit_log(
    db: Session,
    user_id: int,
    action_type: str,
    payload: Dict[str, Any],
) -> AuditLog:
    """
    Build a human-readable audit message and persist it.

    Steps:
        1. Validate user exists.
        2. Generate message via the pure formatter.
        3. Save to audit_logs within a transaction.
        4. Return the created AuditLog entry.

    Args:
        db:          Active SQLAlchemy session.
        user_id:     Primary key of the user.
        action_type: One of "analysis", "negotiation", "switching".
        payload:     Structured data from the originating module.

    Returns:
        The persisted AuditLog ORM instance.

    Raises:
        ValueError: If user not found or action_type is invalid.
    """
    # Step 1 — Validate user
    require_user(db, user_id)

    # Step 2 — Generate human-readable message
    description = format_audit_message(action_type, payload)

    # Step 3 — Persist
    module = _MODULE_MAP.get(action_type, action_type)

    audit = AuditLog(
        user_id=user_id,
        action=action_type,
        module=module,
        description=description,
        details=json.dumps(payload, default=str),
    )

    with transactional(db):
        save(db, audit)

    return audit


def get_user_audit_logs(
    db: Session,
    user_id: int,
    skip: int = 0,
    limit: int = 100,
) -> List[AuditLog]:
    """
    Retrieve audit logs for a user, most recent first, with pagination.

    Args:
        db:      Active SQLAlchemy session.
        user_id: Primary key of the user.
        skip:    Number of records to skip (offset).
        limit:   Maximum number of records to return.

    Returns:
        List of AuditLog ORM instances.

    Raises:
        ValueError: If user not found.
    """
    require_user(db, user_id)

    return (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
