"""
Account Service — password reset, email verification, password change and
signing out everywhere.

Link tokens are random, single-use, expire, and only their SHA-256 is stored.
Asking for a new token of a kind voids the unused ones before it.  Every
password change raises ``users.token_version``, which invalidates all access
tokens issued earlier (see app/core/security.py).
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import quote

from sqlalchemy.orm import Session

from app import config
from app.core.passwords import hash_password, verify_password
from app.models import AccountToken, User
from app.services.base_service import transactional
from app.services.user_service import get_user_by_email


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)     # stored as naive UTC


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lifetime(purpose: str) -> timedelta:
    if purpose == "reset":
        return timedelta(minutes=config.RESET_TOKEN_MINUTES)
    return timedelta(hours=config.VERIFY_TOKEN_HOURS)


def issue_token(db: Session, user: User, purpose: str) -> str:
    """Create a link token for the user and return it (the only time it exists in clear)."""
    raw = secrets.token_urlsafe(32)
    now = _now()
    with transactional(db):
        db.query(AccountToken).filter(
            AccountToken.user_id == user.id, AccountToken.purpose == purpose,
            AccountToken.used_at.is_(None),
        ).update({"used_at": now})                               # void earlier unused links
        db.add(AccountToken(user_id=user.id, purpose=purpose, token_hash=_digest(raw),
                            expires_at=now + _lifetime(purpose)))
    return raw


def _consume(db: Session, raw: str, purpose: str) -> Optional[User]:
    """The token's user if it is valid, unused and unexpired; the token is then spent."""
    row = db.query(AccountToken).filter(
        AccountToken.token_hash == _digest(raw or ""), AccountToken.purpose == purpose,
    ).first()
    if row is None or row.used_at is not None or row.expires_at <= _now():
        return None
    row.used_at = _now()
    return db.get(User, row.user_id)


def link_for(kind: str, raw: str) -> str:
    """The URL a user opens from the email (the dashboard handles it)."""
    return f"{config.APP_BASE_URL}/?{kind}={quote(raw)}"


# ── Password reset ───────────────────────────────────────────
def request_password_reset(db: Session, email: str) -> Optional[Tuple[User, str]]:
    """(user, token) when the address has a password-capable account, else None."""
    user = get_user_by_email(db, email.strip().lower())
    if user is None:
        return None
    return user, issue_token(db, user, "reset")


def reset_password(db: Session, raw: str, new_password: str) -> Optional[User]:
    """
    Set a new password from a reset link.  Signs the user out everywhere, and
    counts as proof of owning the address.  None if the link is not valid.
    """
    with transactional(db):
        user = _consume(db, raw, "reset")
        if user is None:
            return None
        user.password_hash = hash_password(new_password)
        user.token_version += 1
        user.email_verified_at = user.email_verified_at or _now()
    return user


def change_password(db: Session, user: User, current: str, new: str) -> bool:
    """Change the password after checking the current one; signs out other sessions."""
    if not verify_password(current, user.password_hash):
        return False
    with transactional(db):
        user.password_hash = hash_password(new)
        user.token_version += 1
    return True


def sign_out_everywhere(db: Session, user: User) -> None:
    with transactional(db):
        user.token_version += 1


# ── Email verification ───────────────────────────────────────
def verify_email(db: Session, raw: str) -> Optional[User]:
    with transactional(db):
        user = _consume(db, raw, "verify")
        if user is None:
            return None
        user.email_verified_at = user.email_verified_at or _now()
    return user
