"""
Rate limiting — fixed one-minute sliding window per client IP.

Two buckets: login/register (strict, against password guessing) and the
rest of the API.  Limits come from app.config and are read on every
request.  State is in memory: it is per process and resets on restart,
so a multi-process deployment needs a shared store (e.g. Redis) instead.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from app import config

_WINDOW_SECONDS = 60.0
_AUTH_PATHS = ("/api/auth/login", "/api/auth/register")

_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _limit_for(path: str) -> Tuple[str, int]:
    if path in _AUTH_PATHS:
        return "auth", config.LOGIN_RATE_LIMIT_PER_MINUTE
    return "api", config.RATE_LIMIT_PER_MINUTE


def check(client_ip: str, path: str) -> int:
    """
    Record one request.  Returns 0 when allowed, otherwise the number of
    seconds until the client may retry.  Only /api paths are limited.
    """
    if not path.startswith("/api/"):
        return 0
    bucket, limit = _limit_for(path)
    if limit <= 0:
        return 0

    now = time.monotonic()
    with _lock:
        hits = _hits[(client_ip, bucket)]
        while hits and now - hits[0] >= _WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(_WINDOW_SECONDS - (now - hits[0])) + 1)
        hits.append(now)
    return 0


def reset() -> None:
    """Forget all recorded requests (tests)."""
    with _lock:
        _hits.clear()
