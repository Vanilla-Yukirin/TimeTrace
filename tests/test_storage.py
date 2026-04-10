"""Tests for the Storage/Database layer."""

import pytest

from timetrace.config import StorageConfig
from timetrace.storage.database import Database
from timetrace.storage.models import CaptureContext


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


async def test_insert_and_query_record(db):
    ctx = CaptureContext(app_name="VSCode", process_name="code", window_title="main.py")
    record_id = await db.insert_record(ctx, reason="heartbeat")
    assert record_id

    rows = await db.query_records(0, 9_999_999_999_999)
    assert len(rows) == 1
    assert rows[0]["app_name"] == "VSCode"


async def test_mark_pending_creates_analysis_row(db):
    ctx = CaptureContext(app_name="Chrome", process_name="chrome", window_title="Google")
    record_id = await db.insert_record(ctx, reason="switch")
    await db.mark_pending(record_id)

    task = await db.claim_next_task("pending_vlm")
    assert task is not None
    assert task["record_id"] == record_id


async def test_claim_next_task_returns_none_when_empty(db):
    task = await db.claim_next_task("pending_vlm")
    assert task is None


async def test_save_description_and_transition(db):
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(record_id)
    await db.claim_next_task("pending_vlm")

    await db.save_description(record_id, "A test description")
    await db.transition(record_id, "vlm_done")

    rows = await db.query_records(0, 9_999_999_999_999)
    assert rows[0]["status"] == "vlm_done"


async def test_mark_error_final(db):
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(record_id)
    await db.claim_next_task("pending_vlm")
    await db.mark_error_final(record_id, "model unavailable")

    async with db.conn.execute(
        "SELECT status, error_msg FROM analysis_results WHERE record_id=?", (record_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "error_final"
    assert row["error_msg"] == "model unavailable"
