"""Source-hash population + re-emission gating (Phase 2 slice 2a).

The cascade stores an input fingerprint per row. An idempotent rebuild keeps it
stable (no re-queue); a changed input (here: reclassification) flips the leaf
hash, bumps source_version, re-queues the narrative stage, and propagates up to
the day via the Merkle-style rollup hash.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.db import Database
from timetrace.server.summary.rollup import MetricsCascadeBuilder
from timetrace.server.summary.windows import scope_key

CUT = 4


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


def _ms(y, mo, d, h, mi=0, s=0):
    return int(_dt.datetime(y, mo, d, h, mi, s).timestamp() * 1000)


async def _add(db, ts_start, dur_ms, app, category=None):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=f"{app} window")
    rid = await db.insert_record(ctx, reason="test", ts_start=ts_start)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts_start + dur_ms, rid))
        await db.conn.commit()
    if category is not None:
        await db.set_category_final(rid, category)
    return rid


async def test_source_hash_populated_and_stable_on_idempotent_rebuild(db):
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 120_000, "Code", "work")
    b = MetricsCascadeBuilder(db, RollupConfig())
    await b.build_day(ts)

    day = await db.get_summary("day", scope_key(ts, "day", CUT))
    leaf = await db.get_summary("5min", scope_key(ts, "5min", CUT))
    assert day["source_hash"] and leaf["source_hash"]  # populated at every grain
    assert day["source_version"] == 0
    h0 = day["source_hash"]

    await b.build_day(ts)  # same inputs → no-op
    day2 = await db.get_summary("day", scope_key(ts, "day", CUT))
    assert day2["source_hash"] == h0
    assert day2["source_version"] == 0  # not bumped


async def test_reclassify_flips_hash_reemits_and_propagates(db):
    ts = _ms(2001, 6, 9, 9, 0)
    rid = await _add(db, ts, 120_000, "Code", "work")
    b = MetricsCascadeBuilder(db, RollupConfig())
    await b.build_day(ts)

    leaf_key, day_key = scope_key(ts, "5min", CUT), scope_key(ts, "day", CUT)
    leaf0 = await db.get_summary("5min", leaf_key)
    day0 = await db.get_summary("day", day_key)

    # pretend the narrative stage already ran, so we can prove it gets re-queued
    async with db.lock:
        await db.conn.execute("UPDATE summaries SET status='narrative_done'")
        await db.conn.commit()

    await db.set_category_final(rid, "study")  # input changes
    await b.build_day(ts)

    leaf1 = await db.get_summary("5min", leaf_key)
    day1 = await db.get_summary("day", day_key)
    # leaf: hash flipped, version bumped, narrative re-queued
    assert leaf1["source_hash"] != leaf0["source_hash"]
    assert leaf1["source_version"] == 1
    assert leaf1["status"] == "pending_summary"
    # propagated up the cascade: day hash flipped + re-queued too
    assert day1["source_hash"] != day0["source_hash"]
    assert day1["status"] == "pending_summary"
