"""
Base Service — Reusable helper functions for the service layer.

These utilities eliminate repeated DB patterns across all services.
They are framework-agnostic (no FastAPI imports).

Key primitives:
  - get_or_raise / get_or_none  → safe lookups
  - save                        → add + flush + refresh (no commit)
  - transactional               → context manager that commits or rolls back
"""

from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.database import Base


# ─────────────────────────────────────────────────────────────
# Lookup helpers
# ─────────────────────────────────────────────────────────────
def get_or_raise(db: Session, model, record_id: int, entity_name: str):
    """
    Fetch a record by primary key or raise ValueError.

    Args:
        db:          Active SQLAlchemy session.
        model:       ORM model class (e.g. User, Subscription).
        record_id:   Primary key value to look up.
        entity_name: Human-readable name used in error messages (e.g. "User").

    Returns:
        The ORM model instance.

    Raises:
        ValueError: If no record with the given ID exists.
    """
    obj = db.query(model).filter(model.id == record_id).first()
    if not obj:
        raise ValueError(f"{entity_name} with id {record_id} not found")
    return obj


def get_or_none(db: Session, model, record_id: int):
    """
    Fetch a record by primary key. Returns None if not found.

    A non-raising alternative to ``get_or_raise`` for optional lookups.
    """
    return db.query(model).filter(model.id == record_id).first()


# ─────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────
def save(db: Session, instance):
    """
    Add a model instance to the session, flush, and refresh it.

    This does **not** commit — use inside a ``transactional`` block
    so that multiple saves can be grouped into one atomic commit.

    Args:
        db:       Active SQLAlchemy session.
        instance: ORM model instance to persist.

    Returns:
        The refreshed instance with server-generated defaults populated.
    """
    db.add(instance)
    db.flush()
    db.refresh(instance)
    return instance


@contextmanager
def transactional(db: Session):
    """
    Context manager that wraps a block in a database transaction.

    On success the transaction is committed; on any exception it is
    rolled back and the exception is re-raised.

    Usage::

        with transactional(db):
            save(db, user)
            save(db, subscription)
            # both are committed together, or neither is.
    """
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise
