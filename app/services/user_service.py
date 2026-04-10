"""
User Service — Business logic for user management.

All functions accept a SQLAlchemy Session and return ORM model objects.
Validation errors are raised as ValueError; the route layer converts
them to appropriate HTTP responses.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from app.models import User
from app.services.base_service import get_or_raise, get_or_none, save, transactional


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
    Create and persist a new user.

    Raises:
        ValueError: If a user with the same email already exists.
    """
    existing = get_user_by_email(db, user_data["email"])
    if existing:
        raise ValueError(f"Email '{user_data['email']}' is already registered")

    user = User(**user_data)
    with transactional(db):
        save(db, user)
    return user
