"""Tests for OutboxBackend (BackendClient over Outbox) + the HTTP sender adapter.

These cover the queue-side semantics (what OutboxBackend writes to the outbox)
and the drain-side adapter (`make_http_sender` translating each entry kind to
the right HttpBackend call). End-to-end (capture→outbox→sender→server→DB) is
covered separately by tests/test_two_process_smoke.py.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from PIL import Image

from timetrace.client.core.backend import BackendError, HttpBackend
from timetrace.client.core.outbox import Outbox, OutboxEntry
from timetrace.client.core.outbox_backend import OutboxBackend, make_http_sender
from timetrace.client.core.outbox_sender import OutboxSender
from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.common.protocol import ScreenshotSubmission
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.storage.blob import LocalBlobStorage


@pytest.fixture
def outbox(tmp_path) -> Outbox:
    return Outbox(tmp_path / "outbox")


_CTX = CaptureContext(
    app_name="VSCode",
    process_name="code.exe",
    window_title="main.py",
    url=None,
)


def _save_png(path, color=(10, 20, 30), size=16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), color=color).save(path)


# --------------------------------------------------------------------------- #
# OutboxBackend.submit_record                                                  #
# --------------------------------------------------------------------------- #


async def test_submit_record_appends_ingest_entry(outbox):
    backend = OutboxBackend(outbox)
    rid = await backend.submit_record(_CTX, reason="switch", event_type="window_switch")
    assert rid

    entries = [e async for e in outbox.iter_pending()]
    assert len(entries) == 1
    payload = entries[0].payload
    assert payload["kind"] == "ingest"
    assert payload["client_record_id"] == rid
    assert payload["app_name"] == "VSCode"
    assert payload["capture_reason"] == "switch"
    assert payload["event_type"] == "window_switch"
    # No image bytes for record-only ingest
    assert entries[0].image_bytes is None
    assert entries[0].thumb_bytes is None


async def test_submit_record_preserves_explicit_observation_time(outbox):
    observed_ms = 1_747_300_000_000
    backend = OutboxBackend(outbox)
    await backend.submit_record(
        _CTX,
        reason="heartbeat",
        ts_start_ms=observed_ms,
    )

    entries = [entry async for entry in outbox.iter_pending()]
    assert entries[0].payload["ts_start"] == observed_ms


async def test_submit_record_returns_unique_client_record_ids(outbox):
    backend = OutboxBackend(outbox)
    a = await backend.submit_record(_CTX, reason="heartbeat")
    b = await backend.submit_record(_CTX, reason="heartbeat")
    assert a != b


# --------------------------------------------------------------------------- #
# OutboxBackend.submit_screenshot                                              #
# --------------------------------------------------------------------------- #


async def test_submit_screenshot_inlines_image_bytes(outbox, tmp_path):
    backend = OutboxBackend(outbox)
    rid = await backend.submit_record(_CTX, reason="heartbeat")

    img_path = tmp_path / "captures" / "x.png"
    _save_png(img_path, color=(255, 0, 0))

    sid = await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=str(img_path),
            thumb_path=None,
            width=16,
            height=16,
            hash_sha256="ignored",
            phash=0xDEAD,
        )
    )
    # OutboxBackend defers — real screenshot id is server-side, not knowable yet
    assert sid is None

    entries = [e async for e in outbox.iter_pending()]
    # 1 record-only + 1 record-with-image
    assert len(entries) == 2
    img_entry = entries[1]
    assert img_entry.payload["kind"] == "ingest"
    assert img_entry.payload["client_record_id"] == rid
    assert img_entry.payload["image_width"] == 16
    assert img_entry.payload["image_phash"] == 0xDEAD
    assert img_entry.image_bytes == img_path.read_bytes()
    assert img_entry.thumb_bytes is None


async def test_submit_screenshot_resolves_relative_path_against_data_dir(outbox, tmp_path):
    """Capture emits paths relative to data_dir; OutboxBackend(data_dir=...)
    must read them just like HttpBackend does."""
    data_dir = tmp_path / "data"
    rel = "screenshots/2026/05/15/test.png"
    _save_png(data_dir / rel, color=(50, 100, 150))

    backend = OutboxBackend(outbox, data_dir=data_dir)
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=rel,  # relative
            thumb_path=None,
            width=16,
            height=16,
            hash_sha256="ignored",
        )
    )

    entries = [e async for e in outbox.iter_pending()]
    assert entries[1].image_bytes == (data_dir / rel).read_bytes()


async def test_submit_screenshot_relative_path_without_data_dir_raises(outbox, tmp_path):
    backend = OutboxBackend(outbox)  # no data_dir
    with pytest.raises(BackendError, match="data_dir"):
        await backend.submit_screenshot(
            ScreenshotSubmission(
                record_id="any",
                path="rel/x.png",
                thumb_path=None,
                width=1,
                height=1,
                hash_sha256="x",
            )
        )


async def test_submit_screenshot_rejects_path_traversal_outside_data_dir(outbox, tmp_path):
    """Defence-in-depth: a relative path that resolve()s outside data_dir
    must be refused, mirroring HttpBackend's guard. Same threat model:
    a malicious ScreenshotSubmission could otherwise read host secrets
    via the upload pipeline."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Sensitive file outside data_dir
    (tmp_path / "secret.txt").write_bytes(b"OWNED")

    backend = OutboxBackend(outbox, data_dir=data_dir)
    with pytest.raises(BackendError, match="data_dir"):
        await backend.submit_screenshot(
            ScreenshotSubmission(
                record_id="any",
                path="../secret.txt",
                thumb_path=None,
                width=1,
                height=1,
                hash_sha256="x",
            )
        )


