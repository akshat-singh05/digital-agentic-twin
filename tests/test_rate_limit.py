"""Phase K: the shared (Redis) rate-limit store and its fallback."""

import os

import pytest

from app import config
from app.core import rate_limit


class FakeRedis:
    """Just enough of redis-py for the limiter: counters and a pipeline."""

    def __init__(self, fail=False):
        self.counts, self.expiry, self.fail = {}, {}, fail

    def pipeline(self):
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, seconds):
        self.ops.append(("expire", key, seconds))

    def execute(self):
        if self.redis.fail:
            raise ConnectionError("redis is down")
        results = []
        for op, key, *rest in self.ops:
            if op == "incr":
                self.redis.counts[key] = self.redis.counts.get(key, 0) + 1
                results.append(self.redis.counts[key])
            else:
                self.redis.expiry[key] = rest[0]
                results.append(True)
        return results


@pytest.fixture(autouse=True)
def limits(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 3)
    monkeypatch.setattr(config, "LOGIN_RATE_LIMIT_PER_MINUTE", 2)
    yield
    rate_limit.reset()


def _hits(path, n, ip="10.0.0.1"):
    return [rate_limit.check(ip, path) for _ in range(n)]


def test_memory_limiter_blocks_after_the_limit_and_names_a_wait():
    assert not rate_limit.uses_redis()
    results = _hits("/api/plans", 4)
    assert results[:3] == [0, 0, 0] and 1 <= results[3] <= 61


def test_redis_limiter_counts_per_client_and_bucket():
    fake = FakeRedis()
    rate_limit.set_redis_client(fake)

    assert rate_limit.uses_redis()
    assert _hits("/api/plans", 4)[3] > 0                       # api bucket: 3 per minute
    assert _hits("/api/plans", 1, ip="10.0.0.2") == [0]        # another client is unaffected
    assert _hits("/api/auth/login", 3)[2] > 0                  # auth bucket: 2 per minute
    assert all(key.startswith("twin:rl:") for key in fake.counts)
    assert set(fake.expiry.values()) == {120}                  # counters clean themselves up


def test_redis_counters_are_shared_between_processes():
    fake = FakeRedis()
    rate_limit.set_redis_client(fake)
    _hits("/api/plans", 2)                                     # "worker A"

    rate_limit.set_redis_client(fake)                          # "worker B" talks to the same store
    results = _hits("/api/plans", 2)

    assert results[0] == 0 and results[1] > 0


def test_redis_outage_falls_back_to_the_memory_limiter(caplog):
    rate_limit.set_redis_client(FakeRedis(fail=True))

    with caplog.at_level("ERROR"):
        results = _hits("/api/plans", 4)

    assert results[:3] == [0, 0, 0] and results[3] > 0         # still limited, nobody locked out
    assert "Redis unavailable" in caplog.text and caplog.text.count("Redis unavailable") == 1


def test_pages_and_unlimited_settings_skip_the_store(monkeypatch):
    fake = FakeRedis()
    rate_limit.set_redis_client(fake)
    assert _hits("/", 10) == [0] * 10
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 0)
    assert _hits("/api/plans", 10) == [0] * 10
    assert fake.counts == {}


@pytest.mark.skipif(not os.getenv("REDIS_TEST_URL"), reason="set REDIS_TEST_URL to run against a real Redis")
def test_against_a_real_redis(monkeypatch):
    import redis

    url = os.environ["REDIS_TEST_URL"]
    client = redis.Redis.from_url(url, decode_responses=True)
    for key in client.scan_iter("twin:rl:*"):
        client.delete(key)
    monkeypatch.setattr(config, "REDIS_URL", url)

    results = _hits("/api/plans", 4, ip="192.0.2.77")

    assert results[:3] == [0, 0, 0] and results[3] > 0
    keys = list(client.scan_iter("twin:rl:api:192.0.2.77:*"))
    assert len(keys) == 1 and 0 < client.ttl(keys[0]) <= 120
    client.delete(*keys)
