"""Tests for the query_stats agent tool (cascade-first aggregate stats).

Uses period='custom' with explicit ISO bounds so the assertions are
deterministic (the named periods today/this_week/this_month read the real
clock). Sentinel years: 2099 (future) for the live path — never a finalized
cascade window, so it always falls through to live SQL — and 2001 (past) for
the digest path, where a built day window IS finalized (window_end <= now).
"""

from __future__ import annotations

import datetime as _dt

import pytest

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent import tools as agent_tools
from timetrace.server.agent.tools import _UNCLASSIFIED, ms_to_iso
from timetrace.server.db import Database
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


async def _add(db, ts_start, dur_ms, app, category=None):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=f"{app} window")
    rid = await db.insert_record(ctx, reason="heartbeat", ts_start=ts_start)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts_start + dur_ms, rid))
        await db.conn.commit()
    if category is not None:
        await db.set_category_final(rid, category)
    return rid


async def _seed(db, year):
    await _add(db, _ms(year, 6, 9, 9, 0), 120_000, "Code", "work")
    await _add(db, _ms(year, 6, 9, 9, 5), 180_000, "Chrome", "entertainment")
    await _add(db, _ms(year, 6, 9, 14, 0), 60_000, "Slack", "social")


async def test_query_stats_live_by_category(db):
    await _seed(db, 2099)  # future → never a finalized cascade window → live path
    ds, de = window_bounds(_ms(2099, 6, 9, 9, 0), "day", CUT)
    res = await agent_tools.query_stats(
        db, metric="seconds_by_category", period="custom",
        start_iso=ms_to_iso(ds), end_iso=ms_to_iso(de),
    )
    assert res["source"] == "live"
    by = {it["key"]: it["total_seconds"] for it in res["items"]}
    assert by == {"work": 120, "entertainment": 180, "social": 60}
    assert res["total_seconds"] == 360
    assert "categories_legend" in res


async def test_query_stats_digest_for_finalized_day(db):
    await _seed(db, 2001)  # past → a built day window is finalized → digest path
    builder = MetricsCascadeBuilder(db, RollupConfig())
    await builder.build_day(_ms(2001, 6, 9, 9, 0))
    ds, de = window_bounds(_ms(2001, 6, 9, 9, 0), "day", CUT)
    res = await agent_tools.query_stats(
        db, metric="seconds_by_category", period="custom",
        start_iso=ms_to_iso(ds), end_iso=ms_to_iso(de),
    )
    assert res["source"] == "digest"
    by = {it["key"]: it["total_seconds"] for it in res["items"]}
    assert by == {"work": 120, "entertainment": 180, "social": 60}


async def test_query_stats_filter_category(db):
    await _seed(db, 2099)
    ds, de = window_bounds(_ms(2099, 6, 9, 9, 0), "day", CUT)
    res = await agent_tools.query_stats(
        db, metric="seconds_by_category", period="custom",
        start_iso=ms_to_iso(ds), end_iso=ms_to_iso(de), filter_category="entertainment",
    )
    assert [it["key"] for it in res["items"]] == ["entertainment"]
    assert res["total_seconds"] == 180


async def test_query_stats_by_app_and_active_and_count(db):
    await _seed(db, 2099)
    ds, de = window_bounds(_ms(2099, 6, 9, 9, 0), "day", CUT)
    iso = dict(start_iso=ms_to_iso(ds), end_iso=ms_to_iso(de), period="custom")

    apps = await agent_tools.query_stats(db, metric="seconds_by_app", **iso)
    assert {it["key"]: it["total_seconds"] for it in apps["items"]} == {
        "Code": 120, "Chrome": 180, "Slack": 60
    }
    active = await agent_tools.query_stats(db, metric="active_seconds", **iso)
    assert active["total_seconds"] == 360
    count = await agent_tools.query_stats(db, metric="record_count", **iso)
    assert count["record_count"] == 3


async def test_query_stats_unclassified_surfaced(db):
    await _add(db, _ms(2099, 6, 9, 9, 0), 60_000, "Code", "work")
    await _add(db, _ms(2099, 6, 9, 9, 5), 60_000, "Mystery")  # no category → _unclassified
    ds, de = window_bounds(_ms(2099, 6, 9, 9, 0), "day", CUT)
    res = await agent_tools.query_stats(
        db, metric="seconds_by_category", period="custom",
        start_iso=ms_to_iso(ds), end_iso=ms_to_iso(de),
    )
    unc = next(it for it in res["items"] if it["key"] == _UNCLASSIFIED)
    assert unc["is_unclassified"] is True and unc["note"]


async def test_query_stats_validation_errors(db):
    bad_metric = await agent_tools.query_stats(db, metric="bogus", period="today")
    assert "error" in bad_metric
    bad_custom = await agent_tools.query_stats(db, metric="active_seconds", period="custom")
    assert "error" in bad_custom and "start_iso" in bad_custom["error"]


async def test_query_stats_today_bounds_are_sane(db):
    # Named periods read the real clock — just assert the window is well-formed.
    res = await agent_tools.query_stats(db, metric="active_seconds", period="today")
    assert res["source"] == "live"  # today's window is open → never digest
    assert res["period_start_iso"] <= res["period_end_iso"]


async def test_query_stats_active_seconds_is_wall_clock_union(db):
    # Two overlapping captures + one sleep-artifact (>5min span, clamped to 0).
    await _add(db, _ms(2099, 6, 9, 10, 0, 0), 180_000, "Code", "work")  # [10:00, 10:03)
    await _add(db, _ms(2099, 6, 9, 10, 1, 0), 180_000, "Code", "work")  # [10:01, 10:04)
    await _add(db, _ms(2099, 6, 9, 11, 0, 0), 600_000, "Code", "work")  # 10min → clamp → 0
    ds, de = window_bounds(_ms(2099, 6, 9, 10, 0), "day", CUT)
    iso = dict(start_iso=ms_to_iso(ds), end_iso=ms_to_iso(de), period="custom")

    active = await agent_tools.query_stats(db, metric="active_seconds", **iso)
    by_cat = await agent_tools.query_stats(db, metric="seconds_by_category", **iso)

    # union [10:00,10:03)∪[10:01,10:04) = [10:00,10:04) = 240s; sleep artifact adds 0
    assert active["total_seconds"] == 240
    assert active["source"] == "live"
    # Σ own-span double-counts the 60s overlap → 360; sleep artifact still 0
    assert by_cat["total_seconds"] == 360
    # the contract: the union floor never exceeds the additive Σ-span
    assert active["total_seconds"] < by_cat["total_seconds"]


async def test_query_stats_semantics_block_present(db):
    await _seed(db, 2099)
    ds, de = window_bounds(_ms(2099, 6, 9, 9, 0), "day", CUT)
    iso = dict(start_iso=ms_to_iso(ds), end_iso=ms_to_iso(de), period="custom")
    for metric in ("seconds_by_category", "seconds_by_app", "active_seconds", "record_count"):
        res = await agent_tools.query_stats(db, metric=metric, **iso)
        sem = res["semantics"]
        assert "下限" in sem["definition"]
        assert sem["is_a_floor_because"] and sem["how_to_phrase"]
    # active_seconds advertises the union dedup relationship
    act = await agent_tools.query_stats(db, metric="active_seconds", **iso)
    assert "并集" in act["semantics"]["aggregation"]