async def test_submit_screenshot_carries_thumb_bytes_when_present(outbox, tmp_path):
    backend = OutboxBackend(outbox)
    rid = await backend.submit_record(_CTX, reason="heartbeat")

    img = tmp_path / "captures" / "main.png"
    thumb = tmp_path / "captures" / "thumb.jpg"
    _save_png(img, color=(0, 255, 0))
    _save_png(thumb, color=(0, 0, 255), size=8)
    # Save thumb as JPEG so the suffix is honored as format.
    Image.new("RGB", (8, 8), (0, 0, 255)).save(thumb)

    await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=str(img),
            thumb_path=str(thumb),
            width=16,
            height=16,
            hash_sha256="ignored",
        )
    )

    entries = [e async for e in outbox.iter_pending()]
    img_entry = entries[1]
    assert img_entry.thumb_bytes == thumb.read_bytes()
    assert img_entry.payload["thumb_format"] == "jpg"


# --------------------------------------------------------------------------- #
# OutboxBackend.close_record                                                   #
# --------------------------------------------------------------------------- #


async def test_close_record_appends_close_entry_with_ts_end(outbox):
    backend = OutboxBackend(outbox)
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    await backend.close_record(rid)

    entries = [e async for e in outbox.iter_pending()]
    close_entry = entries[1]
    assert close_entry.payload["kind"] == "close"
    assert close_entry.payload["client_record_id"] == rid
    assert isinstance(close_entry.payload["ts_end"], int)
    assert close_entry.payload["ts_end"] > 0


async def test_close_record_preserves_explicit_last_observed_time(outbox):
    backend = OutboxBackend(outbox)
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    await backend.close_record(rid, ts_end_ms=1_747_300_050_000)

    entries = [e async for e in outbox.iter_pending()]
    assert entries[1].payload["ts_end"] == 1_747_300_050_000


# --------------------------------------------------------------------------- #
# OutboxBackend.mark_pending                                                   #
# --------------------------------------------------------------------------- #


async def test_mark_pending_does_not_append(outbox):
    """mark_pending is a no-op — server's ingest already pends every record."""
    backend = OutboxBackend(outbox)
    await backend.mark_pending("any-id")
    assert await outbox.pending_count() == 0


# --------------------------------------------------------------------------- #
# make_http_sender adapter                                                     #
# --------------------------------------------------------------------------- #


class _StubBackend:
    """Captures every public call so we can assert dispatch shape without HTTP."""

    def __init__(self) -> None:
        self.posts: list[tuple[dict, bytes | None, bytes | None]] = []
        self.closes: list[tuple[str, int]] = []

    async def post_ingest(
        self,
        payload: dict,
        *,
        image_bytes: bytes | None = None,
        thumb_bytes: bytes | None = None,
    ) -> dict:
        self.posts.append((payload, image_bytes, thumb_bytes))
        return {"record_id": "srv-1", "screenshot_id": None, "was_new": True}

    async def post_close(self, record_id: str, ts_end: int) -> None:
        self.closes.append((record_id, ts_end))


async def test_sender_dispatches_ingest_kind_to_post_ingest():
    stub = _StubBackend()
    send = make_http_sender(stub)  # type: ignore[arg-type]

    entry = OutboxEntry(
        entry_id="e1",
        timestamp_ms=1,
        payload={"kind": "ingest", "client_record_id": "abc", "ts_start": 100},
        image_bytes=b"IMG",
        thumb_bytes=b"TH",
    )
    await send(entry)

    assert len(stub.posts) == 1
    payload, img, thumb = stub.posts[0]
    assert "kind" not in payload  # stripped before posting
    assert payload["client_record_id"] == "abc"
    assert img == b"IMG"
    assert thumb == b"TH"
    assert stub.closes == []


async def test_sender_dispatches_close_kind_to_post_close():
    stub = _StubBackend()
    send = make_http_sender(stub)  # type: ignore[arg-type]

    entry = OutboxEntry(
        entry_id="e2",
        timestamp_ms=2,
        payload={"kind": "close", "client_record_id": "abc", "ts_end": 999},
        image_bytes=None,
        thumb_bytes=None,
    )
    await send(entry)

    assert stub.closes == [("abc", 999)]
    assert stub.posts == []


