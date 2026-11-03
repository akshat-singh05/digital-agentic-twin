"""Phase K: password reset, email verification, token revocation."""

import re
from datetime import datetime, timedelta

import pytest

from app import config
from app.core import mailer
from app.core.security import create_access_token
from app.database import SessionLocal
from app.models import AccountToken, User
from tests.conftest import TEST_PASSWORD, make_user

NEW_PASSWORD = "a-brand-new-passphrase"


def _tokens_in_outbox(kind):
    """Every ?kind=TOKEN link found in the emails sent so far."""
    return [m.group(1) for mail in mailer.outbox
            for m in re.finditer(rf"\?{kind}=([\w\-]+)", mail["body"])]


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _forgot(client, email):
    return client.post("/api/auth/forgot-password", json={"email": email})


def _reset(client, token, password=NEW_PASSWORD):
    return client.post("/api/auth/reset-password", json={"token": token, "new_password": password})


# ── Password reset ───────────────────────────────────────────
def test_reset_flow_end_to_end(client):
    make_user(client, email="u@example.com")
    client.headers.pop("Authorization")

    assert _forgot(client, "U@example.com").status_code == 202
    [token] = _tokens_in_outbox("reset")
    assert mailer.outbox[-1]["to"] == "u@example.com"
    assert "30 minutes" in mailer.outbox[-1]["body"]

    done = _reset(client, token)

    assert done.status_code == 200
    assert _login(client, "u@example.com", TEST_PASSWORD).status_code == 401
    assert _login(client, "u@example.com", NEW_PASSWORD).status_code == 200


def test_forgot_password_does_not_reveal_which_addresses_exist(client):
    make_user(client, email="u@example.com")

    known = _forgot(client, "u@example.com")
    unknown = _forgot(client, "nobody@example.com")

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    resets = [m["to"] for m in mailer.outbox if "Reset" in m["subject"]]
    assert resets == ["u@example.com"]                                  # only the real one got mail


def test_reset_link_is_single_use(client):
    make_user(client, email="u@example.com")
    _forgot(client, "u@example.com")
    [token] = _tokens_in_outbox("reset")

    assert _reset(client, token).status_code == 200
    again = _reset(client, token, "yet-another-passphrase")

    assert again.status_code == 400 and "invalid or has expired" in again.json()["message"]
    assert _login(client, "u@example.com", NEW_PASSWORD).status_code == 200


def test_a_new_reset_request_voids_the_old_link(client):
    make_user(client, email="u@example.com")
    _forgot(client, "u@example.com")
    _forgot(client, "u@example.com")
    first, second = _tokens_in_outbox("reset")

    assert _reset(client, first).status_code == 400
    assert _reset(client, second).status_code == 200


def test_expired_and_unknown_tokens_are_refused(client):
    make_user(client, email="u@example.com")
    _forgot(client, "u@example.com")
    [token] = _tokens_in_outbox("reset")
    with SessionLocal() as db:
        db.query(AccountToken).update({"expires_at": datetime.utcnow() - timedelta(seconds=1)})
        db.commit()

    assert _reset(client, token).status_code == 400
    assert _reset(client, "x" * 43).status_code == 400
    assert _reset(client, "short").status_code == 422
    assert _login(client, "u@example.com", TEST_PASSWORD).status_code == 200      # password untouched


def test_reset_rejects_a_short_password(client):
    make_user(client, email="u@example.com")
    _forgot(client, "u@example.com")
    [token] = _tokens_in_outbox("reset")

    assert _reset(client, token, "short").status_code == 422
    assert _reset(client, token).status_code == 200            # the token was not spent by the bad try


def test_tokens_are_stored_only_as_hashes(client):
    make_user(client, email="u@example.com")
    _forgot(client, "u@example.com")
    [token] = _tokens_in_outbox("reset")

    with SessionLocal() as db:
        stored = [t.token_hash for t in db.query(AccountToken)]
    assert token not in stored and all(len(h) == 64 for h in stored)


def test_reset_signs_out_existing_sessions_and_confirms_the_email(client):
    uid = make_user(client, email="u@example.com")
    old_auth = client.headers["Authorization"]
    _forgot(client, "u@example.com")
    [token] = _tokens_in_outbox("reset")

    _reset(client, token)

    client.headers["Authorization"] = old_auth
    stale = client.get("/api/auth/me")
    assert stale.status_code == 401 and "signed out" in stale.json()["message"]
    with SessionLocal() as db:
        assert db.get(User, uid).email_verified is True       # reading the email proved ownership


def test_reset_is_audited_and_unauthenticated_requests_are_rate_limited(client, monkeypatch):
    uid = make_user(client, email="u@example.com")
    _forgot(client, "u@example.com")
    [token] = _tokens_in_outbox("reset")
    _reset(client, token)
    token_login = _login(client, "u@example.com", NEW_PASSWORD).json()["data"]["access_token"]
    client.headers["Authorization"] = f"Bearer {token_login}"

    descriptions = [l["description"] for l in client.get(f"/api/audit/{uid}").json()["data"]]
    assert "Password reset with an emailed link; all sessions signed out." in descriptions

    client.headers.pop("Authorization")
    monkeypatch.setattr(config, "LOGIN_RATE_LIMIT_PER_MINUTE", 2)
    codes = [_forgot(client, "u@example.com").status_code for _ in range(3)]
    assert codes == [202, 202, 429]


# ── Email verification ───────────────────────────────────────
def _register(client, email="new@example.com"):
    res = client.post("/api/auth/register", json={"name": "New", "email": email, "password": TEST_PASSWORD})
    assert res.status_code == 201
    data = res.json()["data"]
    client.headers["Authorization"] = f"Bearer {data['access_token']}"
    return data


