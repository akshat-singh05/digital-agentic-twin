"""Phase K: security headers on the dashboard and the API."""

from tests.conftest import make_user


def test_every_response_has_the_basic_protections(client):
    for path in ("/", "/docs", "/health", "/static/script.js"):
        headers = client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["x-frame-options"] == "DENY", path
        assert headers["referrer-policy"] == "no-referrer", path
        assert "camera=()" in headers["permissions-policy"], path


def test_dashboard_gets_a_content_security_policy_but_docs_do_not(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "base-uri 'self'" in csp and "connect-src 'self'" in csp
    assert "content-security-policy" not in client.get("/docs").headers      # Swagger UI loads from a CDN
    assert "content-security-policy" in client.get("/static/styles.css").headers


def test_api_responses_are_never_cached(client):
    make_user(client)
    assert client.get("/api/auth/me").headers["cache-control"] == "no-store"
    assert client.get("/api/plans").headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-cache"           # dashboard files still revalidate


def test_hsts_only_over_https(client):
    assert "strict-transport-security" not in client.get("/health").headers
    behind_proxy = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert "max-age=31536000" in behind_proxy.headers["strict-transport-security"]
