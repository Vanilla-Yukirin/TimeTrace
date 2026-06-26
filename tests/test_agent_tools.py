"""Tests for the shared agent activity tools (read tools + apply_label).

Real SQLite under tmp_path, same as test_storage. No LLM involved here — the
tool-calling loop (runner.py) needs a live model and is smoke-tested separately.
"""

from __future__ import annotations

import datetime as _dt
import time

import pytest

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent import tools as agent_tools
from timetrace.server.db import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


async def _insert(database, app="Cursor", title="main.py - TimeTrace"):
    return await database.insert_record(
        CaptureContext(app_name=app, process_name=app.lower() + ".exe", window_title=title),
        reason="test",
    )


async def _insert_at(database, ts_start, app="Cursor", title="x"):
    return await database.insert_record(
        CaptureContext(app_name=app, process_name=app.lower() + ".exe", window_title=title),
        reason="test",
        ts_start=ts_start,
    )


def _ms(y, mo, d, h, mi=0, s=0):
    return int(_dt.datetime(y, mo, d, h, mi, s).timestamp() * 1000)


async def test_search_activity_finds_by_title(db):
    await _insert(db, title="鸣潮 Wuthering Waves")
    res = await agent_tools.search_activity(db, query="鸣潮")
    assert res["query"] == "鸣潮"
    assert any("鸣潮" in (it["window_title"] or "") for it in res["items"])


async def test_get_recent_activity_returns_items_with_id(db):
    await _insert(db)
    res = await agent_tools.get_recent_activity(db, hours_back=24)
    assert res["hours_back"] == 24
    assert len(res["items"]) >= 1
    assert "id" in res["items"][0]


async def test_get_app_breakdown_groups_by_app(db):
    await _insert(db, app="Cursor")
    await _insert(db, app="Cursor")
    await _insert(db, app="Chrome")
    res = await agent_tools.get_app_breakdown(db, hours_back=24)
    apps = {it["app_name"]: it["records"] for it in res["items"]}
    assert apps.get("Cursor") == 2
    assert apps.get("Chrome") == 1


async def test_get_app_breakdown_clamps_negative_durations(db):
    """Legacy rows can have ts_end < ts_start (the pre-fix ingest restamp bug).
    Such a record must contribute 0 — never a negative — to its app's total,
    so the breakdown never reports a nonsensical negative duration."""
    now = int(time.time() * 1000)
    rid = await db.insert_record(
        CaptureContext(app_name="Cursor", process_name="cursor.exe", window_title="x"),
        reason="test",
        ts_start=now - 1_000,  # inside the 24h window
    )
    await db.close_record(rid, ts_end=now - 181_000)  # ts_end 3 min BEFORE ts_start → negative
    res = await agent_tools.get_app_breakdown(db, hours_back=24)
    cursor = next((it for it in res["items"] if it["app_name"] == "Cursor"), None)
    assert cursor is not None
    assert cursor["total_seconds"] == 0  # clamped, not -180
    assert res["total_seconds"] >= 0


async def test_get_app_breakdown_caps_sleep_inflated_spans(db):
    """A record whose span exceeds the plausibility cap (laptop sleep / lid-
    close left the last pre-sleep record open ~hours) contributes 0, not its
    inflated duration — otherwise one 9h sleep gap dwarfs the real day."""
    now = int(time.time() * 1000)
    rid = await db.insert_record(
        CaptureContext(app_name="QQ", process_name="qq.exe", window_title="x"),
        reason="test",
        ts_start=now - 9 * 3600 * 1000,  # 9h ago, inside the 24h window
    )
    await db.close_record(rid, ts_end=now)  # ~9h span = sleep artifact
    res = await agent_tools.get_app_breakdown(db, hours_back=24)
    qq = next((it for it in res["items"] if it["app_name"] == "QQ"), None)
    assert qq is not None
    assert qq["total_seconds"] == 0  # capped, not 32400
    assert res["capped_per_record_seconds"] == 300


async def test_get_category_stats_buckets_unclassified_not_uncategorized(db):
    """A record with no analysis row yet is SYSTEM-INTERNAL backlog, not the
    user-facing 'uncategorized' category. It must land in the distinct
    '_unclassified' bucket (flagged is_unclassified) so reports don't narrate
    it as real behaviour — this is the NULL-vs-uncategorized conflation fix."""
    await _insert(db)
    res = await agent_tools.get_category_stats(db, hours_back=24)
    by_cat = {it["category"]: it for it in res["items"]}
    assert "_unclassified" in by_cat
    assert by_cat["_unclassified"].get("is_unclassified") is True
    assert "uncategorized" not in by_cat  # NOT conflated with the real category
    assert "work" in res["categories_legend"]


async def test_apply_label_sets_category_final(db):
    rid = await _insert(db)
    res = await agent_tools.apply_label(db, record_id=rid, category="work")
    assert res["status"] == "labeled"
    assert res["category_after"] == "work"
    assert await db.get_category_final(rid) == "work"


async def test_apply_label_resolves_by_chinese_name(db):
    rid = await _insert(db)
    res = await agent_tools.apply_label(db, record_id=rid, category="工作")
    assert res["category_after"] == "work"
    assert await db.get_category_final(rid) == "work"


async def test_apply_label_rejects_unknown_category(db):
    rid = await _insert(db)
    res = await agent_tools.apply_label(db, record_id=rid, category="nonsense")
    assert "error" in res
    assert "work" in res["valid_categories"]


