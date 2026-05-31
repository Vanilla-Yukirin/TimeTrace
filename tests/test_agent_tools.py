"""Tests for the shared agent activity tools (read tools + apply_label).

Real SQLite under tmp_path, same as test_storage. No LLM involved here — the
tool-calling loop (runner.py) needs a live model and is smoke-tested separately.
"""

from __future__ import annotations

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


async def test_get_category_stats_buckets_uncategorized(db):
    await _insert(db)
    res = await agent_tools.get_category_stats(db, hours_back=24)
    cats = {it["category"] for it in res["items"]}
    assert "uncategorized" in cats


async def test_apply_label_sets_category_final(db):
    rid = await _insert(db)
    res = await agent_tools.apply_label(db, record_id=rid, category="work/coding")
    assert res["status"] == "labeled"
    assert res["category_after"] == "work/coding"
    assert await db.get_category_final(rid) == "work/coding"


async def test_apply_label_resolves_by_chinese_name(db):
    rid = await _insert(db)
    res = await agent_tools.apply_label(db, record_id=rid, category="工作/编程")
    assert res["category_after"] == "work/coding"
    assert await db.get_category_final(rid) == "work/coding"


async def test_apply_label_rejects_unknown_category(db):
    rid = await _insert(db)
    res = await agent_tools.apply_label(db, record_id=rid, category="nonsense")
    assert "error" in res
    assert "work/coding" in res["valid_categories"]


async def test_apply_label_rejects_unknown_record(db):
    res = await agent_tools.apply_label(db, record_id="does-not-exist", category="work/coding")
    assert "error" in res


async def test_apply_label_then_category_stats_reflects_label(db):
    rid = await _insert(db)
    await agent_tools.apply_label(db, record_id=rid, category="work/coding")
    res = await agent_tools.get_category_stats(db, hours_back=24)
    cats = {it["category"] for it in res["items"]}
    assert "work/coding" in cats


async def test_dispatch_tool_unknown_returns_error(db):
    res = await agent_tools.dispatch_tool(db, "no_such_tool", {})
    assert "error" in res


async def test_dispatch_tool_routes_to_impl(db):
    await _insert(db)
    res = await agent_tools.dispatch_tool(db, "get_recent_activity", {"hours_back": 1})
    assert "items" in res