async def test_sender_unknown_kind_raises():
    stub = _StubBackend()
    send = make_http_sender(stub)  # type: ignore[arg-type]
    entry = OutboxEntry(
        entry_id="bad",
        timestamp_ms=0,
        payload={"kind": "garbage"},
        image_bytes=None,
        thumb_bytes=None,
    )
    with pytest.raises(ValueError, match="unknown entry kind"):
        await send(entry)


# --------------------------------------------------------------------------- #
# Integration: OutboxBackend → OutboxSender → HttpBackend → in-process FastAPI #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def server_db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path / "server-data")
    db = Database(cfg)
    await db.init()
    yield db
    await db.close()


@pytest.fixture
def server_blob(tmp_path):
    return LocalBlobStorage(tmp_path / "server-data" / "blobs")


async def test_outbox_full_drain_round_trip(server_db, server_blob, tmp_path):
    """Full OutboxBackend → OutboxSender → HttpBackend → server chain.

    Capture-side calls submit_record + submit_screenshot + close_record;
    sender drains; server sees one record with the screenshot attached and
    ts_end stamped. Verifies the wire shape end-to-end without subprocesses.
    """
    outbox = Outbox(tmp_path / "outbox")
    backend = OutboxBackend(outbox)

    # Capture-side: queue a complete activity
    rid = await backend.submit_record(_CTX, reason="switch", event_type="window_switch")
    img_path = tmp_path / "captures" / "x.png"
    _save_png(img_path, color=(123, 45, 67))
    await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=str(img_path),
            thumb_path=None,
            width=16,
            height=16,
            hash_sha256="ignored",
            phash=0xCAFEBABE,
        )
    )
    await backend.close_record(rid)
    assert await outbox.pending_count() == 3

    # Send-side: drive HttpBackend against the in-process FastAPI app
    app = create_app(server_db, blob_storage=server_blob)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        http = HttpBackend(client=raw)
        sender = OutboxSender(outbox, make_http_sender(http), idle_poll_interval_s=0.05)

        stop = asyncio.Event()
        task = asyncio.create_task(sender.run(stop))
        # Wait for sender to drain all three entries
        for _ in range(100):
            if await outbox.pending_count() == 0:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await task

    # Server-side: verify one record + one screenshot + ts_end set
    server_record = await server_db.find_record_by_client_id(rid)
    assert server_record is not None
    assert server_record["app_name"] == "VSCode"
    assert server_record["ts_end"] is not None  # close_record was honored

    shots = await server_db.get_screenshots_for_record(server_record["id"])
    assert len(shots) == 1


async def test_outbox_replay_after_sender_restart_does_not_duplicate(
    server_db, server_blob, tmp_path
):
    """Sender process dies mid-batch; new sender restarts and re-sends.
    Server-side dedup (client_record_id UNIQUE + screenshots(record_id, sha256)
    UNIQUE) must keep DB clean."""
    outbox = Outbox(tmp_path / "outbox")
    backend = OutboxBackend(outbox)

    rid = await backend.submit_record(_CTX, reason="heartbeat")
    img_path = tmp_path / "captures" / "y.png"
    _save_png(img_path, color=(99, 88, 77))
    await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=str(img_path),
            thumb_path=None,
            width=16,
            height=16,
            hash_sha256="ignored",
        )
    )

    app = create_app(server_db, blob_storage=server_blob)
    transport = httpx.ASGITransport(app=app)

    # First drain
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        http = HttpBackend(client=raw)
        sender = OutboxSender(outbox, make_http_sender(http), idle_poll_interval_s=0.05)
        stop = asyncio.Event()
        task = asyncio.create_task(sender.run(stop))
        for _ in range(100):
            if await outbox.pending_count() == 0:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await task

    # Simulate replay: append the SAME entries again (mimicking outbox state
    # being re-read after a crash where ack didn't persist)
    fresh_outbox = Outbox(tmp_path / "outbox-replay")
    fresh_backend = OutboxBackend(fresh_outbox)
    # Re-submit same client_record_id
    await fresh_outbox.append(
        {
            "kind": "ingest",
            "client_record_id": rid,
            "ts_start": 100,
            "app_name": "VSCode",
            "process_name": "code.exe",
            "window_title": "main.py",
            "url": None,
            "capture_reason": "heartbeat",
            "event_type": "heartbeat",
        }
    )
    await fresh_backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path=str(img_path),
            thumb_path=None,
            width=16,
            height=16,
            hash_sha256="ignored",
        )
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        http = HttpBackend(client=raw)
        sender = OutboxSender(fresh_outbox, make_http_sender(http), idle_poll_interval_s=0.05)
        stop = asyncio.Event()
        task = asyncio.create_task(sender.run(stop))
        for _ in range(100):
            if await fresh_outbox.pending_count() == 0:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await task

    # Server still has exactly one record + one screenshot for this client_record_id
    server_record = await server_db.find_record_by_client_id(rid)
    shots = await server_db.get_screenshots_for_record(server_record["id"])
    assert len(shots) == 1, f"replay caused screenshot duplication: {shots!r}"
