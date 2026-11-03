"""
Rate limiting — per client IP, in two buckets: login/register/password reset
(strict, against guessing and abuse) and the rest of the API.  Limits come
from app.config and are read on every request.

Two stores:
  memory (default)  sliding one-minute window in this process: it resets on
                    restart and is not shared between workers or instances
  Redis (REDIS_URL) one counter per client, bucket and clock minute, shared by
                    every process.  A fixed window is coarser than a sliding
                    one (a client can briefly send up to twice the limit across a
                    minute boundary) but needs a single round trip.

If Redis is unreachable the limiter falls back to the in-memory store for that
request, logs the problem, and keeps serving: it never blocks everyone because
the counter store is down.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Optional, Tuple

from app import config
from app.core.logger import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 60.0
_AUTH_PATHS = (
    "/api/auth/login", "/api/auth/register", "/api/auth/forgot-password",
    "/api/auth/reset-password", "/api/auth/verify-email",
)

_hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_lock = threading.Lock()

_redis: Any = None
_redis_url = ""
_last_redis_warning = 0.0


def _limit_for(path: str) -> Tuple[str, int]:
    if path in _AUTH_PATHS:
        return "auth", config.LOGIN_RATE_LIMIT_PER_MINUTE
    return "api", config.RATE_LIMIT_PER_MINUTE


def uses_redis() -> bool:
    """True when counters are kept in Redis (the check then does network I/O)."""
    return bool(config.REDIS_URL) or _redis is not None


def set_redis_client(client: Any) -> None:
    """Use this client instead of connecting to REDIS_URL (tests)."""
    global _redis, _redis_url
    _redis, _redis_url = client, config.REDIS_URL


def _client() -> Optional[Any]:
    global _redis, _redis_url
    if _redis is not None and (not config.REDIS_URL or _redis_url == config.REDIS_URL):
        return _redis
    if not config.REDIS_URL:
        return None
    import redis    # imported on demand: only needed when REDIS_URL is set

    _redis = redis.Redis.from_url(
        config.REDIS_URL, socket_timeout=0.3, socket_connect_timeout=0.3, decode_responses=True,
    )
    _redis_url = config.REDIS_URL
    return _redis


def _check_memory(client_ip: str, bucket: str, limit: int) -> int:
    now = time.monotonic()
    with _lock:
        hits = _hits[(client_ip, bucket)]
        while hits and now - hits[0] >= _WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(_WINDOW_SECONDS - (now - hits[0])) + 1)
        hits.append(now)
    return 0


def _check_redis(client: Any, client_ip: str, bucket: str, limit: int) -> int:
    now = time.time()
    key = f"twin:rl:{bucket}:{client_ip}:{int(now // _WINDOW_SECONDS)}"
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, int(_WINDOW_SECONDS) * 2)
    count = pipe.execute()[0]
    if count > limit:
        return max(1, int(_WINDOW_SECONDS - now % _WINDOW_SECONDS) + 1)
    return 0


def _warn_redis_down(error: Exception) -> None:
    global _last_redis_warning
    now = time.monotonic()
    if now - _last_redis_warning > 60:
        _last_redis_warning = now
        logger.error("Rate limiter: Redis unavailable (%s); using the in-memory limiter", error)


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

    client = _client()
    if client is not None:
        try:
            return _check_redis(client, client_ip, bucket, limit)
        except Exception as error:      # network error, timeout, auth failure ...
            _warn_redis_down(error)
    return _check_memory(client_ip, bucket, limit)


def reset() -> None:
    """Forget all recorded requests (tests)."""
    global _redis, _redis_url
    with _lock:
        _hits.clear()
    _redis, _redis_url = None, ""
