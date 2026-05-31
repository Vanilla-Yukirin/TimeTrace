"""Phase 3: ``BearerOnlyMiddleware`` wrapping the /mcp mount.

What we're verifying:
  - HTTP requests without a bearer header → 401
  - HTTP requests with a malformed / invalid bearer → 401
  - HTTP requests with a valid bearer → pass the middleware (the inner
    FastMCP app may then return its own 4xx for malformed MCP payloads,
    but the key is the gate cleared)
  - Cookie sessions DON'T apply (bearer-only by design — Claude Code
    et al. aren't browsers)
  - Lifespan still flows: ``with TestClient(app)`` triggers
    ``session_manager.run()`` — if the middleware accidentally swallowed
    the lifespan scope this would hang / raise.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import AuthConfig, StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth, TokenEntry
from timetrace.server.db import Database
from timetrace.server.users import UserStore


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def server_auth():
    return ServerAuth([TokenEntry(value="tt_live_known", label="claude-code")])


@pytest.fixture
def auth_cfg():
    return AuthConfig(cookie_secure=False)


@pytest.fixture
async def app(db, server_auth, auth_cfg, tmp_path):
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
    # ``with TestClient(app)`` runs the FastAPI lifespan; if the bearer
    # middleware accidentally consumed the lifespan scope the inner FastMCP
    # session_manager.run() wouldn't start and __enter__ would raise.
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------- #
# Auth gate on /mcp                                                       #
# --------------------------------------------------------------------- #


def test_mcp_no_auth_is_401(client):
    r = client.post("/mcp/", json={})
    assert r.status_code == 401
    assert "missing or invalid bearer" in r.text.lower()
    assert r.headers.get("www-authenticate") == "Bearer"


def test_mcp_invalid_bearer_is_401(client):
    r = client.post("/mcp/", json={}, headers={"Authorization": "Bearer tt_live_ghost"})
    assert r.status_code == 401


def test_mcp_malformed_authorization_is_401(client):
    r = client.post("/mcp/", json={}, headers={"Authorization": "Basic blah"})
    assert r.status_code == 401


def test_mcp_valid_bearer_passes_gate(client):
    """A valid bearer gets past the middleware; the inner FastMCP may then
    400/406 on a non-MCP body, but the response is NOT 401 — proving the
    gate cleared.
    """
    r = client.post("/mcp/", json={}, headers={"Authorization": "Bearer tt_live_known"})
    assert r.status_code != 401, r.text


def test_mcp_cookie_session_does_not_unlock(client):
    """Cookie sessions are for browsers; /mcp is machine-to-machine. Logged-in
    users still need a bearer to hit /mcp (and the Web UI is what they use
    to create that bearer in Phase 6)."""
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.post("/mcp/", json={})
    assert r.status_code == 401


# --------------------------------------------------------------------- #
# Lifespan integrity                                                       #
# --------------------------------------------------------------------- #


def test_other_routes_still_work_under_lifespan(client):
    """If the middleware accidentally swallowed lifespan, the FastMCP
    session_manager wouldn't initialise and the app's startup hook would
    raise — making this whole module fail at fixture setup. A passing
    /healthz proves lifespan + middleware coexist.
    """
    r = client.get("/healthz")
    assert r.status_code == 200