def test_registration_sends_a_confirmation_link_and_it_verifies(client):
    data = _register(client)
    assert data["user"]["email_verified"] is False
    [token] = _tokens_in_outbox("verify")
    assert mailer.outbox[0]["to"] == "new@example.com" and "24 hours" in mailer.outbox[0]["body"]

    res = client.post("/api/auth/verify-email", json={"token": token})

    assert res.status_code == 200
    assert client.get("/api/auth/me").json()["data"]["email_verified"] is True
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400   # single use


def test_resend_verification_replaces_the_old_link(client):
    _register(client)
    [first] = _tokens_in_outbox("verify")

    assert client.post("/api/auth/resend-verification").status_code == 202
    first_again, second = _tokens_in_outbox("verify")

    assert first_again == first
    assert client.post("/api/auth/verify-email", json={"token": first}).status_code == 400
    assert client.post("/api/auth/verify-email", json={"token": second}).status_code == 200
    again = client.post("/api/auth/resend-verification")
    assert "already confirmed" in again.json()["data"]["message"] and len(mailer.outbox) == 2


def test_verification_is_optional_unless_required(client, monkeypatch):
    _register(client)
    client.headers.pop("Authorization")
    assert _login(client, "new@example.com", TEST_PASSWORD).status_code == 200

    monkeypatch.setattr(config, "REQUIRE_EMAIL_VERIFICATION", True)
    blocked = _login(client, "new@example.com", TEST_PASSWORD)
    wrong = _login(client, "new@example.com", "wrong-password")

    assert blocked.status_code == 403 and "Confirm your email" in blocked.json()["message"]
    assert wrong.status_code == 401                              # a wrong password still looks the same
    [token] = _tokens_in_outbox("verify")
    client.post("/api/auth/verify-email", json={"token": token})
    assert _login(client, "new@example.com", TEST_PASSWORD).status_code == 200


# ── Change password and sign out everywhere ──────────────────
def test_change_password_keeps_this_session_and_ends_the_others(client):
    make_user(client, email="u@example.com")
    other = _login(client, "u@example.com", TEST_PASSWORD).json()["data"]["access_token"]

    res = client.post("/api/auth/change-password",
                      json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD})

    assert res.status_code == 200
    client.headers["Authorization"] = f"Bearer {res.json()['data']['access_token']}"
    assert client.get("/api/auth/me").status_code == 200                       # this session continues
    client.headers["Authorization"] = f"Bearer {other}"
    assert client.get("/api/auth/me").status_code == 401                       # the other one ended
    client.headers.pop("Authorization")
    assert _login(client, "u@example.com", NEW_PASSWORD).status_code == 200


def test_change_password_needs_the_current_password(client):
    make_user(client, email="u@example.com")

    res = client.post("/api/auth/change-password",
                      json={"current_password": "not-it", "new_password": NEW_PASSWORD})

    assert res.status_code == 403 and "current password" in res.json()["message"]
    assert client.post("/api/auth/change-password",
                       json={"current_password": TEST_PASSWORD, "new_password": "short"}).status_code == 422
    client.headers.pop("Authorization")
    assert _login(client, "u@example.com", TEST_PASSWORD).status_code == 200


def test_logout_all_revokes_every_token(client):
    uid = make_user(client, email="u@example.com")
    other = _login(client, "u@example.com", TEST_PASSWORD).json()["data"]["access_token"]

    assert client.post("/api/auth/logout-all").status_code == 200

    assert client.get("/api/auth/me").status_code == 401
    client.headers["Authorization"] = f"Bearer {other}"
    assert client.get("/api/auth/me").status_code == 401
    client.headers["Authorization"] = f"Bearer {_login(client, 'u@example.com', TEST_PASSWORD).json()['data']['access_token']}"
    assert client.get("/api/auth/me").status_code == 200                       # signing in again works


def test_tokens_issued_before_versioning_still_work_until_revoked(client):
    uid = make_user(client)
    legacy = create_access_token(uid)                          # no explicit version: counts as 0
    client.headers["Authorization"] = f"Bearer {legacy}"
    assert client.get("/api/auth/me").status_code == 200


def test_account_routes_need_a_token_where_they_act_on_an_account(client):
    client.headers.pop("Authorization", None)
    for path in ("/api/auth/change-password", "/api/auth/logout-all", "/api/auth/resend-verification"):
        assert client.post(path, json={"current_password": "x", "new_password": "y" * 9}).status_code == 401


# ── Email sending ────────────────────────────────────────────
def test_mail_failures_never_reach_the_caller(monkeypatch):
    monkeypatch.setattr(config, "MAIL_MODE", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    mailer.send_mail("u@example.com", "subject", "body")       # raises internally, is logged, returns

    monkeypatch.setattr(config, "SMTP_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SMTP_PORT", 1)
    mailer.send_mail("u@example.com", "subject", "body")        # connection refused: same


def test_emails_are_masked_in_logs():
    assert mailer.mask_email("priya.patel@example.com") == "p***@example.com"
    assert mailer.mask_email("nonsense") == "***"


@pytest.mark.parametrize("kind", ["reset", "verify"])
def test_links_point_at_the_configured_address(client, monkeypatch, kind):
    monkeypatch.setattr(config, "APP_BASE_URL", "https://twin.example.org")
    make_user(client, email="u@example.com")
    if kind == "reset":
        _forgot(client, "u@example.com")
    assert re.search(rf"https://twin\.example\.org/\?{kind}=", mailer.outbox[-1]["body"])
