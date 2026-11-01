"""
User Service — Business logic for user management.

All functions accept a SQLAlchemy Session and return ORM model objects.
Validation errors are raised as ValueError; the route layer converts
them to appropriate HTTP responses.
"""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import User, UserPreference
from app.core.passwords import hash_password, verify_password
from app.services.base_service import get_or_raise, get_or_none, save, transactional

# Preference fields with their defaults (used when none are stored).
_PREFERENCE_DEFAULTS: Dict[str, Any] = {
    "max_monthly_budget": None,
    "min_data_gb": None,
    "min_call_minutes": None,
    "required_features": [],
    "preferred_providers": [],
    "allow_provider_change": False,
}
_JSON_LIST_FIELDS = ("required_features", "preferred_providers")


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Fetch a single user by primary key. Returns None if not found."""
    return get_or_none(db, User, user_id)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Fetch a user by email address. Returns None if not found."""
    return db.query(User).filter(User.email == email).first()


def get_all_users(db: Session, skip: int = 0, limit: int = 50) -> List[User]:
    """Return a paginated list of all users."""
    return db.query(User).offset(skip).limit(limit).all()


def require_user(db: Session, user_id: int) -> User:
    """Fetch a user by ID or raise ValueError if not found."""
    return get_or_raise(db, User, user_id, "User")


def create_user(db: Session, user_data: dict) -> User:
    """
    Create and persist a new user (role "user").

    A plain ``password`` in ``user_data`` is hashed; it is never stored.

    Raises:
        ValueError: If a user with the same email already exists.
    """
    existing = get_user_by_email(db, user_data["email"])
    if existing:
        raise ValueError(f"Email '{user_data['email']}' is already registered")

    user_data = dict(user_data)
    password = user_data.pop("password", None)
    user = User(**user_data, role="user",
                password_hash=hash_password(password) if password else None)
    with transactional(db):
        save(db, user)
    return user


# ─────────────────────────────────────────────────────────────
# Preferences
# ─────────────────────────────────────────────────────────────
def _preferences_out(user_id: int, row: Optional[UserPreference]) -> Dict[str, Any]:
    if row is None:
        return {"user_id": user_id, **_PREFERENCE_DEFAULTS, "updated_at": None}
    out = {"user_id": user_id, "updated_at": row.updated_at}
    for field in _PREFERENCE_DEFAULTS:
        value = getattr(row, field)
        out[field] = json.loads(value) if field in _JSON_LIST_FIELDS and value else value
    for field in _JSON_LIST_FIELDS:
        out[field] = out[field] or []
    return out


def get_preferences(db: Session, user_id: int) -> Dict[str, Any]:
    """
    Return the user's preferences (defaults when none are stored).

    Raises:
        ValueError: If user not found.
    """
    require_user(db, user_id)
    row = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    return _preferences_out(user_id, row)


def replace_preferences(db: Session, user_id: int, prefs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Replace the user's preferences; omitted fields reset to their defaults.

    Raises:
        ValueError: If user not found.
    """
    require_user(db, user_id)
    row = db.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    if row is None:
        row = UserPreference(user_id=user_id)

    for field, default in _PREFERENCE_DEFAULTS.items():
        value = prefs.get(field, default)
        if field in _JSON_LIST_FIELDS:
            value = json.dumps(value or [])
        setattr(row, field, value)

    with transactional(db):
        save(db, row)
    return _preferences_out(user_id, row)


# ─────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────
def authenticate(db: Session, email: str, password: str) -> Optional[User]:
    """
    The user with this email and password, or None.

    Unknown email, wrong password and accounts without a password all
    return None, so callers cannot tell them apart.
    """
    user = get_user_by_email(db, email.strip().lower())
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
