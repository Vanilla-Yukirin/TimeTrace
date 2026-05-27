"""Tests for the Storage/Database layer."""

import time

import pytest

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.db import Database
from timetrace.server.db.sqlite import _MAX_ORPHAN_BRIDGE_MS


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

    # records.status must mirror the analysis state — otherwise the timeline
    # keeps showing pending_vlm while the worker has already given up.
    async with db.conn.execute("SELECT status FROM records WHERE id=?", (record_id,)) as cur:
        row = await cur.fetchone()
    assert row["status"] == "error_final"


async def test_insert_screenshot(db):
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")

    screenshot_id, was_new = await db.insert_screenshot(
        record_id=record_id,
        path="screenshots/2026/04/11/test.png",
        thumb_path="thumbs/2026/04/11/test.jpg",
        width=1920,
        height=1080,
        hash_sha256="abc123",
    )
    assert screenshot_id
    assert was_new is True

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


async def test_query_records_keyword_matches_app_name_via_fts(db):
    """FTS5 ≥3-char keyword hits app_name / process_name / url, not just title."""
    await db.insert_record(
        CaptureContext(app_name="Weixin", process_name="WeChat.exe", window_title="微信"),
        reason="heartbeat",
    )
    await db.insert_record(
        CaptureContext(app_name="VSCode", process_name="Code.exe", window_title="main.py"),
        reason="heartbeat",
    )
    await db.insert_record(
        CaptureContext(app_name="Chrome", process_name="chrome.exe", window_title="Google",
                       url="https://example.com/dashboard"),
        reason="heartbeat",
    )

    # app_name match (Weixin not in window_title)
    rows = await db.query_records(0, 9_999_999_999_999, keyword="Weixin")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "Weixin"

    # process_name match
    rows = await db.query_records(0, 9_999_999_999_999, keyword="Code.exe")
    assert len(rows) == 1
    assert rows[0]["process_name"] == "Code.exe"

    # url match
    rows = await db.query_records(0, 9_999_999_999_999, keyword="dashboard")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "Chrome"


async def test_query_records_keyword_fts_matches_vlm_desc_after_save(db):
    """save_description must keep records_fts.vlm_desc in sync."""
    rid = await db.insert_record(
        CaptureContext(app_name="App", process_name="p", window_title="window"),
        reason="heartbeat",
    )
    await db.mark_pending(rid)
    await db.save_description(rid, "鸣潮游戏内哥莱姆区域广场场景")

    rows = await db.query_records(0, 9_999_999_999_999, keyword="哥莱姆")
    assert len(rows) == 1
    assert rows[0]["id"] == rid


async def test_query_records_short_keyword_falls_back_to_like(db):
    """<3-char queries (incl 2-char CJK) bypass FTS5 and use multi-field LIKE."""
    await db.insert_record(
        CaptureContext(app_name="VS", process_name="vs.exe", window_title="some window"),
        reason="heartbeat",
    )

    rows = await db.query_records(0, 9_999_999_999_999, keyword="VS")
    assert len(rows) == 1
    assert rows[0]["app_name"] == "VS"


async def test_query_records_order_desc_returns_newest_first(db):
    """Regression: ask_agent fallback wants the *most recent* N records, not
    the oldest N. Was a bug — default ASC + LIMIT pulled the oldest N from
    full-table queries, completely reversed from "what happened lately"."""
    # Insert 3 records back-to-back; each one's ts_start is later than the
    # previous because insert_record uses _now_ms() at call time.
    await db.insert_record(
        CaptureContext(app_name="OldApp", process_name="o", window_title="first"),
        reason="heartbeat",
    )
    await db.insert_record(
        CaptureContext(app_name="MidApp", process_name="m", window_title="second"),
        reason="heartbeat",
    )
    await db.insert_record(
        CaptureContext(app_name="NewApp", process_name="n", window_title="third"),
        reason="heartbeat",
    )

    asc = await db.query_records(0, 9_999_999_999_999, limit=2, order="asc")
    desc = await db.query_records(0, 9_999_999_999_999, limit=2, order="desc")

    assert [r["app_name"] for r in asc] == ["OldApp", "MidApp"]
    assert [r["app_name"] for r in desc] == ["NewApp", "MidApp"]


