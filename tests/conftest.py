"""
Shared pytest fixtures.

The test database is a throwaway SQLite file built by the real Alembic
migrations once per session; each test then starts from empty tables.
DATABASE_URL (and the security settings) must be set before any ``app``
import, because ``app.database`` and ``app.config`` read them at import time.

Authentication: ``make_user`` registers through ``/api/auth/register`` and
makes the client act as that user (``Authorization`` header); ``as_user``
switches between users already created.  Rate limiting is off by default.
"""

import os
import random
import tempfile

_TEST_DB = os.path.join(tempfile.mkdtemp(prefix="twin_test_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TEST_DB.replace("\\", "/")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "0"

TEST_PASSWORD = "correct-horse-battery"

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.database import Base, engine
from app.db_migrations import migrate_to_head
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """Build the test schema with the real migrations."""
    migrate_to_head(engine)
    yield


@pytest.fixture(autouse=True)
def fresh_db(migrated_db):
    """Give every test empty tables and a fixed random seed."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    random.seed(1234)
    rate_limit.reset()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.tokens = {}          # user_id → access token
        yield c


# ─────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────
def as_user(client, user_id):
    """Make the client act as an already-created user."""
    client.headers["Authorization"] = f"Bearer {client.tokens[user_id]}"
    return user_id


def make_user(client, email="user@example.com", name="Test User", consents=True, role="user"):
    """
    Register a user and act as them.  ``consents=True`` grants every scope
    directly in the DB (no audit entries); pass False, or a list of scopes,
    to test the Privacy Mediator.  ``role="admin"`` promotes the account.
    """
    from app.database import SessionLocal
    from app.models import Consent, User
    from app.modules.privacy_mediator.consent import CONSENT_SCOPES

    res = client.post("/api/auth/register",
                      json={"name": name, "email": email, "password": TEST_PASSWORD})
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    user_id = data["user"]["id"]
    client.tokens[user_id] = data["access_token"]
    as_user(client, user_id)

    scopes = list(CONSENT_SCOPES) if consents is True else (consents or [])
    with SessionLocal() as db:
        db.add_all(Consent(user_id=user_id, scope=s, granted=True) for s in scopes)
        if role != "user":
            db.get(User, user_id).role = role
        db.commit()
    return user_id


def approve(client, recommendation_id):
    res = client.post(f"/api/recommendations/{recommendation_id}/approve")
    assert res.status_code == 200, res.text
    return res.json()["data"]


def make_subscription(client, user_id, provider="Jio", plan_name="Gold 599",
                      monthly_cost=599.0, data_limit_gb=100.0, call_minutes_limit=500):
    res = client.post("/api/subscriptions", json={
        "user_id": user_id, "provider": provider, "plan_name": plan_name,
        "monthly_cost": monthly_cost, "data_limit_gb": data_limit_gb,
        "call_minutes_limit": call_minutes_limit,
    })
    assert res.status_code == 201, res.text
    return res.json()["data"]["id"]


def add_usage(client, user_id, provider="Jio", months=6, data_used_gb=20.0,
              call_minutes_used=100, billing_amount=599.0):
    for m in range(1, months + 1):
        res = client.post("/api/usage", json={
            "user_id": user_id, "provider": provider,
            "period_start": f"2026-{m:02d}-01T00:00:00",
            "period_end": f"2026-{m:02d}-28T00:00:00",
            "data_used_gb": data_used_gb, "call_minutes_used": call_minutes_used,
            "billing_amount": billing_amount,
        })
        assert res.status_code == 201, res.text


def get_subscription(client, sub_id):
    return client.get(f"/api/subscriptions/{sub_id}").json()["data"]
