"""End-to-end tests for HttpBackend driving the in-process FastAPI app.

We spin up a real `create_app(...)` instance and bind an `httpx.AsyncClient`
to its ASGI transport. That keeps the test pure-async (no socket, no
TestClient threading) while still exercising the multipart codec, the
ingest route, the close route, the BackendClient Protocol mapping, and
the database round-trip in one shot.
"""

from __future__ import annotations

import io

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


async def test_submit_screenshot_rejects_relative_path(backend):
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


async def test_borrowed_client_aclose_is_noop(backend):
    """When the AsyncClient comes from outside (test harness, app composer)
    HttpBackend must not close it on aclose() — that's the caller's job."""
    await backend.aclose()
    # The fixture's `async with httpx.AsyncClient` will close it after — no
    # double-close error is what we're verifying here.


def _img_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG")
    return buf.getvalue()
