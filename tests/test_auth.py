"""Phase E: authentication, per-user access control, rate limiting, CORS."""

import pytest

from app import config
from app.core.passwords import hash_password, verify_password
from app.core.security import create_access_token
from app.database import SessionLocal
from app.models import User
from tests.conftest import (
    TEST_PASSWORD, add_usage, agree_price, as_user, make_subscription, make_user,
)


def _login(client, email, password=TEST_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# ── Passwords ────────────────────────────────────────────────
def test_password_hash_is_salted_scrypt():
    a, b = hash_password("s3cret-pass"), hash_password("s3cret-pass")
    assert a.startswith("scrypt$") and a != b           # random salt
    assert verify_password("s3cret-pass", a)
    assert not verify_password("wrong", a)
    assert not verify_password("s3cret-pass", None)
    assert not verify_password("s3cret-pass", "garbage")


def test_password_is_never_stored_or_returned(client):
    res = client.post("/api/auth/register",
                      json={"name": "A", "email": "a@example.com", "password": "plain-text-pw"})
    body = res.text
    assert res.status_code == 201
    assert "plain-text-pw" not in body and "password_hash" not in body
    with SessionLocal() as db:
        stored = db.query(User).filter_by(email="a@example.com").one().password_hash
    assert stored.startswith("scrypt$") and "plain-text-pw" not in stored


# ── Register / login / me ────────────────────────────────────
def test_register_returns_a_working_token(client):
    res = client.post("/api/auth/register",
                      json={"name": "A", "email": "A@Example.com", "password": TEST_PASSWORD})
    data = res.json()["data"]
    assert data["token_type"] == "bearer" and data["expires_in"] == 3600
    assert data["user"]["email"] == "a@example.com" and data["user"]["role"] == "user"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.json()["data"]["id"] == data["user"]["id"]


def test_login_with_correct_password(client):
    make_user(client, email="u@example.com")
    res = _login(client, "U@example.com")
    assert res.status_code == 200 and res.json()["data"]["access_token"]


def test_wrong_password_and_unknown_email_look_the_same(client):
    make_user(client, email="u@example.com")
    wrong = _login(client, "u@example.com", "not-the-password")
    unknown = _login(client, "nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"status": "error", "message": "Invalid email or password"}


def test_short_password_is_rejected(client):
    res = client.post("/api/auth/register", json={"name": "A", "email": "a@x.io", "password": "short"})
    assert res.status_code == 422 and "password" in res.json()["message"]


def test_account_without_password_cannot_log_in(client):
    with SessionLocal() as db:            # e.g. a user created before authentication existed
        db.add(User(name="Legacy", email="legacy@example.com"))
        db.commit()
    assert _login(client, "legacy@example.com", "").status_code in (401, 422)
    assert _login(client, "legacy@example.com", "anything-at-all").status_code == 401


# ── Tokens ───────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    "/api/auth/me", "/api/users/1", "/api/plans", "/api/subscriptions/1",
    "/api/audit/1", "/api/runs/1", "/api/recommendations/1",
])
def test_protected_routes_need_a_token(client, path):
    res = client.get(path)
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.json()["status"] == "error"


def test_invalid_expired_and_orphaned_tokens_are_rejected(client):
    uid = make_user(client)
    for token, message in (
        ("not-a-jwt", "Invalid token"),
        (create_access_token(uid, minutes=-1), "Token has expired; log in again"),
        (create_access_token(99999), "Invalid token"),           # user no longer exists
    ):
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401 and res.json()["message"] == message


def test_public_pages_need_no_token(client):
    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 200


