"""Phase 1 login system: UserStore unit tests + /v1/auth route integration tests.

Splits into two halves so a UserStore-layer bug surfaces with a clean stack
trace instead of getting wrapped in a 500 inside FastAPI. The /v1/auth/* half
goes through ``TestClient`` like the rest of the API tests for cookie /
header parity with real browsers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import AuthConfig, StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.users import (
    InvalidPasswordError,
    LoginLockedOutError,
    UserStore,
    hash_password,
    validate_new_password,
    verify_password,
)

# --------------------------------------------------------------------- #
# Layer 1: password hashing + validation (no DB)                          #
# --------------------------------------------------------------------- #


def test_hash_and_verify_round_trip():
    h = hash_password("hunter2-strong")
    assert verify_password("hunter2-strong", h) is True
    assert verify_password("hunter2-wrong", h) is False


def test_verify_rejects_malformed_hash():
    # bcrypt raises on garbage hashes; we want a False, not a 500.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_validate_new_password_rules():
    # Too short
    with pytest.raises(InvalidPasswordError):
        validate_new_password("abc1")
    # Letters only
    with pytest.raises(InvalidPasswordError):
        validate_new_password("abcdefghij")
    # Digits only
    with pytest.raises(InvalidPasswordError):
        validate_new_password("1234567890")
    # OK
    validate_new_password("abcd1234")
    validate_new_password("MyPass123")


# --------------------------------------------------------------------- #
# Layer 2: UserStore on a real SQLite                                     #
# --------------------------------------------------------------------- #


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def auth_cfg():
    # Use a wider rate-limit threshold for tests; targeted lockout test resets
    # the counter explicitly.
    return AuthConfig(
        admin_username="admin",
        admin_initial_password="admin",
        cookie_secure=False,  # tests use plain HTTP via TestClient
        login_rate_window_s=60,
        login_rate_threshold=3,
        login_lockout_s=120,
    )


async def test_user_store_seed_admin_idempotent(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    assert await users.ensure_admin_seeded() is True
    assert await users.ensure_admin_seeded() is False  # no-op on second call
    # User row is there with must_change=1
    row = await db.get_user("admin")
    assert row is not None
    assert row["password_must_change"] == 1


async def test_user_store_authenticate_success(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    session = await users.authenticate("admin", "admin", ip="127.0.0.1", user_agent="ua")
    assert session.username == "admin"
    assert session.must_change_password is True
    assert len(session.session_id) >= 40  # token_urlsafe(32) ~ 43 chars
    # Session is in DB
    row = await db.get_session(session.session_id)
    assert row is not None
    assert row["user_agent"] == "ua"
    assert row["ip"] == "127.0.0.1"


async def test_user_store_authenticate_wrong_password(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    with pytest.raises(PermissionError):
        await users.authenticate("admin", "wrong", ip="1.2.3.4", user_agent=None)


async def test_user_store_authenticate_unknown_user(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    with pytest.raises(PermissionError):
        await users.authenticate("nobody", "x", ip="1.2.3.4", user_agent=None)


async def test_rate_limit_locks_out_after_threshold(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    # 3 failures from same IP — at the 4th attempt the lockout fires before
    # the password check even runs.
    for _ in range(auth_cfg.login_rate_threshold):
        with pytest.raises(PermissionError):
            await users.authenticate("admin", "bad", ip="9.9.9.9", user_agent=None)
    with pytest.raises(LoginLockedOutError) as exc:
        await users.authenticate("admin", "admin", ip="9.9.9.9", user_agent=None)
    assert exc.value.retry_after_s > 0
    # Other IPs unaffected
    await users.authenticate("admin", "admin", ip="1.1.1.1", user_agent=None)


async def test_rate_limit_resets_on_success(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    # 2 failures, then a success → counter zeroes; 3 more failures shouldn't
    # lock out because the success wiped the prior streak.
    for _ in range(2):
        with pytest.raises(PermissionError):
            await users.authenticate("admin", "bad", ip="2.2.2.2", user_agent=None)
    await users.authenticate("admin", "admin", ip="2.2.2.2", user_agent=None)
    for _ in range(2):
        with pytest.raises(PermissionError):
            await users.authenticate("admin", "bad", ip="2.2.2.2", user_agent=None)
    # Still below threshold (3), so this fail is permitted as a fail (not 429).
    with pytest.raises(PermissionError):
        await users.authenticate("admin", "bad", ip="2.2.2.2", user_agent=None)


async def test_resolve_session_lazy_purge_expired(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    # Insert a session that's already expired (1 ms ago).
    await db.insert_session(
        "expired-id",
        "admin",
        expires_at=1,  # far in the past
        user_agent=None,
        ip="1.1.1.1",
    )
    result = await users.resolve_session("expired-id")
    assert result is None
    # And the lazy purge removed it.
    assert await db.get_session("expired-id") is None


async def test_change_password_revokes_other_sessions(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    s1 = await users.authenticate("admin", "admin", ip="1.1.1.1", user_agent="a")
    s2 = await users.authenticate("admin", "admin", ip="2.2.2.2", user_agent="b")
    s3 = await users.authenticate("admin", "admin", ip="3.3.3.3", user_agent="c")

    # s1 changes password; s2 and s3 should be gone, s1 still valid.
    await users.change_password(
        "admin",
        "admin",
        "newpassword42",
        current_session_id=s1.session_id,
    )
    assert await users.resolve_session(s1.session_id) is not None
    assert await users.resolve_session(s2.session_id) is None
    assert await users.resolve_session(s3.session_id) is None
    # must_change flag cleared.
    resolved = await users.resolve_session(s1.session_id)
    assert resolved.must_change_password is False


async def test_change_password_rejects_same_password(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    s = await users.authenticate("admin", "admin", ip="1.1.1.1", user_agent=None)
    # Old and new identical (also fails the strength check, but the equality
    # check fires first because that's the cheaper rejection).
    with pytest.raises(ValueError):
        await users.change_password(
            "admin", "admin", "admin", current_session_id=s.session_id
        )


async def test_change_password_rejects_wrong_old(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    s = await users.authenticate("admin", "admin", ip="1.1.1.1", user_agent=None)
    with pytest.raises(PermissionError):
        await users.change_password(
            "admin", "wrong-old", "newpass42", current_session_id=s.session_id
        )


async def test_change_password_validates_strength(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    s = await users.authenticate("admin", "admin", ip="1.1.1.1", user_agent=None)
    with pytest.raises(InvalidPasswordError):
        await users.change_password(
            "admin", "admin", "weak", current_session_id=s.session_id
        )


async def test_purge_expired_sessions_bulk(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    # 2 valid + 3 expired
    for i in range(2):
        await users.authenticate("admin", "admin", ip=f"1.1.1.{i}", user_agent=None)
    for i in range(3):
        await db.insert_session(
            f"exp-{i}", "admin", expires_at=1, user_agent=None, ip=None
        )
    purged = await db.purge_expired_sessions()
    assert purged == 3


# --------------------------------------------------------------------- #
# Layer 3: /v1/auth/* routes via TestClient                               #
# --------------------------------------------------------------------- #


@pytest.fixture
async def app_with_auth(db, auth_cfg):
    users = UserStore(db, auth_cfg)
    await users.ensure_admin_seeded()
    return create_app(db, users=users, auth_cfg=auth_cfg)


@pytest.fixture
def client(app_with_auth):
    with TestClient(app_with_auth) as c:
        yield c


def test_login_success_sets_cookie(client):
    r = client.post(
        "/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert body["must_change_password"] is True
    # TestClient stores cookies on the client object.
    assert "tt_session" in client.cookies


def test_login_wrong_password_is_401_and_no_cookie(client):
    r = client.post(
        "/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert r.status_code == 401
    assert "tt_session" not in client.cookies


def test_login_unknown_user_same_as_wrong_password(client):
    r = client.post(
        "/v1/auth/login", json={"username": "ghost", "password": "x"}
    )
    # Identical 401 — don't leak which one was wrong.
    assert r.status_code == 401


def test_me_requires_session(client):
    r = client.get("/v1/auth/me")
    assert r.status_code == 401


def test_me_returns_session_user_after_login(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.get("/v1/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body == {"username": "admin", "must_change_password": True}


def test_logout_clears_cookie_and_revokes_session(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert "tt_session" in client.cookies
    r = client.post("/v1/auth/logout")
    assert r.status_code == 204
    # Subsequent /me without a fresh login → 401.
    r2 = client.get("/v1/auth/me")
    assert r2.status_code == 401


def test_change_password_full_flow(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.post(
        "/v1/auth/change-password",
        json={"old_password": "admin", "new_password": "newpass42"},
    )
    assert r.status_code == 204
    # /me now reports must_change=False
    me = client.get("/v1/auth/me").json()
    assert me["must_change_password"] is False
    # Logout + log back in with new password
    client.post("/v1/auth/logout")
    r2 = client.post(
        "/v1/auth/login", json={"username": "admin", "password": "newpass42"}
    )
    assert r2.status_code == 200


def test_change_password_weak_new_is_422(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.post(
        "/v1/auth/change-password",
        json={"old_password": "admin", "new_password": "short"},
    )
    assert r.status_code == 422


def test_change_password_wrong_old_is_401(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.post(
        "/v1/auth/change-password",
        json={"old_password": "nope", "new_password": "newpass42"},
    )
    assert r.status_code == 401


def test_change_password_same_as_old_is_422(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.post(
        "/v1/auth/change-password",
        json={"old_password": "admin", "new_password": "admin"},
    )
    assert r.status_code == 422


def test_login_rate_limit_429(client):
    # Threshold=3 from auth_cfg fixture; 4th attempt should be 429.
    for _ in range(3):
        r = client.post(
            "/v1/auth/login", json={"username": "admin", "password": "wrong"}
        )
        assert r.status_code == 401
    r = client.post(
        "/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


# --------------------------------------------------------------------- #
# Layer 4: /docs + /openapi.json behind cookie                            #
# --------------------------------------------------------------------- #


def test_docs_requires_session(client):
    r = client.get("/docs")
    assert r.status_code == 401
    r2 = client.get("/openapi.json")
    assert r2.status_code == 401


def test_docs_visible_after_login(client):
    client.post("/v1/auth/login", json={"username": "admin", "password": "admin"})
    r = client.get("/docs")
    assert r.status_code == 200
    assert "Swagger UI" in r.text or "swagger" in r.text.lower()
    r2 = client.get("/openapi.json")
    assert r2.status_code == 200
    assert r2.json()["info"]["title"]


def test_healthz_still_public(client):
    # /healthz is the probe endpoint; not behind auth.
    r = client.get("/healthz")
    assert r.status_code == 200
