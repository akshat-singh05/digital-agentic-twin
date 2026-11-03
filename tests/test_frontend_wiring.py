"""Static checks that the dashboard is wired to the real API.

No browser: these read frontend/index.html and frontend/script.js and
compare them with the FastAPI routes, so a renamed endpoint or a page
without a renderer fails the suite.
"""

import pathlib
import re

from app.api.routes import router

_FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "frontend"
_JS = (_FRONTEND / "script.js").read_text(encoding="utf-8")
_HTML = (_FRONTEND / "index.html").read_text(encoding="utf-8")


def _normalize(path: str) -> str:
    """'/runs/user/${selectedUser}?limit=20' and '/runs/user/{user_id}' → '/runs/user/{}'."""
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    return re.sub(r"\{[^}]*\}", "{}", path)


def _api_routes():
    """(path, METHOD) for every API route (mounted under /api)."""
    return {
        (_normalize(r.path), method)
        for r in router.routes
        for method in getattr(r, "methods", ())
    }


def test_every_nav_page_has_a_renderer_and_title():
    pages = set(re.findall(r'data-page="([a-z_]+)"', _HTML))
    renderers = set(re.findall(r"^\s{4}(\w+):\s+render\w+,", _JS, re.M))
    titles = set(re.findall(r"^\s{2}(\w+):\s+'[^']+',", _JS, re.M))
    assert {"approvals", "plans", "privacy", "activity"} <= pages
    assert pages <= renderers, pages - renderers
    assert pages <= titles, pages - titles


def test_import_cards_call_real_endpoints():
    paths = re.findall(r"^\s+path:\s+'(/[^']+)',", _JS, re.M)
    assert sorted(paths) == ["/plans/import", "/usage/import"]
    routes = _api_routes()
    assert all((_normalize(p), "POST") in routes for p in paths)


def test_every_api_call_in_the_dashboard_exists():
    calls = re.findall(r"api(Get|Post|Put)\(\s*[`'\"]([^`'\"]+)[`'\"]", _JS)
    calls += [("Post" if m == "POST" else "Get", p)
              for p, m in re.findall(r"api\(\s*`([^`]+)`,\s*'(GET|POST)'", _JS)]
    assert len(calls) > 20
    # decideRecommendation() builds /recommendations/{id}/${decision}
    calls = [(m, p.replace("${decision}", d)) for m, p in calls
             for d in (("approve", "reject") if "${decision}" in p else ("",))]
    routes = _api_routes()
    missing = sorted(
        (method.upper(), path) for method, path in calls
        if (_normalize(path), method.upper()) not in routes
    )
    assert missing == []
