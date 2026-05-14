"""Tests for InProcessBackend — capture's view of the in-process backend.

The Protocol surface is small (5 methods); these tests pin down that each
forwards correctly to Database and that submit_screenshot keeps the pHash
side-index in sync without the capture loop having to know about it.
"""

from __future__ import annotations

import pytest

from timetrace.client.core.backend import InProcessBackend
from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.common.phash_hash import phash_from_blob
from timetrace.common.protocol import ScreenshotSubmission
from timetrace.server.phash_index.index import PHashIndex
from timetrace.server.storage.database import Database


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def phash_index() -> PHashIndex:
    return PHashIndex()


@pytest.fixture
def backend(db, phash_index) -> InProcessBackend:
    return InProcessBackend(db, phash_index=phash_index)


_CTX = CaptureContext(app_name="VSCode", process_name="code", window_title="main.py")


# --------------------------------------------------------------------------- #
# submit_record + close_record                                                  #
# --------------------------------------------------------------------------- #


async def test_submit_record_creates_row_with_metadata(backend, db):
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    rows = await db.query_records(0, 9_999_999_999_999)
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["app_name"] == "VSCode"
    assert rows[0]["window_title"] == "main.py"
    assert rows[0]["capture_reason"] == "heartbeat"


async def test_close_record_sets_ts_end(backend, db):
    rid = await backend.submit_record(_CTX, reason="switch", event_type="window_switch")
    await backend.close_record(rid)
    async with db.conn.execute("SELECT ts_end FROM records WHERE id=?", (rid,)) as cur:
        row = await cur.fetchone()
    assert row["ts_end"] is not None


# --------------------------------------------------------------------------- #
# submit_screenshot                                                             #
# --------------------------------------------------------------------------- #


async def test_submit_screenshot_persists_blob_and_metadata(backend, db):
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    sid = await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path="screenshots/2026/05/15/x.png",
            thumb_path="thumbs/2026/05/15/x.jpg",
            width=1920,
            height=1080,
            hash_sha256="abc",
            phash=0xDEADBEEFCAFEBABE,
        )
    )
    shots = await db.get_screenshots_for_record(rid)
    assert len(shots) == 1
    assert shots[0]["id"] == sid
    assert shots[0]["width"] == 1920
    assert shots[0]["hash_sha256"] == "abc"

    # phash blob is excluded from get_screenshots_for_record's column list, so
    # query the raw column directly to verify the encoding round-trip.
    async with db.conn.execute("SELECT phash FROM screenshots WHERE id=?", (sid,)) as cur:
        row = await cur.fetchone()
    assert phash_from_blob(row["phash"]) == 0xDEADBEEFCAFEBABE


async def test_submit_screenshot_inserts_into_phash_index(backend, phash_index, db):
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    sid = await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path="x.png",
            thumb_path=None,
            width=1,
            height=1,
            hash_sha256="h",
            phash=0xAAAA,
        )
    )

    hits = phash_index.search(0xAAAA, radius=0)
    assert len(hits) == 1
    distance, value = hits[0]
    assert distance == 0
    assert value == sid


async def test_submit_screenshot_with_no_phash_skips_index(backend, phash_index):
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path="x.png",
            thumb_path=None,
            width=1,
            height=1,
            hash_sha256="h",
            phash=None,
        )
    )
    assert len(phash_index) == 0


async def test_submit_screenshot_without_phash_index_still_persists(db):
    """Backend without a phash index must still write the screenshot row."""
    backend = InProcessBackend(db, phash_index=None)
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    sid = await backend.submit_screenshot(
        ScreenshotSubmission(
            record_id=rid,
            path="x.png",
            thumb_path=None,
            width=1,
            height=1,
            hash_sha256="h",
            phash=0xBEEF,  # would be indexed if there were an index
        )
    )
    shots = await db.get_screenshots_for_record(rid)
    assert len(shots) == 1
    assert shots[0]["id"] == sid


# --------------------------------------------------------------------------- #
# mark_pending                                                                  #
# --------------------------------------------------------------------------- #


async def test_mark_pending_creates_analysis_task(backend, db):
    rid = await backend.submit_record(_CTX, reason="heartbeat")
    await backend.mark_pending(rid)
    task = await db.claim_next_task("pending_vlm")
    assert task is not None
    assert task["record_id"] == rid