def test_dashboard_files_are_always_revalidated(client):
    # A stale cached index.html/script.js after an upgrade breaks the login flow.
    for path in ("/", "/static/script.js", "/static/styles.css"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


# ── Per-user access control ──────────────────────────────────
@pytest.fixture
def alice_and_bob(client):
    """Alice has a subscription, usage, a negotiation brief, a run and a pending recommendation."""
    alice = make_user(client, email="alice@example.com")
    sub = make_subscription(client, alice)
    add_usage(client, alice, data_used_gb=20.0)
    brief = agree_price(client, alice)
    run = client.post(f"/api/run-cycle/{alice}").json()["data"]
    bob = make_user(client, email="bob@example.com")
    return {"alice": alice, "bob": bob, "sub": sub, "run_id": run["run_id"],
            "brief_id": brief["id"], "rec_id": run["recommendation"]["recommendation_id"]}


def test_other_users_data_looks_nonexistent(client, alice_and_bob):
    a = alice_and_bob
    as_user(client, a["bob"])
    for path in (
        f"/api/users/{a['alice']}", f"/api/users/{a['alice']}/consents",
        f"/api/subscriptions/user/{a['alice']}", f"/api/subscriptions/{a['sub']}",
        f"/api/subscriptions/{a['sub']}/history", f"/api/usage/user/{a['alice']}",
        f"/api/negotiation/briefs/{a['brief_id']}", f"/api/negotiation/briefs/user/{a['alice']}",
        f"/api/audit/{a['alice']}",
        f"/api/recommendations/user/{a['alice']}", f"/api/recommendations/{a['rec_id']}",
        f"/api/runs/{a['run_id']}", f"/api/runs/user/{a['alice']}",
    ):
        res = client.get(path)
        assert res.status_code == 404, path
        assert "not found" in res.json()["message"], path


def test_users_cannot_act_for_each_other(client, alice_and_bob):
    a = alice_and_bob
    as_user(client, a["bob"])
    assert client.post(f"/api/recommendations/{a['rec_id']}/approve").status_code == 404
    assert client.post(f"/api/recommendations/{a['rec_id']}/reject").status_code == 404
    assert client.post(f"/api/run-cycle/{a['alice']}").status_code == 404
    assert client.put(f"/api/users/{a['alice']}/consents/usage_analysis",
                      json={"granted": False}).status_code == 404
    assert client.post("/api/usage", json={
        "user_id": a["alice"], "provider": "Jio",
        "period_start": "2026-07-01T00:00:00", "period_end": "2026-07-28T00:00:00",
    }).status_code == 404
    assert client.post(f"/api/subscriptions/{a['sub']}/rollback").status_code == 404
    assert client.post(f"/api/negotiate/{a['alice']}").status_code == 404
    assert client.post(f"/api/negotiation/briefs/{a['brief_id']}/outcome",
                       json={"outcome": "dismissed"}).status_code == 404

    as_user(client, a["alice"])           # Alice's proposal is untouched
    assert client.get(f"/api/recommendations/{a['rec_id']}").json()["data"]["status"] == "proposed"


def test_admin_can_read_but_not_act_for_a_user(client, alice_and_bob):
    a = alice_and_bob
    make_user(client, email="admin@example.com", role="admin")

    assert client.get(f"/api/audit/{a['alice']}").status_code == 200
    assert client.get(f"/api/recommendations/{a['rec_id']}").status_code == 200
    assert len(client.get("/api/users").json()["data"]) == 3

    for method, path, body in (
        ("post", f"/api/recommendations/{a['rec_id']}/approve", None),
        ("post", f"/api/run-cycle/{a['alice']}", None),
        ("put", f"/api/users/{a['alice']}/consents/usage_analysis", {"granted": False}),
    ):
        res = getattr(client, method)(path, json=body)
        assert res.status_code == 403, path
        assert "not act for them" in res.json()["message"]


def test_only_admins_list_or_create_users(client):
    make_user(client)
    assert client.get("/api/users").status_code == 403
    assert client.post("/api/users", json={"name": "X", "email": "x@example.com"}).status_code == 403

    make_user(client, email="admin@example.com", role="admin")
    created = client.post("/api/users", json={"name": "X", "email": "x@example.com"})
    assert created.status_code == 201 and created.json()["data"]["role"] == "user"


# ── Rate limiting ────────────────────────────────────────────
def test_api_requests_are_rate_limited(client, monkeypatch):
    make_user(client)
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 3)
    codes = [client.get("/api/auth/me").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    res = client.get("/api/auth/me")
    assert res.json()["status"] == "error" and int(res.headers["retry-after"]) > 0
    assert client.get("/").status_code == 200        # pages are not limited


def test_login_has_its_own_stricter_limit(client, monkeypatch):
    make_user(client, email="u@example.com")
    monkeypatch.setattr(config, "LOGIN_RATE_LIMIT_PER_MINUTE", 2)
    codes = [_login(client, "u@example.com", "guess").status_code for _ in range(3)]
    assert codes == [401, 401, 429]
    assert client.get("/api/auth/me").status_code == 200   # API bucket unaffected


# ── CORS ─────────────────────────────────────────────────────
def _preflight(client, origin):
    return client.options("/api/auth/me", headers={
        "Origin": origin, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })


def test_cors_allows_only_configured_origins(client):
    allowed = _preflight(client, "http://localhost:8000")
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:8000"
    assert "access-control-allow-credentials" not in allowed.headers

    blocked = _preflight(client, "https://evil.example")
    assert "access-control-allow-origin" not in blocked.headers
