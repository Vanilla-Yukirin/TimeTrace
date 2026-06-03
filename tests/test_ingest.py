"""Tests for POST /v1/ingest/record (HttpBackend's primary endpoint)."""

from __future__ import annotations

import hashlib
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from timetrace.common.config import StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.phash_index.index import PHashIndex
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


def _png_bytes(color: tuple[int, int, int] = (10, 20, 30), size: int = 16) -> bytes:
    img = Image.new("RGB", (size, size), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _record_payload(client_record_id: str, **overrides) -> str:
    base = {
        "client_record_id": client_record_id,
        "ts_start": 1747300000000,  # arbitrary epoch ms in 2025
        "app_name": "VSCode",
        "process_name": "code",
        "window_title": "main.py",
        "capture_reason": "switch",
        "event_type": "window_switch",
    }
    base.update(overrides)
    return json.dumps(base)


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #


async def test_ingest_missing_record_returns_422(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        resp = client.post("/v1/ingest/record")
    assert resp.status_code == 422


async def test_ingest_bad_json_record_returns_422(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        resp = client.post("/v1/ingest/record", data={"record": "not json"})
    assert resp.status_code == 422


async def test_ingest_missing_client_record_id_returns_422(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    payload = json.dumps({"ts_start": 1, "app_name": "x"})  # no client_record_id
    with TestClient(app) as client:
        resp = client.post("/v1/ingest/record", data={"record": payload})
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Record-only path                                                             #
# --------------------------------------------------------------------------- #


async def test_ingest_record_only_persists_metadata(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest/record",
            data={"record": _record_payload("client-1")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["was_new"] is True
    assert body["screenshot_id"] is None
    assert body["record_id"]

    # Stored row carries the right client_record_id + metadata
    found = await db.find_record_by_client_id("client-1")
    assert found is not None
    assert found["app_name"] == "VSCode"
    assert found["window_title"] == "main.py"


async def test_ingest_record_honors_client_ts_start(db, blob_storage):
    """Wire contract: the server does not rewrite ts_start — a record's stored
    ts_start must equal the client's capture clock, not the server-receive time.

    Regression: a backed-up outbox drain used to restamp records with
    server-now, so ts_end (the client's close clock) landed *before* ts_start
    and every replayed record reported a negative duration.
    """
    app = create_app(db, blob_storage=blob_storage)
    capture_ts = 1747300000000  # fixed 2025 epoch ms, far from the server's "now"
    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest/record",
            data={"record": _record_payload("client-ts", ts_start=capture_ts)},
        )
    assert resp.status_code == 200
    found = await db.find_record_by_client_id("client-ts")
    assert found is not None
    assert found["ts_start"] == capture_ts


# --------------------------------------------------------------------------- #
# With image + thumb                                                           #
# --------------------------------------------------------------------------- #


async def test_ingest_with_image_writes_blob_and_screenshot_row(db, blob_storage, tmp_path):
    app = create_app(db, blob_storage=blob_storage)

    image_data = _png_bytes((255, 0, 0))
    thumb_data = _png_bytes((0, 255, 0), size=8)

    payload = _record_payload(
        "client-img",
        image_md5=_md5(image_data),
        image_width=16,
        image_height=16,
        image_format="png",
        thumb_md5=_md5(thumb_data),
        thumb_format="png",
    )

    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest/record",
            data={"record": payload},
            files={
                "image": ("img.png", image_data, "image/png"),
                "thumb": ("thumb.png", thumb_data, "image/png"),
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["was_new"] is True
    assert body["screenshot_id"]

    # Blob storage actually wrote both files
    keys = list((tmp_path / "blobs").rglob("*"))
    paths = [str(p.relative_to(tmp_path / "blobs")) for p in keys if p.is_file()]
    assert any("screenshots" in p and p.endswith(".png") for p in paths)
    assert any("thumbs" in p and p.endswith(".png") for p in paths)

    # Screenshot row exists and points at the stored path
    shots = await db.get_screenshots_for_record(body["record_id"])
    assert len(shots) == 1
    assert shots[0]["path"].startswith("screenshots/")


async def test_ingest_image_md5_mismatch_returns_400(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    image_data = _png_bytes()
    payload = _record_payload(
        "client-bad-md5",
        image_md5="0" * 32,  # wrong
        image_width=16,
        image_height=16,
    )
    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest/record",
            data={"record": payload},
            files={"image": ("img.png", image_data, "image/png")},
        )
    assert resp.status_code == 400
    assert "md5" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# Idempotency                                                                  #
# --------------------------------------------------------------------------- #


async def test_ingest_replay_returns_was_new_false_with_same_record_id(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        first = client.post(
            "/v1/ingest/record", data={"record": _record_payload("client-replay")}
        ).json()
        second = client.post(
            "/v1/ingest/record", data={"record": _record_payload("client-replay")}
        ).json()
    assert first["was_new"] is True
    assert second["was_new"] is False
    assert second["record_id"] == first["record_id"]
    assert second["screenshot_id"] is None


async def test_ingest_screenshot_attach_after_record_only(db, blob_storage):
    """HttpBackend pattern: first POST is record-only, second POST adds the image
    against the same client_record_id. The second call must attach the screenshot
    to the existing record rather than dropping it.
    """
    app = create_app(db, blob_storage=blob_storage)
    image_data = _png_bytes()

    record_only = _record_payload("client-two-stage")
    record_with_image = _record_payload(
        "client-two-stage",
        image_md5=_md5(image_data),
        image_width=16,
        image_height=16,
    )

    with TestClient(app) as client:
        first = client.post("/v1/ingest/record", data={"record": record_only}).json()
        second = client.post(
            "/v1/ingest/record",
            data={"record": record_with_image},
            files={"image": ("a.png", image_data, "image/png")},
        ).json()

    assert first["was_new"] is True
    assert first["screenshot_id"] is None
    assert second["was_new"] is False
    assert second["record_id"] == first["record_id"]
    assert second["screenshot_id"]  # attached on the second call

    shots = await db.get_screenshots_for_record(first["record_id"])
    assert len(shots) == 1
    assert shots[0]["id"] == second["screenshot_id"]


# --------------------------------------------------------------------------- #
# Screenshot-after-skip re-enqueue (metadata-arrives-first race fix)           #
# --------------------------------------------------------------------------- #


async def test_ingest_screenshot_requeues_skipped_no_image_record(db, blob_storage):
    """Race fix: metadata arrives first, the 1s-poll worker claims the record and
    short-circuits it to vlm_done with no image (worker.vlm_skipped_no_image),
    THEN the screenshot lands. The screenshot ingest must re-enqueue the record to
    pending_vlm so the worker re-describes it WITH the image — otherwise the
    screenshot is orphaned and never classified (the 3508-record prod bug).
    """
    app = create_app(db, blob_storage=blob_storage)
    image_data = _png_bytes()
    record_with_image = _record_payload(
        "client-race", image_md5=_md5(image_data), image_width=16, image_height=16
    )

    with TestClient(app) as client:
        first = client.post(
            "/v1/ingest/record", data={"record": _record_payload("client-race")}
        ).json()
        rid = first["record_id"]

        # Worker grabs it before the screenshot and skips (no image) → vlm_done,
        # NULL vlm_desc. Mirrors loop.py's short-circuit exactly.
        await db.transition(rid, "vlm_done")
        assert await db.claim_next_task("pending_vlm") is None  # nothing queued now

        # Screenshot finally arrives (second call, same client_record_id).
        client.post(
            "/v1/ingest/record",
            data={"record": record_with_image},
            files={"image": ("a.png", image_data, "image/png")},
        )

    # Re-enqueued: the worker can claim it again, now with the screenshot attached.
    task = await db.claim_next_task("pending_vlm")
    assert task is not None
    assert task["record_id"] == rid
    shots = await db.get_screenshots_for_record(rid)
    assert len(shots) == 1


async def test_ingest_screenshot_does_not_requeue_described_record(db, blob_storage):
    """The re-enqueue is gated on vlm_desc IS NULL: a record the worker already
    described (success path writes vlm_desc before vlm_done) must NOT be disturbed
    when a replayed screenshot arrives, or we'd clobber a finished result.
    """
    app = create_app(db, blob_storage=blob_storage)
    image_data = _png_bytes()
    record_with_image = _record_payload(
        "client-described", image_md5=_md5(image_data), image_width=16, image_height=16
    )

    with TestClient(app) as client:
        first = client.post(
            "/v1/ingest/record", data={"record": _record_payload("client-described")}
        ).json()
        rid = first["record_id"]
        # Worker genuinely processed it: description written, then vlm_done.
        await db.save_description(rid, "VSCode 编辑器，main.py")
        await db.transition(rid, "vlm_done")

        client.post(
            "/v1/ingest/record",
            data={"record": record_with_image},
            files={"image": ("a.png", image_data, "image/png")},
        )

    # Not re-enqueued — stays vlm_done, nothing claimable.
    assert await db.claim_next_task("pending_vlm") is None


# --------------------------------------------------------------------------- #
# pHash side-index                                                             #
# --------------------------------------------------------------------------- #


async def test_ingest_image_phash_lands_in_index(db, blob_storage):
    index = PHashIndex()
    app = create_app(db, blob_storage=blob_storage, phash_index=index)

    image_data = _png_bytes()
    payload = _record_payload(
        "client-phash",
        image_md5=_md5(image_data),
        image_width=16,
        image_height=16,
        image_phash=0x12345678ABCD0000,
    )
    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest/record",
            data={"record": payload},
            files={"image": ("a.png", image_data, "image/png")},
        )
    sid = resp.json()["screenshot_id"]
    hits = index.search(0x12345678ABCD0000, radius=0)
    assert len(hits) == 1 and hits[0][1] == sid


async def test_ingest_marks_record_pending_for_worker(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        client.post("/v1/ingest/record", data={"record": _record_payload("client-pending")})

    task = await db.claim_next_task("pending_vlm")
    assert task is not None  # worker would pick this up


# --------------------------------------------------------------------------- #
# At-least-once replay (outbox crashes mid-ack)                                #
# --------------------------------------------------------------------------- #


async def test_ingest_replay_with_image_does_not_duplicate_screenshot(db, blob_storage):
    """Outbox is at-least-once: if the sender successfully POSTs but crashes
    before ack_next, the same (record + image) entry is re-sent. The server
    must NOT insert a second screenshots row for the same (record_id, sha256)
    or the phash_index will get a duplicate, doubling visual-search hits.
    """
    app = create_app(db, blob_storage=blob_storage)
    image_data = _png_bytes((10, 20, 30))
    payload = _record_payload(
        "client-replay-img",
        image_md5=_md5(image_data),
        image_width=16,
        image_height=16,
    )

    with TestClient(app) as client:
        first = client.post(
            "/v1/ingest/record",
            data={"record": payload},
            files={"image": ("a.png", image_data, "image/png")},
        ).json()
        second = client.post(
            "/v1/ingest/record",
            data={"record": payload},
            files={"image": ("a.png", image_data, "image/png")},
        ).json()

    assert first["was_new"] is True
    assert second["was_new"] is False
    assert second["record_id"] == first["record_id"]
    # Critical: same screenshot id is echoed back — no second insert happened.
    assert second["screenshot_id"] == first["screenshot_id"]

    shots = await db.get_screenshots_for_record(first["record_id"])
    assert len(shots) == 1


# --------------------------------------------------------------------------- #
# /v1/ingest/record/{id}/close                                                 #
# --------------------------------------------------------------------------- #


async def test_close_record_unknown_id_returns_404(db, blob_storage):
    """Closing an id the server has never seen must surface as 404 — silently
    returning 200 hides the very real possibility that an HttpBackend restart
    left the client routing closes against ids that have no matching row."""
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        resp = client.post("/v1/ingest/record/no-such-id/close", json={"ts_end": 1})
    assert resp.status_code == 404


async def test_close_record_known_id_response_echoes_supplied_ts_end(db, blob_storage):
    """When ts_end is provided the response must echo it exactly — no silent
    fallback to server clock that would mask client/server drift."""
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        first = client.post(
            "/v1/ingest/record", data={"record": _record_payload("client-close-echo")}
        ).json()
        resp = client.post(
            f"/v1/ingest/record/{first['record_id']}/close",
            json={"ts_end": 1747300050000},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ts_end"] == 1747300050000


async def test_close_record_accepts_client_record_id(db, blob_storage):
    """HttpBackend can recover from a process restart only if the close endpoint
    accepts the client_record_id directly (the only id the client always
    holds). Without this, a client losing its in-memory server-id map closes
    nothing on the server even though the route returns 200 — silent failure.
    """
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        first = client.post(
            "/v1/ingest/record", data={"record": _record_payload("client-by-crid")}
        ).json()
        # Close by client_record_id — not the server id we got back.
        resp = client.post(
            "/v1/ingest/record/client-by-crid/close",
            json={"ts_end": 1747300099000},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["record_id"] == first["record_id"]
    assert body["ts_end"] == 1747300099000

    async with db.conn.execute(
        "SELECT ts_end FROM records WHERE id=?", (first["record_id"],)
    ) as cur:
        row = await cur.fetchone()
    assert row["ts_end"] == 1747300099000


# --------------------------------------------------------------------------- #
# Cross-midnight date-bucket bug                                               #
# --------------------------------------------------------------------------- #


async def test_screenshot_blob_uses_record_ts_start_not_upload_time(db, blob_storage, tmp_path):
    """When a screenshot is uploaded long after capture (outbox flush after
    offline period, or late-night activity uploaded past midnight), the blob
    path must reflect the *record's* date — not the upload date — so adjacent
    activity stays in the same date bucket on disk.
    """
    app = create_app(db, blob_storage=blob_storage)

    # 1) Create record-only via ingest, then backdate it on the server side.
    record_only = _record_payload("client-cross-midnight")
    with TestClient(app) as client:
        first = client.post("/v1/ingest/record", data={"record": record_only}).json()
        rid = first["record_id"]

        # 2024-03-15 12:00:00 UTC — well in the past so it can't collide with "now".
        backdate_ms = 1710504000000
        await db.conn.execute("UPDATE records SET ts_start=? WHERE id=?", (backdate_ms, rid))
        await db.conn.commit()

        # 2) Upload screenshot bytes; payload.ts_start is "now" (current run time).
        image_data = _png_bytes((50, 100, 150))
        upload_payload = _record_payload(
            "client-cross-midnight",
            image_md5=_md5(image_data),
            image_width=16,
            image_height=16,
            # ts_start defaults to ~2025 in _record_payload; explicitly use "now" to
            # match what HttpBackend.submit_screenshot would actually send.
            ts_start=int(__import__("time").time() * 1000),
        )
        client.post(
            "/v1/ingest/record",
            data={"record": upload_payload},
            files={"image": ("a.png", image_data, "image/png")},
        )

    shots = await db.get_screenshots_for_record(rid)
    assert len(shots) == 1
    assert "2024/03/15" in shots[0]["path"], (
        f"screenshot path {shots[0]['path']!r} does not reflect record's "
        "backdated ts_start (2024-03-15) — bucketed by upload time instead"
    )


async def test_ingest_replay_with_image_does_not_duplicate_phash_index(db, blob_storage):
    """Same setup as the screenshot dedup test, but verifies the phash_index
    isn't double-inserted on outbox at-least-once replay."""
    index = PHashIndex()
    app = create_app(db, blob_storage=blob_storage, phash_index=index)

    image_data = _png_bytes((40, 50, 60))
    phash_val = 0xDEADBEEFCAFEBABE
    payload = _record_payload(
        "client-replay-phash",
        image_md5=_md5(image_data),
        image_width=16,
        image_height=16,
        image_phash=phash_val,
    )

    with TestClient(app) as client:
        client.post(
            "/v1/ingest/record",
            data={"record": payload},
            files={"image": ("a.png", image_data, "image/png")},
        )
        client.post(
            "/v1/ingest/record",
            data={"record": payload},
            files={"image": ("a.png", image_data, "image/png")},
        )

    hits = index.search(phash_val, radius=0)
    assert len(hits) == 1, f"phash_index has duplicate hits after replay: {hits!r}"