async def test_query_records_fts_bm25_actually_ranks(db):
    """Regression: BM25 ordering was a silent no-op because bm25() was called
    in a subquery without MATCH (FTS5 aux functions only work alongside MATCH).
    A record whose vlm_desc mentions the keyword many times should out-rank
    one that mentions it once.

    Insert order is INVERTED relative to the expected output order: we put
    the LOW-density record in first (lower rowid) and the HIGH-density one
    second. This way the test can actually distinguish:
      - real BM25 working → high-density wins regardless of insert order
      - bug regression (e.g. ORDER BY constant → falls back to rowid)
        → low-density would come first
    """
    # Insert LOW-density first so it gets the lower rowid.
    rid_low = await db.insert_record(
        CaptureContext(app_name="App2", process_name="b", window_title="w"),
        reason="heartbeat",
    )
    await db.mark_pending(rid_low)
    await db.save_description(
        rid_low,
        "今天天气真好，顺便提一句 TimeTrace 这种工具能记录我看了什么。",
    )

    # Then insert HIGH-density (4 mentions) with the higher rowid.
    rid_high = await db.insert_record(
        CaptureContext(app_name="App1", process_name="a", window_title="w"),
        reason="heartbeat",
    )
    await db.mark_pending(rid_high)
    await db.save_description(
        rid_high,
        "TimeTrace 是一款桌面活动记录工具。TimeTrace 跑在本机。"
        "我现在正在阅读 TimeTrace 的源码，理解 TimeTrace 的架构。",
    )

    # Plus a third record with even higher density (7 mentions) inserted
    # last — most-relevant must end up first regardless of insert order,
    # which a working BM25 satisfies but rowid-fallback does not.
    rid_top = await db.insert_record(
        CaptureContext(app_name="App3", process_name="c", window_title="w"),
        reason="heartbeat",
    )
    await db.mark_pending(rid_top)
    await db.save_description(
        rid_top,
        "TimeTrace TimeTrace TimeTrace TimeTrace "
        "TimeTrace TimeTrace TimeTrace 测试 BM25 排序。",
    )

    rows = await db.query_records(0, 9_999_999_999_999, keyword="TimeTrace")
    assert len(rows) == 3
    # Expected: highest density first, lowest last. The opposite of insert order.
    assert [r["id"] for r in rows] == [rid_top, rid_high, rid_low], (
        f"BM25 ranking broken; got insert-order or worse: "
        f"{[r['app_name'] for r in rows]}"
    )


async def test_reclaim_stale_tasks(db):
    import time

    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(record_id)
    await db.claim_next_task("pending_vlm")  # status → processing_vlm

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
    # Must be exactly the pending queue name — not "pending_pending_vlm" — so a
    # subsequent claim_next_task("pending_vlm") finds it again.
    assert row["status"] == "pending_vlm"

    reclaimed = await db.claim_next_task("pending_vlm")
    assert reclaimed is not None
    assert reclaimed["record_id"] == record_id


