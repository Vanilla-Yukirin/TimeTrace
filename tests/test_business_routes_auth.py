"""Phase 4: business routes (records / search / feedback / categories /
runtime-info / apps) gated with ``require_principal``.

Asserts the gate on each route through three channels:
  - no auth      → 401
  - cookie       → 200 (or non-401)
  - valid bearer → 200 (or non-401)

We don't try to assert payload shape here — that's covered by the existing
test_api.py / test_search.py / test_storage.py — only the auth gate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import AuthConfig, StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth, TokenEntry
from timetrace.server.db import Database
from timetrace.server.users import UserStore

# All business-route endpoints we expect to gate. Each tuple is
# (method, path, optional body). 401-on-no-auth applies to every one.
GATED_ENDPOINTS = [
    ("GET", "/v1/records?start=0&end=99999999999999&limit=1", None),
    ("GET", "/v1/apps", None),
    ("GET", "/v1/runtime-info", None),
    ("GET", "/v1/categories", None),
    (
        "POST",
        "/v1/feedback",
        {"record_id": "fake", "action": "confirm", "category": "work/coding"},
    ),
    # /v1/search/by-image is multipart and harder to construct here; the gate
    # is on the router (whole prefix), so verifying records/apps/categories
    # already proves the include_router dep wiring.
]


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def server_auth():
    return ServerAuth([TokenEntry(value="tt_live_known", label="capture")])


@pytest.fixture
def auth_cfg():
    return AuthConfig(cookie_secure=False)


@pytest.fixture
async def app(db, auth_cfg, server_auth, tmp_path):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    storage_cfg = StorageConfig(data_dir=tmp_path)
    return create_app(
        db,
        storage_cfg=storage_cfg,
        auth=server_auth,
        users=users,
        auth_cfg=auth_cfg,
    )


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _call(client: TestClient, method: str, path: str, body: dict | None, **kw):
    if method == "GET":
        return client.get(path, **kw)
    if method == "POST":
        return client.post(path, json=body or {}, **kw)
    raise ValueError(method)


@pytest.mark.parametrize("method,path,body", GATED_ENDPOINTS)
def test_business_route_no_auth_is_401(client, method, path, body):
    r = _call(client, method, path, body)
    assert r.status_code == 401, f"{method} {path} expected 401 got {r.status_code}"


@pytest.mark.parametrize("method,path,body", GATED_ENDPOINTS)
def test_business_route_with_cookie_passes(client, method, path, body):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = _call(client, method, path, body)
    # The endpoint may return 422 (validation), 200 (success), 404 (missing
    # record) etc., but NOT 401 — that's the only thing the gate decides.
    assert r.status_code != 401, f"{method} {path} expected non-401 got {r.status_code}"


@pytest.mark.parametrize("method,path,body", GATED_ENDPOINTS)
def test_business_route_with_bearer_passes(client, method, path, body):
    r = _call(
        client, method, path, body, headers={"Authorization": "Bearer tt_live_known"}
    )
    assert r.status_code != 401, f"{method} {path} expected non-401 got {r.status_code}"


@pytest.mark.parametrize("method,path,body", GATED_ENDPOINTS)
def test_business_route_with_invalid_bearer_is_401(client, method, path, body):
    r = _call(
        client, method, path, body, headers={"Authorization": "Bearer tt_live_ghost"}
    )
    assert r.status_code == 401


def test_thumbs_still_gated_same_principle(client):
    """Sanity check that /thumbs (Phase 2) still 401s — guards against any
    regression from the include_router dep wiring landing this phase."""
    r = client.get("/thumbs/whatever.jpg")
    assert r.status_code == 401


def test_ingest_still_bearer_only(client):
    """ingest must NOT accept a cookie — it's a write surface for capture
    clients that hold bearer tokens, not for humans."""
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    # Cookie set; ingest should still 401 because make_bearer_dependency is
    # the only gate on it.
    r = client.post("/v1/ingest/record", json={})
    assert r.status_code == 401


def test_auth_routes_not_gated_by_principal(client):
    """Login must work without prior auth (chicken-and-egg)."""
    r = client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200


def test_healthz_remains_public(client):
    r = client.get("/healthz")
    assert r.status_code == 200
