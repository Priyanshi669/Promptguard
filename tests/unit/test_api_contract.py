"""Integration contract: the frontend client and the backend API agree.

The frontend `client.ts` builds request URLs from route literals fed to
`request<T>()` (plus the export URL helper); this test parses those
literals, normalizes them to FastAPI path syntax, and compares both
directions against the OpenAPI schema produced by `create_app()`. The
OpenAPI schema is the machine-readable registered-route inventory;
iterating `app.routes` is NOT sufficient because FastAPI mounts included
routers lazily (`_IncludedRouter`), so route objects carry no path.

Sanity guards assert the parser stays non-vacuous: an empty or tiny
client surface fails the test rather than passing silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promptguard.interfaces.api.app import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIENT_TS = PROJECT_ROOT / "frontend" / "src" / "api" / "client.ts"

# Path literals at `request<T>(` call sites. The first argument is a
# template literal (backticked) or a plain string; each form is parsed
# separately because a shared negated class would swallow the other
# quote type and produce garbage captures.
REQUEST_TEMPLATE_RE = re.compile(r"request<[^>]*>\(\s*`([^`]+)`", re.DOTALL)
REQUEST_STRING_RE = re.compile(r'request<[^>]*>\(\s*"([^"]+)"', re.DOTALL)
# Export helper: `${API_PREFIX}/runs/.../export?format=...` template.
EXPORT_URL_RE = re.compile(r"API_PREFIX\s*}(/runs/[^`]*)")
_PLACEHOLDER_RE = re.compile(r"\$\{[^}]*\}")
_OPENAPI_PARAM_RE = re.compile(r"\{[^}]+\}")


def _normalize(path: str) -> str:
    """Reduce a path on either side to the {param} placeholder syntax."""
    # Runtime placeholders -> route params; terminate query strings.
    path = _PLACEHOLDER_RE.sub("{param}", path).split("?", 1)[0]
    # FastAPI/OpenAPI route params -> same placeholder vocabulary.
    return _OPENAPI_PARAM_RE.sub("{param}", path)


def _client_paths() -> set[str]:
    """Endpoint paths referenced by the frontend client (with /api prefix)."""
    source = CLIENT_TS.read_text(encoding="utf-8")
    literals = REQUEST_TEMPLATE_RE.findall(source)
    literals += REQUEST_STRING_RE.findall(source)
    literals += EXPORT_URL_RE.findall(source)
    return {
        f"/api/v1{_normalize(literal)}" for literal in literals
    }


def _registered_paths(client: TestClient) -> set[str]:
    """Paths from the OpenAPI schema (all routers, machine-readable)."""
    return {
        _normalize(path)
        for path in client.get("/api/openapi.json").json()["paths"]
    }


# Endpoints that legitimately exist server-side without a UI consumer:
# the single calibration validation detail route (the dashboard gets
# validation state via the overview payload instead).
ALLOWED_API_ONLY = {
    "/api/v1/calibration/draft/validation",
}

MIN_CLIENT_ENDPOINTS = 6
MIN_REGISTERED_ENDPOINTS = 10


@pytest.mark.skipif(
    not CLIENT_TS.is_file(), reason="frontend source not present",
)
def test_every_frontend_endpoint_is_registered() -> None:
    """Direction 1: the UI must not call routes the API does not serve."""
    client_paths = _client_paths()
    # Non-vacuous parser guard: the dashboard consumes the run surface
    # and the calibration workspace; fewer paths means parser rot.
    assert len(client_paths) >= MIN_CLIENT_ENDPOINTS, sorted(client_paths)
    with TestClient(create_app()) as http:
        registered = _registered_paths(http)
    missing = client_paths - registered
    assert not missing, f"frontend calls missing API routes: {sorted(missing)}"


@pytest.mark.skipif(
    not CLIENT_TS.is_file(), reason="frontend source not present",
)
def test_backend_endpoints_do_not_silently_exceed_client_surface() -> None:
    """Direction 2: API routes must be UI-consumed or explicitly listed."""
    with TestClient(create_app()) as http:
        registered = _registered_paths(http)
    assert len(registered) >= MIN_REGISTERED_ENDPOINTS, sorted(registered)
    client_paths = _client_paths()
    unaccounted = registered - client_paths - ALLOWED_API_ONLY
    assert not unaccounted, f"undocumented API routes: {sorted(unaccounted)}"