async def test_apply_label_rejects_unknown_record(db):
    res = await agent_tools.apply_label(db, record_id="does-not-exist", category="work/coding")
    assert "error" in res


async def test_apply_label_then_category_stats_reflects_label(db):
    rid = await _insert(db)
    await agent_tools.apply_label(db, record_id=rid, category="work")
    res = await agent_tools.get_category_stats(db, hours_back=24)
    cats = {it["category"] for it in res["items"]}
    assert "work" in cats


async def test_dispatch_tool_unknown_returns_error(db):
    res = await agent_tools.dispatch_tool(db, "no_such_tool", {})
    assert "error" in res


async def test_dispatch_tool_routes_to_impl(db):
    await _insert(db)
    res = await agent_tools.dispatch_tool(db, "get_recent_activity", {"hours_back": 1})
    assert "items" in res


# --- MCP usability fixes (limit / absolute window / cursor / FTS multi-word) --- #


async def test_get_recent_activity_limit_exceeds_old_200_cap(db):
    # #1: a full day can be >200 records; the cap was raised to 2000.
    now = int(time.time() * 1000)
    for i in range(205):
        await _insert_at(db, ts_start=now - (i + 1) * 1000)
    res = await agent_tools.get_recent_activity(db, hours_back=24, limit=205)
    assert res["count"] == 205  # would have been clamped to 200 before
    assert len(res["items"]) == 205


async def test_get_recent_activity_absolute_window(db):
    # #3: explicit start_iso/end_iso bounds win over hours_back.
    await _insert_at(db, ts_start=_ms(2099, 6, 9, 9, 0), title="early")
    await _insert_at(db, ts_start=_ms(2099, 6, 9, 10, 0), title="mid")
    await _insert_at(db, ts_start=_ms(2099, 6, 9, 14, 0), title="late")
    res = await agent_tools.get_recent_activity(
        db, start_iso="2099-06-09 09:30:00", end_iso="2099-06-09 12:00:00"
    )
    assert [it["window_title"] for it in res["items"]] == ["mid"]


async def test_get_recent_activity_bad_iso_returns_error(db):
    res = await agent_tools.get_recent_activity(db, start_iso="not-a-date")
    assert "error" in res


async def test_get_recent_activity_cursor_paginates_full_set(db):
    # #2: page a full window via next_cursor without overlap or gaps.
    now = int(time.time() * 1000)
    ids_in_order = []
    for i in range(5):
        rid = await _insert_at(db, ts_start=now - (5 - i) * 60_000, title=f"rec{i}")
        ids_in_order.append(rid)  # ascending ts_start
    seen: list[str] = []
    cursor = None
    for _ in range(10):  # safety bound
        res = await agent_tools.get_recent_activity(db, hours_back=24, limit=2, cursor=cursor)
        seen.extend(it["id"] for it in res["items"])
        cursor = res["next_cursor"]
        if cursor is None:
            break
    assert seen == ids_in_order  # full set, in order, no overlap, terminates


async def test_search_activity_multiword_matches_any_order(db):
    # #4: "judge replay history" matches a title with all 3 words non-contiguous,
    # and does NOT match a title missing one of them.
    await _insert(db, title="judge the replay in history view")
    await _insert(db, title="judge the replay only")
    res = await agent_tools.search_activity(db, query="judge replay history")
    titles = [it["window_title"] for it in res["items"]]
    assert any("history view" in t for t in titles)
    assert all("only" not in t for t in titles)


async def test_search_summaries_reads_narrative_and_folds(db):
    # Build a day's metrics cascade + a hand-written day narrative, then read it
    # back through search_summaries with the drill-down hint.
    from timetrace.common.config import RollupConfig
    from timetrace.server.summary.rollup import MetricsCascadeBuilder
    from timetrace.server.summary.windows import scope_key

    ts = _ms(2001, 6, 9, 9, 0)  # sentinel date, never "today"
    rid = await _insert_at(db, ts, app="Code", title="main.py")
    await db.set_category_final(rid, "work")
    await db.save_description(rid, "编辑 outbox 队列代码")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)

    day = await db.get_summary("day", scope_key(ts, "day", 4))
    await db.save_summary_narrative(
        day["id"],
        description="这一天主要在写 outbox 队列代码",
        evaluation="专注",
        body_json='{"key_points": ["写 outbox 队列"]}',
        src_tokens=10,
        out_tokens=5,
        compression_ratio=0.5,
    )

    res = await agent_tools.search_summaries(
        db,
        grain="day",
        period="custom",
        start_iso="2001-06-09 00:00:00",
        end_iso="2001-06-10 00:00:00",
    )
    assert res["count"] >= 1
    it = res["items"][0]
    assert "outbox" in it["description"]
    assert it["key_points"] == ["写 outbox 队列"]
    assert it["narrated"] is True
    assert res["drill_down_grain"] == "6h"  # fold day → 6h

    # query filter excludes non-matching windows
    miss = await agent_tools.search_summaries(
        db,
        grain="day",
        period="custom",
        start_iso="2001-06-09 00:00:00",
        end_iso="2001-06-10 00:00:00",
        query="不存在的关键词",
    )
    assert miss["count"] == 0


async def test_search_summaries_rejects_bad_grain(db):
    res = await agent_tools.search_summaries(db, grain="nope", period="today")
    assert "error" in res and "valid_grains" in res
