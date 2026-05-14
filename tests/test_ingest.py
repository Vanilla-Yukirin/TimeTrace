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


async def test_ingest_replay_with_image_does_not_double_write(db, blob_storage, tmp_path):
    """Second submission must not create a second screenshot row for the same
    client_record_id, even when image bytes are re-sent."""
    app = create_app(db, blob_storage=blob_storage)
    image_data = _png_bytes()
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
    assert first["was_new"] is True and first["screenshot_id"]
    assert second["was_new"] is False
    assert second["screenshot_id"] is None  # second response advertises no new shot

    shots = await db.get_screenshots_for_record(first["record_id"])
    assert len(shots) == 1


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
