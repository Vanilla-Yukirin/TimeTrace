"""End-to-end tests for HttpBackend driving the in-process FastAPI app.

We spin up a real `create_app(...)` instance and bind an `httpx.AsyncClient`
to its ASGI transport. That keeps the test pure-async (no socket, no
TestClient threading) while still exercising the multipart codec, the
ingest route, the close route, the BackendClient Protocol mapping, and
the database round-trip in one shot.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from timetrace.client.core.backend import BackendError, HttpBackend
from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.common.protocol import ScreenshotSubmission
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.storage.blob import LocalBlobStorage


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def blob_storage(tmp_path) -> LocalBlobStorage:
    return LocalBlobStorage(tmp_path / "blobs")


@pytest.fixture
async def backend(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield HttpBackend(client=client)


def _save_png(path, color=(20, 30, 40), size=16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), color=color).save(path)


_CTX = CaptureContext(
    app_name="VSCode",
    process_name="code",
    window_title="main.py",
    url=None,
)


# --------------------------------------------------------------------------- #
# submit_record                                                                 #
# --------------------------------------------------------------------------- #


async def test_submit_record_creates_server_row(backend, db):
    rid = await backend.submit_record(_CTX, reason="switch", event_type="window_switch")
    assert rid

    found = await db.find_record_by_client_id(rid)
    assert found is not None
    assert found["app_name"] == "VSCode"
    assert found["window_title"] == "main.py"


async def test_submit_record_marks_record_pending(backend, db):
    """Each ingest also enqueues a pending_vlm task — worker can pick it up."""
    await backend.submit_record(_CTX, reason="heartbeat")
    task = await db.claim_next_task("pending_vlm")
    assert task is not None


# --------------------------------------------------------------------------- #
# submit_screenshot                                                             #
# --------------------------------------------------------------------------- #


async def test_submit_screenshot_attaches_image_to_existing_record(backend, db, tmp_path):
    rid = await backend.submit_record(_CTX, reason="heartbeat")

    image_path = tmp_path / "captures" / "x.png"
    _save_png(image_path, color=(255, 0, 0))

    sid = await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=str(image_path),
            thumb_path=None,
            width=16,
            height=16,
            hash_sha256="ignored-by-server-uses-its-own",
            phash=0xABCDEF0123456789,
        )
    )
    assert sid

    server_record_id = (await db.find_record_by_client_id(rid))["id"]
    shots = await db.get_screenshots_for_record(server_record_id)
    assert len(shots) == 1
    assert shots[0]["id"] == sid
    assert shots[0]["path"].startswith("screenshots/")


async def test_submit_screenshot_uploads_thumb_when_present(backend, db, tmp_path):
    rid = await backend.submit_record(_CTX, reason="heartbeat")

    image_path = tmp_path / "captures" / "main.png"
    thumb_path = tmp_path / "captures" / "thumb.png"
    _save_png(image_path, color=(255, 255, 0))
    _save_png(thumb_path, color=(0, 255, 255), size=8)

    await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=str(image_path),
            thumb_path=str(thumb_path),
            width=16,
            height=16,
            hash_sha256="ignored",
        )
    )
    server_record_id = (await db.find_record_by_client_id(rid))["id"]
    shots = await db.get_screenshots_for_record(server_record_id)
    assert shots[0]["thumb_path"]
    assert shots[0]["thumb_path"].startswith("thumbs/")


async def test_submit_screenshot_rejects_relative_path_when_no_data_dir(backend):
    """Without a data_dir context HttpBackend cannot resolve a relative path,
    so it must refuse the call rather than silently misread bytes from CWD."""
    with pytest.raises(BackendError, match="absolute"):
        await backend.submit_screenshot(
            ScreenshotSubmission(
                record_id="any",
                path="relative/x.png",
                thumb_path=None,
                width=1,
                height=1,
                hash_sha256="x",
            )
        )


async def test_submit_screenshot_resolves_relative_path_against_data_dir(
    db, blob_storage, tmp_path
):
    """capture_active_window emits paths *relative* to storage_cfg.data_dir.
    HttpBackend constructed with data_dir must resolve them and upload the
    bytes — otherwise the production capture→http chain breaks at the first
    screenshot.
    """
    # 1) Place a real screenshot under data_dir so capture's output shape is mirrored.
    data_dir = tmp_path / "data"
    rel_path = Path("screenshots/2026/05/15/test.png")
    (data_dir / rel_path).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=(33, 66, 99)).save(data_dir / rel_path)

    app = create_app(db, blob_storage=blob_storage)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        backend = HttpBackend(client=client, data_dir=data_dir)
        rid = await backend.submit_record(_CTX, reason="heartbeat")

        sid = await backend.submit_screenshot(
            ScreenshotSubmission(
                record_id=rid,
                path=str(rel_path),  # relative — exactly what capture emits
                thumb_path=None,
                width=16,
                height=16,
                hash_sha256="ignored",
            )
        )
    assert sid

    server_record_id = (await db.find_record_by_client_id(rid))["id"]
    shots = await db.get_screenshots_for_record(server_record_id)
    assert len(shots) == 1


async def test_submit_screenshot_rejects_path_traversal_outside_data_dir(
    db, blob_storage, tmp_path
):
    """A relative path containing .. that resolves outside data_dir must be
    refused — otherwise an attacker-controlled record could exfiltrate
    arbitrary files from the host."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create a sensitive file outside data_dir
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"OWNED")

    app = create_app(db, blob_storage=blob_storage)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        backend = HttpBackend(client=client, data_dir=data_dir)
        with pytest.raises(BackendError, match="data_dir"):
            await backend.submit_screenshot(
                ScreenshotSubmission(
                    record_id="any",
                    path="../secret.txt",  # escapes data_dir
                    thumb_path=None,
                    width=1,
                    height=1,
                    hash_sha256="x",
                )
            )


