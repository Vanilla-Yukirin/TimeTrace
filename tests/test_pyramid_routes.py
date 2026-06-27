"""E2E tests for the two pyramid-panel feeds: GET /v1/llm-requests and
GET /v1/summaries. In-process FastAPI over a real SqliteDatabase (no auth:
create_app(db) leaves users=None so business_deps is empty)."""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.llm_log import LLMRequestLog
from timetrace.server.summary.rollup import MetricsCascadeBuilder
from timetrace.server.summary.windows import scope_key


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


def _ms(y, mo, d, h, mi=0):
    return int(_dt.datetime(y, mo, d, h, mi).timestamp() * 1000)


# ----------------------------- /v1/llm-requests ----------------------------- #


async def test_llm_requests_feed_and_stats(db):
    from dataclasses import asdict

    for r in [
        LLMRequestLog("narrate", "m", 1000, 1500, 500, "ok", 100, 30, 10, 130, 200, 50),
        LLMRequestLog("worker_vlm", "m", 2000, 2400, 400, "ok", 80, 20, 0, 100, 90, 40),
        LLMRequestLog("narrate", "m", 3000, 3100, 100, "error", error="boom"),
    ]:
        await db.insert_llm_request(**asdict(r))

    app = create_app(db)
    with TestClient(app) as client:
        data = client.get("/v1/llm-requests", params={"limit": 10}).json()
        assert len(data["items"]) == 3
        assert data["items"][0]["ts_start"] == 3000  # newest first

        only = client.get("/v1/llm-requests", params={"caller": "narrate"}).json()
        assert len(only["items"]) == 2

        # keyset paging
        page = client.get("/v1/llm-requests", params={"limit": 2}).json()
        assert len(page["items"]) == 2 and page["next_cursor"] == 2000
        page2 = client.get(
            "/v1/llm-requests", params={"limit": 2, "before_ts": page["next_cursor"]}
        ).json()
        assert [it["ts_start"] for it in page2["items"]] == [1000]

        stats = client.get("/v1/llm-requests/stats").json()
        assert stats["n"] == 3 and stats["n_error"] == 1
        assert stats["prompt_tokens"] == 180


# ------------------------------- /v1/summaries ------------------------------ #


async def _add(db, ts, app, title, desc, cat):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=title)
    rid = await db.insert_record(ctx, reason="test", ts_start=ts)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts + 60_000, rid))
        await db.conn.commit()
    await db.set_category_final(rid, cat)
    await db.save_description(rid, desc)


async def test_summaries_day_view_includes_all_grains(db):
    # 2001-06-09 09:00 activity → build the day cascade, then a day narrative.
    ts = _ms(2001, 6, 9, 9)
    await _add(db, ts, "Code", "main.py", "写 outbox 队列", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)
    day = await db.get_summary("day", scope_key(ts, "day", 4))
    await db.save_summary_narrative(
        day["id"],
        description="这一天在写 outbox",
        evaluation="专注",
        body_json='{"key_points": ["写 outbox"]}',
        src_tokens=5,
        out_tokens=3,
        compression_ratio=0.6,
    )

    app = create_app(db)
    with TestClient(app) as client:
        data = client.get("/v1/summaries", params={"day": "2001-06-09"}).json()

    assert data["day"] == "2001-06-09"
    assert set(data["grains"]) == {"5min", "1h", "6h", "day", "week"}
    # the 5min window for 09:00 overlaps the day and carries metrics
    leaves = data["grains"]["5min"]
    assert any(w["window_start"] == _ms(2001, 6, 9, 9) for w in leaves)
    # the narrated day window shows its narrative + key_points
    day_win = data["grains"]["day"][0]
    assert "outbox" in day_win["description"]
    assert day_win["key_points"] == ["写 outbox"]
    assert day_win["record_count"] >= 1


async def test_summaries_week_overlaps_day(db):
    # the week window starts Monday (before the day) — overlap query must still
    # return it for any day inside the week.
    ts = _ms(2001, 6, 9, 9)  # 2001-06-09 is a Saturday; its ISO week starts 06-04
    await _add(db, ts, "Code", "x", "y", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)

    app = create_app(db)
    with TestClient(app) as client:
        data = client.get("/v1/summaries", params={"day": "2001-06-09"}).json()
    assert len(data["grains"]["week"]) == 1  # week row caught by overlap
