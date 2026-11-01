"""
Security — password hashing, access tokens, and the authentication
dependency used by every protected route.

Passwords are hashed in app/core/passwords.py (scrypt).

Tokens: signed JWT (HS256, PyJWT) carrying the user id; the user and role
are re-read from the database on every request, so a deleted user or a
changed role takes effect immediately.

Authorization rule (``authorize``):
  - a user may read and act on their own data;
  - an ``admin`` may read any user's data but never acts for them
    (consents, approvals and agent runs stay the user's own decisions).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import ACCESS_TOKEN_MINUTES, SECRET_KEY
from app.database import get_db
from app.models import User

_ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/auth/login")


# ─────────────────────────────────────────────────────────────
# Tokens
# ─────────────────────────────────────────────────────────────
def create_access_token(user_id: int, minutes: int = ACCESS_TOKEN_MINUTES) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, SECRET_KEY, algorithm=_ALGORITHM)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"status": "error", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: the authenticated user, or 401."""
    if credentials is None:
        raise _unauthorized("Not authenticated: send 'Authorization: Bearer <token>'")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[_ALGORITHM])
        user_id = int(payload["sub"])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token has expired; log in again")
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise _unauthorized("Invalid token")

    user = db.get(User, user_id)
    if user is None:
        raise _unauthorized("Invalid token")
    return user


# ─────────────────────────────────────────────────────────────
# Authorization
# ─────────────────────────────────────────────────────────────
def authorize(current: User, owner_id: int, not_found: str, write: bool = False) -> None:
    """
    Allow the owner; allow an admin to read.

    Anyone else gets 404 with ``not_found`` as the message, so other users'
    records cannot be probed for existence.  An admin attempting to act
    for another user gets 403.

    Args:
        current:   The authenticated user.
        owner_id:  The user who owns the resource.
        not_found: Message for the 404 (e.g. "Subscription with id 7 not found").
        write:     True for anything that changes data or acts for the user.
    """
    if current.id == owner_id:
        return
    if current.role == "admin":
        if not write:
            return
        raise HTTPException(
            status_code=403,
            detail={"status": "error",
                    "message": "Administrators can view other users' data but not act for them"},
        )
    raise HTTPException(status_code=404, detail={"status": "error", "message": not_found})


def require_admin(current: User) -> None:
    if current.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"status": "error", "message": "Administrator access required"},
        )
