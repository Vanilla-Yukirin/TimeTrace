"""Phase 6: /v1/admin/tokens CRUD (cookie-only) + ServerAuth hot mutation.

Verifies:
  - list/create/revoke require a cookie session (bearer must NOT unlock them)
  - create returns the value exactly once; list never exposes values
  - a freshly-created token immediately works on a bearer-gated route
    (hot mutation — no restart) and a revoked one immediately stops working
  - duplicate label → 409, unknown label revoke → 404
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
def server_auth(tmp_path):
    # token_dir under tmp_path so add/revoke persist somewhere disposable.
    return ServerAuth(
        [TokenEntry(value="tt_live_seed", label="seed", created_at=1)],
        token_dir=tmp_path / "tokens",
    )


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
    with TestClient(app) as c:
        yield c


def _login(client):
    """Log in as admin/admin AND clear the forced-change flag, so the resulting
    cookie session can reach the gated admin routes. (A raw must-change session
    is now 403 on admin/business/thumbs by design — see test_must_change_*.)"""
    r = client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    r = client.post(
        "/v1/auth/change-password",
        json={"old_password": "admin", "new_password": "newpass123"},
    )
    assert r.status_code == 204


# --------------------------------------------------------------------- #
# Auth gate: cookie-only                                                  #
# --------------------------------------------------------------------- #


def test_list_tokens_requires_cookie(client):
    assert client.get("/v1/admin/tokens").status_code == 401


def test_bearer_does_not_unlock_admin(client):
    # Even a VALID bearer can't manage tokens — admin is interactive-only.
    r = client.get("/v1/admin/tokens", headers={"Authorization": "Bearer tt_live_seed"})
    assert r.status_code == 401


def test_must_change_session_is_403_on_admin(client):
    """A freshly-logged-in admin/admin session (password_must_change=1) must NOT
    be able to manage tokens until the password is changed — 403, not 200."""
    r = client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True
    # List + create + revoke all gated behind the password change.
    assert client.get("/v1/admin/tokens").status_code == 403
    assert client.post("/v1/admin/tokens", json={"label": "x"}).status_code == 403
    assert client.delete("/v1/admin/tokens/seed").status_code == 403


def test_list_tokens_after_login(client):
    _login(client)
    r = client.get("/v1/admin/tokens")
    assert r.status_code == 200
    labels = [t["label"] for t in r.json()]
    assert "seed" in labels
    # values are never listed
    assert all("value" not in t for t in r.json())


# --------------------------------------------------------------------- #
# Create + hot mutation                                                   #
# --------------------------------------------------------------------- #


def test_create_token_returns_value_once(client):
    _login(client)
    r = client.post("/v1/admin/tokens", json={"label": "claude-code"})
    assert r.status_code == 201
    body = r.json()
    assert body["label"] == "claude-code"
    assert body["value"].startswith("tt_live_")
    # And it doesn't appear with a value in the list.
    listed = client.get("/v1/admin/tokens").json()
    entry = next(t for t in listed if t["label"] == "claude-code")
    assert "value" not in entry


def test_created_token_works_immediately_on_bearer_route(client):
    """Hot mutation: a token created via the Web UI works on /thumbs (a
    bearer-gated route) without a server restart."""
    _login(client)
    value = client.post("/v1/admin/tokens", json={"label": "fresh"}).json()["value"]
    # /thumbs with the brand-new bearer — should clear the gate (404 because
    # the file doesn't exist, but NOT 401).
    r = client.get("/thumbs/none.jpg", headers={"Authorization": f"Bearer {value}"})
    assert r.status_code == 404


def test_revoked_token_stops_working_immediately(client):
    _login(client)
    value = client.post("/v1/admin/tokens", json={"label": "temp"}).json()["value"]
    # Revoke (still need the cookie for the admin call)
    assert client.delete("/v1/admin/tokens/temp").status_code == 204
    # Now check the BEARER path alone — clear cookies so require_principal's
    # cookie branch (which would otherwise win) can't mask the revocation.
    client.cookies.clear()
    assert (
        client.get("/thumbs/none.jpg", headers={"Authorization": f"Bearer {value}"}).status_code
        == 401
    )


def test_created_token_works_on_pure_bearer(client):
    """Complements the hot-mutation test: a created token works via bearer
    even with NO cookie present (the real MCP-client scenario)."""
    _login(client)
    value = client.post("/v1/admin/tokens", json={"label": "mcp"}).json()["value"]
    client.cookies.clear()
    r = client.get("/thumbs/none.jpg", headers={"Authorization": f"Bearer {value}"})
    assert r.status_code == 404  # gate cleared (file just doesn't exist)


def test_create_duplicate_label_is_409(client):
    _login(client)
    client.post("/v1/admin/tokens", json={"label": "dup"})
    r = client.post("/v1/admin/tokens", json={"label": "dup"})
    assert r.status_code == 409


def test_revoke_unknown_label_is_404(client):
    _login(client)
    r = client.delete("/v1/admin/tokens/ghost")
    assert r.status_code == 404


def test_revoke_requires_cookie(client):
    assert client.delete("/v1/admin/tokens/seed").status_code == 401
