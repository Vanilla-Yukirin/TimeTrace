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


async def test_insert_screenshot(db):
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")

    screenshot_id = await db.insert_screenshot(
        record_id=record_id,
        path="screenshots/2026/04/11/test.png",
        thumb_path="thumbs/2026/04/11/test.jpg",
        width=1920,
        height=1080,
        hash_sha256="abc123",
    )
    assert screenshot_id

    shots = await db.get_screenshots_for_record(record_id)
    assert len(shots) == 1
    assert shots[0]["width"] == 1920
    assert shots[0]["hash_sha256"] == "abc123"


async def test_insert_feedback(db):
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(record_id)

    feedback_id = await db.insert_feedback(
        record_id=record_id,
        action="edit",
        category_before="uncategorized",
        category_after="work/coding",
    )
    assert feedback_id

    async with db.conn.execute(
        "SELECT action, category_after FROM feedback WHERE id=?", (feedback_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row["action"] == "edit"
    assert row["category_after"] == "work/coding"


async def test_get_categories_seeded(db):
    cats = await db.get_categories()
    names = [c["name"] for c in cats]
    assert "工作/编程" in names
    assert "未分类" in names


async def test_query_records_filter_app(db):
    ctx_code = CaptureContext(app_name="VSCode", process_name="code", window_title="file.py")
    ctx_chrome = CaptureContext(app_name="Chrome", process_name="chrome", window_title="Google")
    await db.insert_record(ctx_code, reason="heartbeat")
    await db.insert_record(ctx_chrome, reason="heartbeat")

    rows = await db.query_records(0, 9_999_999_999_999, app_name="VSCode")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "VSCode"


async def test_query_records_filter_keyword(db):
    ctx = CaptureContext(app_name="VSCode", process_name="code", window_title="README.md - project")
    await db.insert_record(ctx, reason="heartbeat")
    await db.insert_record(
        CaptureContext(app_name="VSCode", process_name="code", window_title="main.py"),
        reason="heartbeat",
    )

    rows = await db.query_records(0, 9_999_999_999_999, keyword="README")
    assert len(rows) == 1
    assert "README" in rows[0]["window_title"]


async def test_reclaim_stale_tasks(db):
    import time

    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(record_id)
    await db.claim_next_task("pending_vlm")  # sets status = processing_pending_vlm

    # Force locked_at to be very old
    old_ts = int((time.time() - 400) * 1000)
    await db.conn.execute(
        "UPDATE analysis_results SET locked_at=? WHERE record_id=?",
        (old_ts, record_id),
    )
    await db.conn.commit()

    count = await db.reclaim_stale_tasks(timeout_ms=300_000)
    assert count == 1

    async with db.conn.execute(
        "SELECT status FROM analysis_results WHERE record_id=?", (record_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"].startswith("pending_")