async def test_reclaim_handles_legacy_processing_pending_state(db):
    """Rows left over from older builds carried `processing_pending_vlm`; after
    reclaim they must end up in `pending_vlm`, not `pending_pending_vlm`."""
    import time as _t

    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(record_id)

    # Manually inject the legacy bad state.
    old_ts = int((_t.time() - 1000) * 1000)
    await db.conn.execute(
        "UPDATE analysis_results SET status='processing_pending_vlm', locked_at=?"
        " WHERE record_id=?",
        (old_ts, record_id),
    )
    await db.conn.commit()

    count = await db.reclaim_stale_tasks(timeout_ms=300_000)
    assert count == 1

    async with db.conn.execute(
        "SELECT status FROM analysis_results WHERE record_id=?", (record_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "pending_vlm"

    # And it can now be claimed again — the original orphaning bug is gone.
    reclaimed = await db.claim_next_task("pending_vlm")
    assert reclaimed is not None
    assert reclaimed["record_id"] == record_id


async def test_mark_pending_idempotent_preserves_existing_data(db):
    """Second mark_pending must not overwrite existing analysis_results fields."""
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")

    # First mark_pending – creates the analysis_results row
    await db.mark_pending(record_id)

    # Simulate worker completing VLM analysis
    await db.claim_next_task("pending_vlm")
    await db.save_description(record_id, "a real description")
    await db.transition(record_id, "vlm_done")

    # Second mark_pending – must NOT reset status or wipe vlm_desc
    await db.mark_pending(record_id)

    async with db.conn.execute(
        "SELECT status, vlm_desc FROM analysis_results WHERE record_id=?",
        (record_id,),
    ) as cur:
        row = await cur.fetchone()

    assert row["status"] == "vlm_done"  # not reset to pending_vlm
    assert row["vlm_desc"] == "a real description"  # not wiped


async def test_mark_pending_records_status_guard(db):
    """records.status must not be downgraded once past 'captured'."""
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Window")
    record_id = await db.insert_record(ctx, reason="heartbeat")

    # First mark_pending: captured → pending_vlm (normal path)
    await db.mark_pending(record_id)

    # Advance through analysis
    await db.claim_next_task("pending_vlm")
    await db.transition(record_id, "vlm_done")

    # Second mark_pending: records.status should remain vlm_done
    await db.mark_pending(record_id)

    async with db.conn.execute("SELECT status FROM records WHERE id=?", (record_id,)) as cur:
        row = await cur.fetchone()

    assert row["status"] == "vlm_done"  # not downgraded


# --------------------------------------------------------------------- #
# Orphan ts_end IS NULL healing                                          #
# --------------------------------------------------------------------- #


async def test_insert_closes_prior_orphan_to_next_ts_start(db):
    """Layer 1: insert_record collapses a prior orphan to the new record's ts_start."""
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid_a = await db.insert_record(ctx, reason="heartbeat")

    # Force A to a recent ts_start AND null out ts_end (= simulates a short
    # interruption before close_record fired).
    recent_start = int(time.time() * 1000) - 1000
    await db.conn.execute(
        "UPDATE records SET ts_start=?, ts_end=NULL WHERE id=?",
        (recent_start, rid_a),
    )
    await db.conn.commit()

    rid_b = await db.insert_record(ctx, reason="heartbeat")

    async with db.conn.execute("SELECT ts_end FROM records WHERE id=?", (rid_a,)) as cur:
        a_end = (await cur.fetchone())["ts_end"]
    async with db.conn.execute("SELECT ts_start FROM records WHERE id=?", (rid_b,)) as cur:
        b_start = (await cur.fetchone())["ts_start"]

    assert a_end is not None
    assert a_end == b_start


async def test_insert_zeroes_prior_orphan_when_new_record_is_too_late(db):
    """A stale orphan from a previous run must not span into today's first record."""
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid_a = await db.insert_record(ctx, reason="heartbeat")

    old_start = 1000
    await db.conn.execute(
        "UPDATE records SET ts_start=?, ts_end=NULL WHERE id=?", (old_start, rid_a)
    )
    await db.conn.commit()

    await db.insert_record(ctx, reason="heartbeat")

    async with db.conn.execute("SELECT ts_start, ts_end FROM records WHERE id=?", (rid_a,)) as cur:
        row = await cur.fetchone()

    assert row["ts_end"] == row["ts_start"]


async def test_insert_closes_multiple_orphans_each_to_correct_boundary(db):
    """Layer 1, multi-orphan: MIN(next.ts_start) gives each row its own boundary,
    not a shared timestamp."""
    ctx = CaptureContext(app_name="X", process_name="x", window_title="x")
    rid_a = await db.insert_record(ctx, reason="heartbeat")
    rid_b = await db.insert_record(ctx, reason="heartbeat")
    rid_c = await db.insert_record(ctx, reason="heartbeat")

    # Stamp recent deterministic timestamps and null out ts_end on all three.
    base = int(time.time() * 1000) - 3000
    await db.conn.execute("UPDATE records SET ts_start=?, ts_end=NULL WHERE id=?", (base, rid_a))
    await db.conn.execute(
        "UPDATE records SET ts_start=?, ts_end=NULL WHERE id=?", (base + 1000, rid_b)
    )
    await db.conn.execute(
        "UPDATE records SET ts_start=?, ts_end=NULL WHERE id=?", (base + 2000, rid_c)
    )
    await db.conn.commit()

    rid_d = await db.insert_record(ctx, reason="heartbeat")

    async with db.conn.execute("SELECT id, ts_start, ts_end FROM records ORDER BY ts_start") as cur:
        rows = list(await cur.fetchall())
    by_id = {r["id"]: r for r in rows}
    a, b, c, d = by_id[rid_a], by_id[rid_b], by_id[rid_c], by_id[rid_d]

    # Each orphan closes to its own next, not to D's ts_start.
    assert a["ts_end"] == b["ts_start"]
    assert b["ts_end"] == c["ts_start"]
    assert c["ts_end"] == d["ts_start"]


async def test_init_heals_orphan_with_successor_to_next_ts_start(tmp_path):
    """Layer 2: db.init() repairs historical orphans whose successor exists."""
    cfg = StorageConfig(data_dir=tmp_path)
    db1 = Database(cfg)
    await db1.init()
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid_a = await db1.insert_record(ctx, reason="heartbeat")
    rid_b = await db1.insert_record(ctx, reason="heartbeat")
    # Re-create the bug: A's ts_end never closed, B sits after it.
    await db1.conn.execute("UPDATE records SET ts_start=1000, ts_end=NULL WHERE id=?", (rid_a,))
    await db1.conn.execute("UPDATE records SET ts_start=2000 WHERE id=?", (rid_b,))
    await db1.conn.commit()
    await db1.close()

    # Re-open: init() heals A.
    db2 = Database(cfg)
    await db2.init()
    try:
        async with db2.conn.execute("SELECT ts_end FROM records WHERE id=?", (rid_a,)) as cur:
            a_end = (await cur.fetchone())["ts_end"]
        async with db2.conn.execute("SELECT ts_start FROM records WHERE id=?", (rid_b,)) as cur:
            b_start = (await cur.fetchone())["ts_start"]
        assert a_end == b_start
    finally:
        await db2.close()


async def test_init_zeroes_orphan_when_successor_gap_is_too_large(tmp_path):
    """Historical orphans with day-sized gaps should render as points, not bars."""
    cfg = StorageConfig(data_dir=tmp_path)
    db1 = Database(cfg)
    await db1.init()
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid_a = await db1.insert_record(ctx, reason="heartbeat")
    rid_b = await db1.insert_record(ctx, reason="heartbeat")

    await db1.conn.execute("UPDATE records SET ts_start=1000, ts_end=NULL WHERE id=?", (rid_a,))
    await db1.conn.execute(
        "UPDATE records SET ts_start=? WHERE id=?",
        (1000 + _MAX_ORPHAN_BRIDGE_MS + 1, rid_b),
    )
    await db1.conn.commit()
    await db1.close()

    db2 = Database(cfg)
    await db2.init()
    try:
        async with db2.conn.execute(
            "SELECT ts_start, ts_end FROM records WHERE id=?", (rid_a,)
        ) as cur:
            row = await cur.fetchone()
        assert row["ts_end"] == row["ts_start"]
    finally:
        await db2.close()


async def test_init_caps_existing_implausibly_long_closed_record(tmp_path):
    """Rows already closed by older builds must be cleaned up on startup too."""
    cfg = StorageConfig(data_dir=tmp_path)
    db1 = Database(cfg)
    await db1.init()
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid = await db1.insert_record(ctx, reason="heartbeat")
    await db1.conn.execute(
        "UPDATE records SET ts_start=1000, ts_end=? WHERE id=?",
        (1000 + _MAX_ORPHAN_BRIDGE_MS + 1, rid),
    )
    await db1.conn.commit()
    await db1.close()

    db2 = Database(cfg)
    await db2.init()
    try:
        async with db2.conn.execute(
            "SELECT ts_start, ts_end FROM records WHERE id=?", (rid,)
        ) as cur:
            row = await cur.fetchone()
        assert row["ts_end"] == row["ts_start"]
    finally:
        await db2.close()


async def test_init_heals_latest_orphan_with_no_successor_to_zero_duration(tmp_path):
    """Layer 2: orphan with no successor gets ts_end == ts_start (zero-duration),
    NOT updated_at — that would point at when VLM completed days later."""
    cfg = StorageConfig(data_dir=tmp_path)
    db1 = Database(cfg)
    await db1.init()
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid_a = await db1.insert_record(ctx, reason="heartbeat")
    # Simulate the worker bumping updated_at long after capture (the exact
    # contamination we want the fix to be immune to).
    await db1.conn.execute(
        "UPDATE records SET ts_start=1000, ts_end=NULL, updated_at=99999999 WHERE id=?",
        (rid_a,),
    )
    await db1.conn.commit()
    await db1.close()

    db2 = Database(cfg)
    await db2.init()
    try:
        async with db2.conn.execute(
            "SELECT ts_start, ts_end FROM records WHERE id=?", (rid_a,)
        ) as cur:
            row = await cur.fetchone()
        assert row["ts_end"] is not None
        assert row["ts_end"] == row["ts_start"]  # zero-duration, NOT updated_at
    finally:
        await db2.close()


async def test_ingest_or_get_record_inserts_when_new(db):
    ctx = CaptureContext(app_name="A", process_name="a", window_title="t")
    rid, was_new = await db.ingest_or_get_record(
        ctx,
        reason="switch",
        event_type="window_switch",
        client_record_id="client-uuid-1",
    )
    assert was_new is True
    assert rid

    found = await db.find_record_by_client_id("client-uuid-1")
    assert found is not None
    assert found["id"] == rid
    assert found["client_record_id"] == "client-uuid-1"


async def test_ingest_or_get_record_returns_existing_on_replay(db):
    ctx = CaptureContext(app_name="A", process_name="a", window_title="t")
    rid_first, _ = await db.ingest_or_get_record(
        ctx, reason="heartbeat", event_type="heartbeat", client_record_id="dup"
    )
    rid_second, was_new = await db.ingest_or_get_record(
        ctx, reason="heartbeat", event_type="heartbeat", client_record_id="dup"
    )
    assert rid_second == rid_first
    assert was_new is False


async def test_find_record_by_client_id_returns_none_for_missing(db):
    assert await db.find_record_by_client_id("never-set") is None


async def test_insert_record_without_client_id_leaves_column_null(db):
    """Pre-P3a callers that don't pass client_record_id keep working."""
    ctx = CaptureContext(app_name="A", process_name="a", window_title="t")
    rid = await db.insert_record(ctx, reason="heartbeat")
    async with db.conn.execute("SELECT client_record_id FROM records WHERE id=?", (rid,)) as cur:
        row = await cur.fetchone()
    assert row["client_record_id"] is None


async def test_multiple_null_client_record_ids_do_not_collide(db):
    """The partial unique index must not constrain rows where the column is NULL."""
    ctx = CaptureContext(app_name="A", process_name="a", window_title="t")
    rid_a = await db.insert_record(ctx, reason="heartbeat")
    rid_b = await db.insert_record(ctx, reason="heartbeat")
    assert rid_a != rid_b


async def test_records_client_record_id_index_exists(db):
    """Smoke check: the partial unique index lives on a freshly-init'd DB."""
    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_records_client_record_id'"
    ) as cur:
        assert await cur.fetchone() is not None


async def test_ingest_or_get_record_does_not_leave_open_transaction_on_replay(db):
    """When ingest_or_get_record hits the IntegrityError path on a replay, the
    heal UPDATE that ran *before* the failed INSERT must not stay in a pending
    transaction. Otherwise a subsequent crash loses the heal and a parallel
    connection reads stale state.

    Verified by opening a *second* aiosqlite connection on the same DB file
    after the IntegrityError path runs, and asserting the heal is visible
    there — uncommitted writes on connection A are invisible to connection B.
    """
    import aiosqlite

    # Setup: original record, then a duplicate ingest to trigger heal+IntegrityError.
    ctx = CaptureContext(app_name="A", process_name="a", window_title="t")
    rid_first, _ = await db.ingest_or_get_record(
        ctx,
        reason="heartbeat",
        event_type="heartbeat",
        client_record_id="dup-id",
    )

    # Sleep one ms so the second insert's ts_start is strictly greater than
    # rid_first's, making rid_first eligible for heal (ts_start < cutoff).
    time.sleep(0.002)

    rid_again, was_new = await db.ingest_or_get_record(
        ctx,
        reason="heartbeat",
        event_type="heartbeat",
        client_record_id="dup-id",
    )
    assert was_new is False
    assert rid_again == rid_first

    # Read from a fresh connection — sees only committed data.
    async with aiosqlite.connect(db._cfg.db_path) as conn2:
        conn2.row_factory = aiosqlite.Row
        async with conn2.execute("SELECT ts_end FROM records WHERE id=?", (rid_first,)) as cur:
            row = await cur.fetchone()
    # If the heal UPDATE was rolled back / left pending, ts_end is still NULL.
    assert row["ts_end"] is not None, (
        "heal UPDATE was not committed before IntegrityError path returned"
    )


async def test_get_category_final_returns_none_for_missing(db):
    assert await db.get_category_final("does-not-exist") is None


async def test_get_category_final_returns_value_after_classification(db):
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(rid)
    await db.conn.execute(
        "UPDATE analysis_results SET category_final=? WHERE record_id=?",
        ("work/coding", rid),
    )
    await db.conn.commit()

    assert await db.get_category_final(rid) == "work/coding"


async def test_close_open_records_before_returns_zero_when_clean(db):
    """Strict `ts_start < cutoff` keeps the helper from accidentally closing
    the about-to-be-active record (i.e. the legitimate in-flight one)."""
    ctx = CaptureContext(app_name="A", process_name="a", window_title="a")
    rid = await db.insert_record(ctx, reason="heartbeat")

    async with db.conn.execute("SELECT ts_start FROM records WHERE id=?", (rid,)) as cur:
        ts_start = (await cur.fetchone())["ts_start"]

    # Calling with cutoff == this record's own ts_start must NOT close it.
    async with db.lock:
        healed = await db._close_open_records_before(ts_start, tail_ts=ts_start)
        await db.conn.commit()

    assert healed == 0

    async with db.conn.execute("SELECT ts_end FROM records WHERE id=?", (rid,)) as cur:
        assert (await cur.fetchone())["ts_end"] is None
