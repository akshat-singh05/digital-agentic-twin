"""
Password hashing — scrypt from the Python standard library.

Stored format: ``scrypt$n$r$p$salt_hex$hash_hex`` (random 16-byte salt).
Verification recomputes the hash with the stored parameters and compares
in constant time.  Plain passwords are never stored or logged.
"""

import hashlib
import hmac
import secrets
from typing import Optional

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    """True when ``password`` matches ``stored``; False for missing/malformed hashes."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p),
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)
