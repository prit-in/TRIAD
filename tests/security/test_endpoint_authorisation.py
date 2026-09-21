"""ST-02: every endpoint's role check, enumerated from the app rather than from a list.

The point of generating the cases from the OpenAPI document is that a *new* endpoint is covered the
moment it is added. A hand-written list covers the endpoints someone remembered, which is how
`GET /approvals` and `GET /approvals/{id}` shipped with no role dependency at all - any
authenticated account, a viewer included, could read every pending promotion with its target
and evidence URI.

Two properties are asserted here:

  1. Every non-public route refuses an anonymous caller. Not 404, not 200 with empty data - a
     401, because "no such thing" and "not for you" are different answers and only one of them
     is true.
  2. Every route this project has declared admin-only refuses a non-admin with 403.

A route that needs neither (there is one: the login endpoint) is listed explicitly, so adding
another public route is a deliberate edit to this file rather than a silent omission.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app.deps import get_session
from backend.app.main import app
from backend.app.security import hash_password, issue_token
from triad_core.models import User

PASSWORD = "correct horse battery staple"

# Routes that answer without a token, by design.
PUBLIC = {
    ("POST", "/api/v1/auth/token"),
    ("GET", "/health"),
    ("GET", "/metrics"),
    ("GET", "/docs"),
    ("GET", "/redoc"),
    ("GET", "/openapi.json"),
    ("GET", "/docs/oauth2-redirect"),
}

# Routes gated to administrators. Each is a deliberate decision recorded here as well as in
# the router, so removing the dependency breaks this test rather than going unnoticed.
ADMIN_ONLY = {
    ("GET", "/api/v1/audit-logs"),
    ("GET", "/api/v1/audit-logs/verify"),
    ("GET", "/api/v1/settings"),
}

# A path parameter needs *a* value for the request to reach the dependency that refuses it.
# The value is never found; that is fine, because authorisation is checked before the lookup.
SAMPLE = {
    "run_ref": "run-2026-01-01-001",
    "model_ref": "churn_classifier",
    "ref": "transactions",
    "version_no": "1",
    "version_id": str(uuid.uuid4()),
    "report_id": str(uuid.uuid4()),
    "alert_id": str(uuid.uuid4()),
    "approval_id": str(uuid.uuid4()),
    "name": "export.csv",
}


def _routes() -> list[tuple[str, str]]:
    """Every route a client can see, taken from the OpenAPI document.

    Not from `app.routes`: this FastAPI version keeps an included router as a single opaque
    entry rather than flattening its children, so walking `app.routes` finds seven routes and
    none of the API. The generated document is what a caller actually has, which makes it the
    right thing to hold to the role checks.
    """
    out: list[tuple[str, str]] = []
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if method.upper() in {"HEAD", "OPTIONS", "PARAMETERS"}:
                continue
            out.append((method.upper(), path))
    # Served, deliberately outside the schema, and still worth asserting about.
    out.append(("GET", "/metrics"))
    return sorted(set(out))


def _fill(path: str) -> str:
    for name, value in SAMPLE.items():
        path = path.replace("{" + name + "}", value)
    return path


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def viewer(db_session):
    user = User(
        email=f"viewer-{uuid.uuid4().hex[:8]}@triad.test",
        display_name="viewer",
        role="viewer",
        password_hash=hash_password(PASSWORD),
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _auth(user: User) -> dict[str, str]:
    token, _ = issue_token(user_id=user.id, email=user.email, role=user.role)
    return {"Authorization": f"Bearer {token}"}


def test_the_enumeration_found_the_api(postgres_available) -> None:
    """A guard on the guard: an empty route list would make every case below vacuous."""
    if not postgres_available:
        pytest.skip("postgres service is not reachable")
    api_routes = [r for r in _routes() if r[1].startswith("/api/v1")]
    assert len(api_routes) >= 30, api_routes


@pytest.mark.parametrize(("method", "path"), [r for r in _routes() if r not in PUBLIC])
def test_every_non_public_route_refuses_an_anonymous_caller(
    client, postgres_available, method: str, path: str
) -> None:
    if not postgres_available:
        pytest.skip("postgres service is not reachable")
    response = client.request(method, _fill(path), json={})
    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} without a token"
    )


@pytest.mark.parametrize(("method", "path"), sorted(ADMIN_ONLY))
def test_admin_only_routes_refuse_a_viewer(
    client, viewer, postgres_available, method: str, path: str
) -> None:
    if not postgres_available:
        pytest.skip("postgres service is not reachable")
    response = client.request(method, _fill(path), headers=_auth(viewer), json={})
    assert response.status_code == 403, (
        f"{method} {path} answered {response.status_code} for a viewer"
    )