# --------------------------------------------------------------------------- #
# close_record                                                                  #
# --------------------------------------------------------------------------- #


async def test_close_record_sets_ts_end(backend, db):
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    server_record_id = (await db.find_record_by_client_id(rid))["id"]

    async with db.conn.execute("SELECT ts_end FROM records WHERE id=?", (server_record_id,)) as cur:
        assert (await cur.fetchone())["ts_end"] is None

    await backend.close_record(rid)

    async with db.conn.execute("SELECT ts_end FROM records WHERE id=?", (server_record_id,)) as cur:
        assert (await cur.fetchone())["ts_end"] is not None


async def test_close_record_after_simulated_restart(db, blob_storage):
    """HttpBackend keeps client_record_id → server_id only in memory. After a
    process restart the map is empty; close_record must still succeed because
    the server route accepts client_record_id as fallback. Otherwise stale
    backends silently no-op closes (server returns 200 with rowcount==0)."""
    app = create_app(db, blob_storage=blob_storage)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_backend = HttpBackend(client=client)
        rid = await first_backend.submit_record(_CTX, reason="heartbeat")
        server_record_id = (await db.find_record_by_client_id(rid))["id"]

        # Simulate a process restart: brand new HttpBackend with empty map.
        fresh_backend = HttpBackend(client=client)
        # The fresh backend has never seen `rid` in its _record_id_for map but
        # still must close the existing record on the server.
        await fresh_backend.close_record(rid)

    async with db.conn.execute("SELECT ts_end FROM records WHERE id=?", (server_record_id,)) as cur:
        assert (await cur.fetchone())["ts_end"] is not None


async def test_close_record_unknown_id_raises(backend):
    """A close against a server id that doesn't exist must propagate as
    BackendError (the server now returns 404). Silent 200s used to mask bugs."""
    with pytest.raises(BackendError, match="404"):
        await backend.close_record("definitely-not-a-real-record-id")


# --------------------------------------------------------------------------- #
# mark_pending — no-op on HttpBackend (server already pends inside ingest)     #
# --------------------------------------------------------------------------- #


async def test_mark_pending_is_a_noop(backend, db):
    """Calling mark_pending after ingest must not error and must not double-pend.
    Ingest itself already calls db.mark_pending; HttpBackend's mark_pending is a
    no-op so capture's redundant call is free.
    """
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    await backend.mark_pending(rid)  # must not raise

    # Worker can still claim exactly once
    task = await db.claim_next_task("pending_vlm")
    assert task is not None
    second_claim = await db.claim_next_task("pending_vlm")
    assert second_claim is None


