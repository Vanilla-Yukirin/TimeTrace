"""Phase 2: /thumbs route + ``require_principal`` (cookie OR bearer).

Two surfaces under test:
  - The thumb route's auth gate (cookie / bearer / neither / wrong)
  - Path-traversal defense (../etc/passwd, absolute paths, dotdot inside)

The bearer side also tests the new ``BearerPrincipal`` / ``CookiePrincipal``
union so we know require_principal actually returns the right shape from
each channel. We don't assert on the principal directly (it's not echoed
back to the client), but a 200 means the dep resolved successfully.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import AuthConfig, StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth, TokenEntry
from timetrace.server.db import Database
from timetrace.server.users import UserStore

# --------------------------------------------------------------------- #
# Fixtures                                                                #
# --------------------------------------------------------------------- #


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def storage_cfg(tmp_path):
    return StorageConfig(data_dir=tmp_path)


@pytest.fixture
def auth_cfg():
    return AuthConfig(
        admin_username="admin",
        admin_initial_password="admin",
        cookie_secure=False,
    )


@pytest.fixture
def server_auth():
    """Two known bearer tokens — ``known`` is valid, ``ghost`` is not."""
    return ServerAuth(
        [
            TokenEntry(value="tt_live_known", label="claude-code"),
            TokenEntry(value="tt_live_capture", label="capture-yuki-win"),
        ]
    )


@pytest.fixture
async def app(db, storage_cfg, auth_cfg, server_auth):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    # Pre-create one thumb file we can fetch, plus a nested one.
    storage_cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
    (storage_cfg.thumbs_dir / "hello.jpg").write_bytes(b"\xff\xd8\xff-fake-jpeg")
    nested = storage_cfg.thumbs_dir / "2026" / "05" / "29"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "x.jpg").write_bytes(b"nested-payload")
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


# --------------------------------------------------------------------- #
# Auth gate                                                                #
# --------------------------------------------------------------------- #


def test_thumb_no_auth_is_401(client):
    r = client.get("/thumbs/hello.jpg")
    assert r.status_code == 401


def test_thumb_with_cookie_after_login(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.get("/thumbs/hello.jpg")
    assert r.status_code == 200
    assert r.content == b"\xff\xd8\xff-fake-jpeg"
    # private cache header so a future CDN won't cache user-private content
    assert "private" in r.headers.get("cache-control", "").lower()


def test_thumb_with_valid_bearer(client):
    r = client.get(
        "/thumbs/hello.jpg",
        headers={"Authorization": "Bearer tt_live_known"},
    )
    assert r.status_code == 200
    assert r.content == b"\xff\xd8\xff-fake-jpeg"


def test_thumb_with_invalid_bearer_is_401(client):
    r = client.get(
        "/thumbs/hello.jpg",
        headers={"Authorization": "Bearer tt_live_ghost"},
    )
    assert r.status_code == 401


def test_thumb_with_malformed_auth_header_is_401(client):
    r = client.get(
        "/thumbs/hello.jpg",
        headers={"Authorization": "NotBearer xyz"},
    )
    assert r.status_code == 401


def test_thumb_cookie_takes_precedence_over_bad_bearer(client):
    """Browser request with a valid cookie + garbage Authorization → 200.

    Real-world scenario: a logged-in browser somehow has a stale Authorization
    header injected (e.g. an extension). Cookie should win.
    """
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.get(
        "/thumbs/hello.jpg",
        headers={"Authorization": "Bearer tt_live_ghost"},
    )
    assert r.status_code == 200


# --------------------------------------------------------------------- #
# Path traversal + 404                                                    #
# --------------------------------------------------------------------- #


def test_thumb_nested_path_works(client):
    r = client.get(
        "/thumbs/2026/05/29/x.jpg",
        headers={"Authorization": "Bearer tt_live_known"},
    )
    assert r.status_code == 200
    assert r.content == b"nested-payload"


def test_thumb_missing_file_is_404_not_401(client):
    """A logged-in user asking for a missing file gets 404, not 401 — the
    auth gate fires first, so absence of file is the failure mode."""
    r = client.get(
        "/thumbs/nonexistent.jpg",
        headers={"Authorization": "Bearer tt_live_known"},
    )
    assert r.status_code == 404


def test_thumb_dotdot_escape_is_404(client, storage_cfg):
    """``../`` shouldn't escape thumbs_dir."""
    # Create a file outside thumbs_dir to make sure we don't accidentally serve it.
    sibling = storage_cfg.data_dir / "secret.txt"
    sibling.write_bytes(b"do-not-serve")
    r = client.get(
        "/thumbs/../secret.txt",
        headers={"Authorization": "Bearer tt_live_known"},
    )
    # Even though secret.txt exists, the traversal is blocked.
    assert r.status_code == 404


def test_thumb_dotdot_inside_then_escape(client, storage_cfg):
    """Multi-segment escape: ``2026/../../secret`` should also be rejected."""
    sibling = storage_cfg.data_dir / "secret.txt"
    sibling.write_bytes(b"do-not-serve")
    r = client.get(
        "/thumbs/2026/../../secret.txt",
        headers={"Authorization": "Bearer tt_live_known"},
    )
    assert r.status_code == 404
