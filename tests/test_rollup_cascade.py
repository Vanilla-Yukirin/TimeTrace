"""Tests for the deterministic metrics cascade (5min → 1h → 6h → day → week).

Real tmp SQLite (no mocks), sentinel year 2099 so nothing touches a real
"today". Durations stay under the 5-minute plausibility cap so they aren't
zeroed by the duration clamp.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent.tools import _UNCLASSIFIED
from timetrace.server.db import Database
from timetrace.server.summary.metrics import aggregate_frame_metrics, merge_metrics
from timetrace.server.summary.rollup import MetricsCascadeBuilder
from timetrace.server.summary.windows import window_bounds

CUT = 4


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


def _ms(y, mo, d, h, mi=0, s=0) -> int:
    return int(_dt.datetime(y, mo, d, h, mi, s).timestamp() * 1000)


async def _add(db: Database, ts_start: int, dur_ms: int, app: str, category: str) -> str:
    """Insert one frame at ts_start with a closed ts_end and a final category."""
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=f"{app} window")
    rid = await db.insert_record(ctx, reason="heartbeat", ts_start=ts_start)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts_start + dur_ms, rid))
        await db.conn.commit()
    await db.set_category_final(rid, category)
    return rid


async def _seed_day(db: Database) -> None:
    """A synthetic logical day (2099-06-09, 04:00→04:00) with known durations.

    Layout (all within the 5-min cap):
      09:00 work 2m + 09:01 work 1m   → one 5min window [09:00,09:05), 2 records
      09:07 entertainment 3m          → 5min window [09:05,09:10)
      14:00 study 4m                  → 6h block idx1
      23:00 social 2m                 → 6h block idx3
    """
    await _add(db, _ms(2099, 6, 9, 9, 0), 120_000, "Code", "work")
    await _add(db, _ms(2099, 6, 9, 9, 1), 60_000, "Code", "work")
    await _add(db, _ms(2099, 6, 9, 9, 7), 180_000, "Chrome", "entertainment")
    await _add(db, _ms(2099, 6, 9, 14, 0), 240_000, "Obsidian", "study")
    await _add(db, _ms(2099, 6, 9, 23, 0), 120_000, "Slack", "social")


async def test_cascade_builds_expected_window_counts(db):
    await _seed_day(db)
    builder = MetricsCascadeBuilder(db, RollupConfig())
    counts = await builder.build_day(_ms(2099, 6, 9, 9, 0))

    # 4 populated 5min windows (09:00, 09:05, 14:00, 23:00); ~284 empty skipped.
    assert counts["5min"] == 4
    # 1h: hours 09, 14, 23 → 3 (09:00 + 09:07 collapse into hour 09).
    assert counts["1h"] == 3
    # 6h blocks: idx0 (09:xx), idx1 (14:00), idx3 (23:00) → 3.
    assert counts["6h"] == 3
    assert counts["day"] == 1
    assert counts["week"] == 1


async def test_day_metrics_match_direct_frame_aggregation(db):
    """SUM-invariant: the cascaded day metrics == a direct frame aggregation.

    This is the load-bearing correctness check — it catches double-counting and
    window off-by-ones in the merge, since the two sides partition frames
    differently (per-5min-window merged up vs one query over the whole day).
    """
    await _seed_day(db)
    builder = MetricsCascadeBuilder(db, RollupConfig())
    await builder.build_day(_ms(2099, 6, 9, 9, 0))

    day_start, day_end = window_bounds(_ms(2099, 6, 9, 9, 0), "day", CUT)
    direct = await aggregate_frame_metrics(db, day_start, day_end)
    day_row = await db.get_summary("day", "2099-06-09")
    assert day_row is not None
    cascaded = json.loads(day_row["metrics_json"])

    assert cascaded["cat_ms"] == direct["cat_ms"]
    assert cascaded["app_ms"] == direct["app_ms"]
    assert cascaded["active_ms"] == direct["active_ms"]
    assert cascaded["record_count"] == direct["record_count"] == 5
    # Spot-check the known truth (ms): work 180k, entertainment 180k, study 240k, social 120k.
    assert cascaded["cat_ms"] == {
        "work": 180_000,
        "entertainment": 180_000,
        "study": 240_000,
        "social": 120_000,
    }
    assert cascaded["active_ms"] == 720_000


async def test_1h_window_merges_its_5min_children(db):
    await _seed_day(db)
    builder = MetricsCascadeBuilder(db, RollupConfig())
    await builder.build_day(_ms(2099, 6, 9, 9, 0))

    hour = await db.get_summary("1h", "2099-06-09T09")
    assert hour is not None
    m = json.loads(hour["metrics_json"])
    # hour 09 holds the two work frames (180k) + the entertainment frame (180k).
    assert m["cat_ms"] == {"work": 180_000, "entertainment": 180_000}
    assert m["active_ms"] == 360_000
    assert m["record_count"] == 3


async def test_rebuild_is_idempotent(db):
    await _seed_day(db)
    builder = MetricsCascadeBuilder(db, RollupConfig())
    await builder.build_day(_ms(2099, 6, 9, 9, 0))
    first = await db.get_summary("day", "2099-06-09")
    # Re-run over the same range: UNIQUE(grain, scope_key) UPSERT must not dup.
    counts = await builder.build_day(_ms(2099, 6, 9, 9, 0))
    assert counts["5min"] == 4
    second = await db.get_summary("day", "2099-06-09")
    assert json.loads(first["metrics_json"]) == json.loads(second["metrics_json"])
    assert first["id"] == second["id"]  # same row, not a duplicate

    async with db.lock:
        async with db.conn.execute(
            "SELECT COUNT(*) AS n FROM summaries WHERE grain='5min'"
        ) as cur:
            n = (await cur.fetchone())["n"]
    assert n == 4


async def test_empty_day_builds_nothing(db):
    builder = MetricsCascadeBuilder(db, RollupConfig())
    counts = await builder.build_day(_ms(2099, 6, 9, 9, 0))
    assert counts == {"5min": 0, "1h": 0, "6h": 0, "day": 0, "week": 0}
    assert await db.get_summary("day", "2099-06-09") is None


async def test_partial_range_rebuild_does_not_downgrade_parent(db):
    """A partial-range rebuild must NOT shrink a higher-grain row that already
    has children outside the range (crash-recovery / re-emission safety).

    6h block 1 = [10:00, 16:00) holds frames at 10:30 AND 14:00. A range of
    [04:00, 12:00) only contains the 10:30 child. Building that range, then the
    full day, then the partial range AGAIN must leave block 1 with BOTH frames —
    the bug was that the second partial build re-merged only the in-range child.
    """
    await _add(db, _ms(2099, 6, 9, 10, 30), 60_000, "Code", "work")  # block1, in range
    await _add(db, _ms(2099, 6, 9, 14, 0), 120_000, "Obsidian", "study")  # block1, NOT in range
    builder = MetricsCascadeBuilder(db, RollupConfig())
    block1_key = "2099-06-09T1"

    await builder.build_range(_ms(2099, 6, 9, 4, 0), _ms(2099, 6, 9, 12, 0))  # partial
    await builder.build_day(_ms(2099, 6, 9, 10, 30))  # full → block1 has both
    full = json.loads((await db.get_summary("6h", block1_key))["metrics_json"])
    assert full["cat_ms"] == {"work": 60_000, "study": 120_000}

    await builder.build_range(_ms(2099, 6, 9, 4, 0), _ms(2099, 6, 9, 12, 0))  # partial again
    after = json.loads((await db.get_summary("6h", block1_key))["metrics_json"])
    assert after == full  # not downgraded to just the in-range child


async def test_boundary_records_attribute_to_opening_window(db):
    """Frames exactly on window boundaries land in the window they OPEN (half-open),
    and the day SUM-invariant still holds."""
    await _add(db, _ms(2099, 6, 9, 10, 0), 60_000, "Code", "work")  # exact 1h + 6h-block-1 start
    await _add(db, _ms(2099, 6, 9, 9, 5), 60_000, "Chrome", "study")  # exact 5min start
    builder = MetricsCascadeBuilder(db, RollupConfig())
    await builder.build_day(_ms(2099, 6, 9, 9, 5))

    # 10:00 opens hour '10' and 6h block 1, NOT hour '09' / block 0.
    assert await db.get_summary("1h", "2099-06-09T10") is not None
    assert json.loads((await db.get_summary("6h", "2099-06-09T1"))["metrics_json"])["cat_ms"] == {
        "work": 60_000
    }
    block0 = await db.get_summary("6h", "2099-06-09T0")
    assert json.loads(block0["metrics_json"])["cat_ms"] == {"study": 60_000}

    day_start, day_end = window_bounds(_ms(2099, 6, 9, 9, 5), "day", CUT)
    direct = await aggregate_frame_metrics(db, day_start, day_end)
    cascaded = json.loads((await db.get_summary("day", "2099-06-09"))["metrics_json"])
    assert cascaded["cat_ms"] == direct["cat_ms"] == {"work": 60_000, "study": 60_000}


async def test_multi_day_range_spans_week_boundary(db):
    """build_range across days in different ISO weeks: per-day + per-week rows,
    and the grand SUM-invariant (Σ week rows == direct frames over the range)."""
    # 8 days apart guarantees two distinct ISO weeks without computing weekdays.
    await _add(db, _ms(2099, 6, 9, 9, 0), 120_000, "Code", "work")
    await _add(db, _ms(2099, 6, 17, 21, 0), 180_000, "Chrome", "entertainment")
    builder = MetricsCascadeBuilder(db, RollupConfig())

    start = window_bounds(_ms(2099, 6, 9, 9, 0), "day", CUT)[0]
    end = window_bounds(_ms(2099, 6, 17, 21, 0), "day", CUT)[1]
    await builder.build_range(start, end)

    days = await db.get_summaries_in_range("day", 0, 9_999_999_999_999)
    weeks = await db.get_summaries_in_range("week", 0, 9_999_999_999_999)
    assert len(days) == 2
    assert len(weeks) == 2  # different ISO weeks

    grand = merge_metrics([json.loads(w["metrics_json"]) for w in weeks])
    direct = await aggregate_frame_metrics(db, start, end)
    assert grand["cat_ms"] == direct["cat_ms"]
    assert grand["active_ms"] == direct["active_ms"] == 300_000


async def test_unclassified_records_flow_into_metrics(db):
    """A frame with NO analysis row buckets under '_unclassified' (matching the
    read-path get_category_stats), not a real category."""
    await _add(db, _ms(2099, 6, 9, 9, 0), 60_000, "Code", "work")
    # insert a frame WITHOUT set_category_final → no analysis_results row.
    ctx = CaptureContext(app_name="Mystery", process_name="mystery", window_title="?")
    rid = await db.insert_record(ctx, reason="heartbeat", ts_start=_ms(2099, 6, 9, 9, 2))
    async with db.lock:
        await db.conn.execute(
            "UPDATE records SET ts_end=? WHERE id=?", (_ms(2099, 6, 9, 9, 3), rid)
        )
        await db.conn.commit()

    builder = MetricsCascadeBuilder(db, RollupConfig())
    await builder.build_day(_ms(2099, 6, 9, 9, 0))
    cats = json.loads((await db.get_summary("day", "2099-06-09"))["metrics_json"])["cat_ms"]
    assert cats == {"work": 60_000, _UNCLASSIFIED: 60_000}


async def test_rebuild_reflects_relabel(db):
    """Re-running the cascade after a category change picks up the new label
    (the re-emission foundation: rebuild = current truth)."""
    rid = await _add(db, _ms(2099, 6, 9, 9, 0), 120_000, "Code", "work")
    builder = MetricsCascadeBuilder(db, RollupConfig())
    await builder.build_day(_ms(2099, 6, 9, 9, 0))
    before = json.loads((await db.get_summary("day", "2099-06-09"))["metrics_json"])
    assert before["cat_ms"] == {"work": 120_000}

    await db.set_category_final(rid, "study")
    await builder.build_day(_ms(2099, 6, 9, 9, 0))
    after = json.loads((await db.get_summary("day", "2099-06-09"))["metrics_json"])
    assert after["cat_ms"] == {"study": 120_000}
