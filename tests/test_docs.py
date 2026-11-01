"""Phase G: the README, Dockerfile and config must agree with the code."""

import pathlib
import re

from app import config
from app.main import app

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_README = (_ROOT / "README.md").read_text(encoding="utf-8")
_DOCKERFILE = (_ROOT / "Dockerfile").read_text(encoding="utf-8")


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def _documented_endpoints():
    """(METHOD, path) from the README endpoint tables; `GET` / `PUT` rows give two."""
    found = set()
    for line in _README.splitlines():
        m = re.match(r"\| `([A-Z]+)`(?: / `([A-Z]+)`)? \| `(/[^`]*)`", line)
        if m:
            for method in filter(None, m.group(1, 2)):
                found.add((method, _norm(m.group(3))))
    return found


def _served_endpoints():
    spec = app.openapi()
    return {(m.upper(), _norm(p)) for p, ops in spec["paths"].items() for m in ops
            if p.startswith("/api") or p == "/health"}


def test_readme_documents_exactly_the_endpoints_the_api_serves():
    documented, served = _documented_endpoints(), _served_endpoints()
    assert len(served) > 30
    assert sorted(served - documented) == [], "served but not in the README"
    assert sorted(documented - served) == [], "in the README but not served"


def test_readme_settings_table_matches_config():
    table = [n for n in re.findall(r"^\| `([A-Z_]+)` \|", _README, re.M)
             if n not in ("GET", "POST", "PUT")]
    assert {"SECRET_KEY", "CORS_ORIGINS", "RATE_LIMIT_PER_MINUTE"} <= set(table)
    for name in table:
        assert hasattr(config, name), f"README documents {name}, config has no such setting"


def test_readme_defaults_match_config_source():
    # The test run overrides the rate limits, so compare with the declared defaults.
    source = (_ROOT / "app" / "config.py").read_text(encoding="utf-8")
    for name in ("ACCESS_TOKEN_MINUTES", "RATE_LIMIT_PER_MINUTE", "LOGIN_RATE_LIMIT_PER_MINUTE",
                 "REJECTION_COOLDOWN_DAYS"):
        default = re.search(rf'{name} = int\(os.getenv\("{name}", "(\d+)"\)\)', source).group(1)
        assert f"| `{name}` | `{default}` |" in _README, name


def test_health_endpoint(client):
    res = client.get("/health")                      # no token needed
    assert res.status_code == 200 and res.json()["status"] == "success"


def test_dockerfile_is_production_safe():
    assert "SECRET_KEY" not in re.sub(r"#.*", "", _DOCKERFILE)   # no baked-in secret
    assert "DEBUG=false" in _DOCKERFILE
    assert re.search(r"^USER \w+", _DOCKERFILE, re.M)            # not root
    assert "--workers\", \"1\"" in _DOCKERFILE                   # rate limiter is in-process
    assert "/health" in _DOCKERFILE and "8000" in _DOCKERFILE
    for needed in ("app", "frontend", "migrations", "alembic.ini", "requirements.txt"):
        assert re.search(rf"^COPY .*\b{re.escape(needed)}\b", _DOCKERFILE, re.M), needed


def test_dockerignore_keeps_databases_and_secrets_out_of_the_image():
    ignored = (_ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for pattern in ("*.db", ".env", ".git"):
        assert pattern in ignored
