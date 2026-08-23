"""End-to-end device identity tests against real SQLite and ASGI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from timetrace.client.core.backend import HttpBackend
from timetrace.common.config import AuthConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth, TokenEntry
from timetrace.server.db import Database
from timetrace.server.storage.blob import LocalBlobStorage
from timetrace.server.users import UserStore

DEVICE_A = "57b81d95-8c0d-41d8-8e56-ef116904661c"
DEVICE_B = "27c75380-75a5-4f56-a51c-d32fb6dcaa51"
DEVICE_C = "08ce9677-f137-4254-a2ba-9275c54eb76b"
TOKEN_A = "tt_live_device_a"
TOKEN_B = "tt_live_device_b"


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def auth():
    return ServerAuth(
        [
            TokenEntry(value=TOKEN_A, label="capture-a", created_at=1),
            # Deliberately reuse the label: binding must key off fingerprint,
            # never a human-readable label that may be recycled after revoke.
            TokenEntry(value=TOKEN_B, label="capture-a", created_at=2),
        ]
    )


def _headers(token: str, device_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if device_id is not None:
        headers["X-Device-Id"] = device_id
    return headers


def _payload(record_id: str, *, metadata: bool = False) -> str:
    payload: dict = {
        "client_record_id": record_id,
        "ts_start": 1_747_300_000_000,
        "app_name": "VSCode",
    }
    if metadata:
        payload["device"] = {
            "name": "雪之下的电脑",
            "description": "Windows capture client",
            "client_version": "0.1.0",
            "capabilities": ["screenshots", "capture", "capture"],
        }
    return json.dumps(payload, ensure_ascii=False)


async def test_missing_header_keeps_legacy_record_unassigned(db, auth):
    app = create_app(db, auth=auth)
    with TestClient(app) as client:
        response = client.post(
            "/v1/ingest/record",
            data={"record": _payload("legacy")},
            headers=_headers(TOKEN_A),
        )
    assert response.status_code == 200
    assert (await db.find_record_by_client_id("legacy"))["device_id"] is None
    assert await db.list_devices() == []


@pytest.mark.parametrize(
    "device_id",
    ["not-a-uuid", DEVICE_A.upper(), "{57b81d95-8c0d-41d8-8e56-ef116904661c}"],
)
async def test_device_header_requires_canonical_uuid(db, auth, device_id):
    app = create_app(db, auth=auth)
    with TestClient(app) as client:
        response = client.post(
            "/v1/ingest/record",
            data={"record": _payload("bad-device")},
            headers=_headers(TOKEN_A, device_id),
        )
    assert response.status_code == 422
    assert await db.find_record_by_client_id("bad-device") is None


async def test_first_ingest_registers_device_and_persists_metadata(db, auth):
    app = create_app(db, auth=auth)
    with TestClient(app) as client:
        response = client.post(
            "/v1/ingest/record",
            data={"record": _payload("registered", metadata=True)},
            headers=_headers(TOKEN_A, DEVICE_A),
        )
    assert response.status_code == 200
    record = await db.find_record_by_client_id("registered")
    assert record["device_id"] == DEVICE_A
    [device] = await db.list_devices()
    assert device["id"] == DEVICE_A
    assert device["name"] == "雪之下的电脑"
    assert device["reported_name"] == "雪之下的电脑"
    assert device["capabilities"] == ["capture", "screenshots"]
    assert "token_fingerprint" not in device
    async with db.conn.execute(
        "SELECT token_fingerprint FROM devices WHERE id=?", (DEVICE_A,)
    ) as cur:
        stored = (await cur.fetchone())["token_fingerprint"]
    assert stored == hashlib.sha256(TOKEN_A.encode()).hexdigest()
    assert TOKEN_A not in stored


async def test_same_device_replay_is_idempotent_but_other_device_conflicts(db, auth):
    app = create_app(db, auth=auth)
    with TestClient(app) as client:
        first = client.post(
            "/v1/ingest/record",
            data={"record": _payload("same-rid")},
            headers=_headers(TOKEN_A, DEVICE_A),
        )
        replay = client.post(
            "/v1/ingest/record",
            data={"record": _payload("same-rid")},
            headers=_headers(TOKEN_A, DEVICE_A),
        )
        conflict = client.post(
            "/v1/ingest/record",
            data={"record": _payload("same-rid")},
            headers=_headers(TOKEN_A, DEVICE_B),
        )
    assert first.status_code == replay.status_code == 200
    assert first.json()["record_id"] == replay.json()["record_id"]
    assert replay.json()["was_new"] is False
    assert conflict.status_code == 409
    assert {device["id"] for device in await db.list_devices()} == {DEVICE_A}


async def test_concurrent_device_binding_and_record_id_races_are_contained(db, auth):
    app = create_app(db, auth=auth)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # The same public device UUID cannot be claimed by two valid tokens.
        claims = await asyncio.gather(
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("claim-a")},
                headers=_headers(TOKEN_A, DEVICE_C),
            ),
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("claim-b")},
                headers=_headers(TOKEN_B, DEVICE_C),
            ),
        )
        assert sorted(response.status_code for response in claims) == [200, 403]

        # A single global client_record_id has exactly one owning device.
        collisions = await asyncio.gather(
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("concurrent-rid")},
                headers=_headers(TOKEN_A, DEVICE_A),
            ),
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("concurrent-rid")},
                headers=_headers(TOKEN_A, DEVICE_B),
            ),
        )
    assert sorted(response.status_code for response in collisions) == [200, 409]
    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM records WHERE client_record_id='concurrent-rid'"
    ) as cur:
        assert (await cur.fetchone())["n"] == 1


async def test_new_ingest_only_heals_open_records_from_same_device(db):
    token_fp = hashlib.sha256(TOKEN_A.encode()).hexdigest()
    base = int(time.time() * 1000) - 100

    async def ingest(record_id: str, device_id: str, ts_start: int) -> str:
        rid, _ = await db.ingest_device_or_get_record(
            CaptureContext(app_name=device_id, process_name="capture", window_title="active"),
            reason="heartbeat",
            event_type="heartbeat",
            client_record_id=record_id,
            device_id=device_id,
            token_fingerprint=token_fp,
            token_label="capture-a",
            ts_start=ts_start,
        )
        return rid

    record_a = await ingest("timeline-a1", DEVICE_A, base)
    record_b = await ingest("timeline-b1", DEVICE_B, base + 1)
    # B's ingest must not close A; then A's next ingest may close only A.
    assert (await db.get_record_by_id(record_a))["ts_end"] is None
    await ingest("timeline-a2", DEVICE_A, base + 2)
    assert (await db.get_record_by_id(record_a))["ts_end"] is not None
    assert (await db.get_record_by_id(record_b))["ts_end"] is None


async def test_startup_heals_each_device_timeline_independently(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    token_fp = hashlib.sha256(TOKEN_A.encode()).hexdigest()
    base = int(time.time() * 1000) - 1_000

    async def ingest(record_id: str, device_id: str, ts_start: int) -> str:
        rid, _ = await database.ingest_device_or_get_record(
            CaptureContext(app_name=device_id, process_name="capture", window_title="active"),
            reason="heartbeat",
            event_type="heartbeat",
            client_record_id=record_id,
            device_id=device_id,
            token_fingerprint=token_fp,
            token_label="capture-a",
            ts_start=ts_start,
        )
        return rid

    record_a1 = await ingest("restart-a1", DEVICE_A, base)
    record_b1 = await ingest("restart-b1", DEVICE_B, base + 100)
    record_a2 = await ingest("restart-a2", DEVICE_A, base + 200)
    # Simulate crash-open rows regardless of the normal insert-time healing.
    async with database.lock:
        await database.conn.execute("UPDATE records SET ts_end=NULL")
        await database.conn.commit()
    await database.close()

    reopened = Database(cfg)
    await reopened.init()
    assert (await reopened.get_record_by_id(record_a1))["ts_end"] == base + 200
    assert (await reopened.get_record_by_id(record_b1))["ts_end"] == base + 100
    assert (await reopened.get_record_by_id(record_a2))["ts_end"] == base + 200
    await reopened.close()


async def test_token_authorizes_many_devices_but_device_cannot_change_token(db, auth):
    """Bearer grants upload authority; each device id is still bound on first sight."""
    app = create_app(db, auth=auth)
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("device-a")},
                headers=_headers(TOKEN_A, DEVICE_A),
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("device-b")},
                headers=_headers(TOKEN_A, DEVICE_B),
            ).status_code
            == 200
        )
        stolen = client.post(
            "/v1/ingest/record",
            data={"record": _payload("stolen")},
            headers=_headers(TOKEN_B, DEVICE_A),
        )
    assert stolen.status_code == 403
    assert await db.find_record_by_client_id("stolen") is None


async def test_close_enforces_device_ownership_and_revocation(db, auth):
    app = create_app(db, auth=auth)
    with TestClient(app) as client:
        created = client.post(
            "/v1/ingest/record",
            data={"record": _payload("close-owned")},
            headers=_headers(TOKEN_A, DEVICE_A),
        ).json()
        wrong = client.post(
            f"/v1/ingest/record/{created['record_id']}/close",
            headers=_headers(TOKEN_A, DEVICE_B),
        )
        assert wrong.status_code == 404
        assert {device["id"] for device in await db.list_devices()} == {DEVICE_A}
        # Once B is a legitimate registered device, ownership mismatch is a
        # non-enumerating 404 rather than an authorization failure.
        assert (
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("owned-by-b")},
                headers=_headers(TOKEN_A, DEVICE_B),
            ).status_code
            == 200
        )
        wrong = client.post(
            f"/v1/ingest/record/{created['record_id']}/close",
            headers=_headers(TOKEN_A, DEVICE_B),
        )
        assert wrong.status_code == 404
        assert await db.set_device_revoked(DEVICE_A, revoked=True)
        revoked = client.post(
            f"/v1/ingest/record/{created['record_id']}/close",
            headers=_headers(TOKEN_A, DEVICE_A),
        )
    assert revoked.status_code == 403


async def test_cutover_claims_legacy_record_for_screenshot_replay_and_close(db, auth, tmp_path):
    legacy_id, _ = await db.ingest_or_get_record(
        CaptureContext(app_name="legacy", process_name="old", window_title="old"),
        reason="heartbeat",
        event_type="heartbeat",
        client_record_id="legacy-pending",
        ts_start=1_747_300_000_000,
    )
    close_only_id, _ = await db.ingest_or_get_record(
        CaptureContext(app_name="legacy-close", process_name="old", window_title="old"),
        reason="heartbeat",
        event_type="heartbeat",
        client_record_id="legacy-close-only",
        ts_start=1_747_300_000_000,
    )
    server_id_only, _ = await db.ingest_or_get_record(
        CaptureContext(app_name="legacy-server-id", process_name="old", window_title="old"),
        reason="heartbeat",
        event_type="heartbeat",
        client_record_id="legacy-server-id-only",
        ts_start=1_747_300_000_000,
    )
    app = create_app(
        db,
        auth=auth,
        blob_storage=LocalBlobStorage(tmp_path / "blobs"),
    )
    with TestClient(app) as client:
        replay = client.post(
            "/v1/ingest/record",
            data={"record": _payload("legacy-pending")},
            files={"image": ("frame.png", b"legacy-image", "image/png")},
            headers=_headers(TOKEN_A, DEVICE_A),
        )
        closed = client.post(
            "/v1/ingest/record/legacy-pending/close",
            json={"ts_end": 1_747_300_001_000},
            headers=_headers(TOKEN_A, DEVICE_A),
        )
        # A close entry can be the first post-upgrade item in the old outbox.
        close_only = client.post(
            "/v1/ingest/record/legacy-close-only/close",
            headers=_headers(TOKEN_A, DEVICE_B),
        )
        server_id_claim = client.post(
            f"/v1/ingest/record/{server_id_only}/close",
            headers=_headers(TOKEN_A, DEVICE_C),
        )

    assert replay.status_code == closed.status_code == close_only.status_code == 200
    assert replay.json()["was_new"] is False
    assert replay.json()["record_id"] == legacy_id
    assert closed.json()["record_id"] == legacy_id
    assert close_only.json()["record_id"] == close_only_id
    assert server_id_claim.status_code == 404
    assert (await db.find_record_by_client_id("legacy-pending"))["device_id"] == DEVICE_A
    assert (await db.find_record_by_client_id("legacy-close-only"))["device_id"] == DEVICE_B
    assert (await db.find_record_by_client_id("legacy-server-id-only"))["device_id"] is None
    assert DEVICE_C not in {device["id"] for device in await db.list_devices()}
    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM screenshots WHERE record_id=?", (legacy_id,)
    ) as cur:
        assert (await cur.fetchone())["n"] == 1


async def test_records_expose_and_filter_device_ownership(db, auth):
    app = create_app(db, auth=auth)
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("filtered-a")},
                headers=_headers(TOKEN_A, DEVICE_A),
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/v1/ingest/record",
                data={"record": _payload("filtered-legacy")},
                headers=_headers(TOKEN_A),
            ).status_code
            == 200
        )

        by_device = client.get(f"/v1/records?device_id={DEVICE_A}")
        unknown = client.get("/v1/records?device_id=_unknown_device")
        invalid = client.get("/v1/records?device_id=not-a-uuid")

    assert by_device.status_code == unknown.status_code == 200
    assert {item["client_record_id"] for item in by_device.json()["items"]} == {"filtered-a"}
    assert by_device.json()["items"][0]["device_id"] == DEVICE_A
    assert {item["client_record_id"] for item in unknown.json()["items"]} == {"filtered-legacy"}
    assert invalid.status_code == 422


async def test_http_backend_injects_unicode_metadata_when_draining_old_payload(db):
    app = create_app(db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        backend = HttpBackend(
            client=raw,
            device_id=DEVICE_A,
            device_name="雪之下的电脑",
            device_description="旧 outbox 也会补元数据",
            client_version="0.1.0",
            capabilities=("outbox", "capture"),
        )
        response = await backend.post_ingest(
            {"client_record_id": "old-outbox", "ts_start": 1},
        )
    assert response["was_new"] is True
    [device] = await db.list_devices()
    assert device["reported_name"] == "雪之下的电脑"
    assert device["reported_description"] == "旧 outbox 也会补元数据"


async def test_admin_device_api_is_cookie_only_and_never_returns_fingerprint(db, auth):
    users = UserStore(db, AuthConfig(cookie_secure=False))
    await users.ensure_admin_seeded()
    app = create_app(
        db,
        auth=auth,
        users=users,
        auth_cfg=AuthConfig(cookie_secure=False),
    )
    await db.validate_or_register_device(
        DEVICE_A,
        token_fingerprint=hashlib.sha256(TOKEN_A.encode()).hexdigest(),
        token_label="capture-a",
    )
    with TestClient(app) as client:
        assert client.get("/v1/admin/devices").status_code == 401
        assert client.get("/v1/admin/devices", headers=_headers(TOKEN_A)).status_code == 401
        assert (
            client.post(
                "/v1/auth/login", json={"username": "admin", "password": "admin"}
            ).status_code
            == 200
        )
        assert client.get("/v1/admin/devices").status_code == 403
        assert (
            client.post(
                "/v1/auth/change-password",
                json={"old_password": "admin", "new_password": "newpass123"},
            ).status_code
            == 204
        )
        listed = client.get("/v1/admin/devices")
        renamed = client.patch(
            f"/v1/admin/devices/{DEVICE_A}",
            json={"name": "Workstation", "description": "desk"},
        )
    assert listed.status_code == renamed.status_code == 200
    assert "token_fingerprint" not in json.dumps(listed.json())
    assert renamed.json()["name"] == "Workstation"


async def test_legacy_database_migration_is_additive_and_idempotent(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path)
    conn.executescript(
        """
        CREATE TABLE records (
            id TEXT PRIMARY KEY, client_record_id TEXT, ts_start INTEGER NOT NULL,
            ts_end INTEGER, event_type TEXT NOT NULL DEFAULT 'heartbeat',
            app_name TEXT NOT NULL DEFAULT '', process_name TEXT NOT NULL DEFAULT '',
            window_title TEXT NOT NULL DEFAULT '', url TEXT, capture_reason TEXT,
            status TEXT NOT NULL DEFAULT 'captured', created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        INSERT INTO records
            (id, client_record_id, ts_start, app_name, created_at, updated_at)
            VALUES ('legacy-row', 'legacy-crid', 1, 'old', 1, 1);
        """
    )
    conn.commit()
    conn.close()

    first = Database(cfg)
    await first.init()
    async with first.conn.execute("PRAGMA table_info(records)") as cur:
        assert "device_id" in {row["name"] for row in await cur.fetchall()}
    assert (await first.get_record_by_id("legacy-row"))["device_id"] is None
    async with first.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='devices'"
    ) as cur:
        assert await cur.fetchone() is not None
    async with first.conn.execute("PRAGMA index_list(records)") as cur:
        assert "idx_records_device_ts" in {row["name"] for row in await cur.fetchall()}
    async with first.conn.execute("PRAGMA index_list(devices)") as cur:
        device_indexes = {row["name"]: row["unique"] for row in await cur.fetchall()}
    assert device_indexes["idx_devices_token_fingerprint"] == 0
    await first.close()

    second = Database(cfg)
    await second.init()
    assert (await second.get_record_by_id("legacy-row"))["device_id"] is None
    await second.close()