# --------------------------------------------------------------------------- #
# Surface lifecycle                                                             #
# --------------------------------------------------------------------------- #


async def test_owned_client_aclose_is_safe(tmp_path):
    """When constructed with base_url, HttpBackend owns the AsyncClient and
    aclose() must shut it down cleanly without raising."""
    backend = HttpBackend(base_url="http://127.0.0.1:9999")
    await backend.aclose()


async def test_device_id_header_added_when_set(db, blob_storage):
    """HttpBackend(device_id=...) must include X-Device-Id on every request, even
    when the AsyncClient was supplied externally (test path)."""
    captured: list[str | None] = []
    base_app = create_app(db, blob_storage=blob_storage)

    @base_app.middleware("http")
    async def _capture_device_header(request, call_next):  # noqa: ANN001
        captured.append(request.headers.get("x-device-id"))
        return await call_next(request)

    transport = httpx.ASGITransport(app=base_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        backend = HttpBackend(client=raw, device_id="57b81d95-8c0d-41d8-8e56-ef116904661c")
        await backend.submit_record(_CTX, reason="heartbeat")

    assert any(h == "57b81d95-8c0d-41d8-8e56-ef116904661c" for h in captured)


async def test_no_device_id_header_when_unset(db, blob_storage):
    captured: list[str | None] = []
    base_app = create_app(db, blob_storage=blob_storage)

    @base_app.middleware("http")
    async def _capture_device_header(request, call_next):  # noqa: ANN001
        captured.append(request.headers.get("x-device-id"))
        return await call_next(request)

    transport = httpx.ASGITransport(app=base_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        backend = HttpBackend(client=raw)
        await backend.submit_record(_CTX, reason="heartbeat")

    assert all(h is None for h in captured)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"device_name": "n" * 129}, "name"),
        ({"device_description": "d" * 513}, "description"),
        ({"client_version": "v" * 65}, "client_version"),
        ({"capabilities": ("INVALID CAPABILITY",)}, "capabilities"),
    ],
)
def test_http_backend_rejects_unsendable_device_metadata_before_network(kwargs, field):
    with pytest.raises(ValueError, match="invalid HttpBackend device metadata") as exc_info:
        HttpBackend(
            base_url="http://test", device_id="57b81d95-8c0d-41d8-8e56-ef116904661c", **kwargs
        )
    assert exc_info.value.__cause__ is not None
    assert field in str(exc_info.value.__cause__)


async def test_borrowed_client_aclose_is_noop(backend):
    """When the AsyncClient comes from outside (test harness, app composer)
    HttpBackend must not close it on aclose() — that's the caller's job."""
    await backend.aclose()
    # The fixture's `async with httpx.AsyncClient` will close it after — no
    # double-close error is what we're verifying here.


async def test_borrowed_client_includes_auth_token_per_request(db, blob_storage):
    """HttpBackend(client=raw, auth_token=...) used to silently drop the
    auth_token because Authorization was only set when constructing an owned
    client. Borrowed-client tests would then auth-bypass against a server
    that requires Bearer — masking real auth misconfiguration in production.
    """
    captured: list[str | None] = []
    base_app = create_app(db, blob_storage=blob_storage)

    @base_app.middleware("http")
    async def _capture_auth(request, call_next):  # noqa: ANN001
        captured.append(request.headers.get("authorization"))
        return await call_next(request)

    transport = httpx.ASGITransport(app=base_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        backend = HttpBackend(client=raw, auth_token="tt_live_secret123")
        await backend.submit_record(_CTX, reason="heartbeat")

    assert any(h == "Bearer tt_live_secret123" for h in captured)


async def test_no_authorization_header_when_token_unset(db, blob_storage):
    captured: list[str | None] = []
    base_app = create_app(db, blob_storage=blob_storage)

    @base_app.middleware("http")
    async def _capture_auth(request, call_next):  # noqa: ANN001
        captured.append(request.headers.get("authorization"))
        return await call_next(request)

    transport = httpx.ASGITransport(app=base_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        backend = HttpBackend(client=raw)
        await backend.submit_record(_CTX, reason="heartbeat")

    assert all(h is None for h in captured)


def _img_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    return buf.getvalue()
