"""MCP layer tests — build_mcp_server tools wired against a real SQLite db."""

import json
import time
from typing import Any

import pytest

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.db import Database
from timetrace.server.mcp_layer.server import build_mcp_server


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


def _structured(result: Any) -> dict:
    """``FastMCP.call_tool`` returns a list of MCP ContentBlock items where
    each TextContent's ``text`` is the JSON-stringified return value.
    Parse the first TextContent back to a dict for assertions.
    """
    if isinstance(result, list) and result:
        text = getattr(result[0], "text", None)
        if text is not None:
            return json.loads(text)
    if isinstance(result, tuple) and len(result) >= 2:
        return result[1]
    if isinstance(result, dict):
        return result
    raise AssertionError(f"unexpected MCP tool result shape: {type(result).__name__} {result!r}")


async def _seed_records(db: Database, n: int = 3) -> list[str]:
    """Insert n records, close each ~60s after its own ts_start (positive
    duration). All within the last hour. Returns the ids.
    """
    ids = []
    apps = ["Visual Studio Code", "Wuthering Waves", "Weixin"]
    titles = ["main.py - TimeTrace", "鸣潮主城", "群聊 - 工设2"]
    for i in range(n):
        ctx = CaptureContext(
            app_name=apps[i % 3],
            process_name=f"app{i}.exe",
            window_title=titles[i % 3],
        )
        rid = await db.insert_record(ctx, reason="heartbeat")
        # Re-read the row's ts_start so close uses a later ts_end.
        row = await db.get_record_by_id(rid)
        await db.close_record(rid, ts_end=row["ts_start"] + 60_000)
        ids.append(rid)
    return ids


async def test_build_mcp_server_returns_fastmcp_instance(db):
    mcp = build_mcp_server(db, vlm_cfg=None)
    assert mcp.name == "timetrace"


async def test_search_activity_tool_hits_app_name(db):
    await _seed_records(db, n=3)
    mcp = build_mcp_server(db, vlm_cfg=None)

    result = await mcp.call_tool("search_activity", {"query": "Weixin", "limit": 5})
    items = _structured(result)["items"]
    assert any("Weixin" in (it.get("app_name") or "") for it in items)


async def test_search_activity_tool_cjk_via_fts(db):
    rid = await db.insert_record(
        CaptureContext(app_name="App", process_name="p", window_title="w"),
        reason="heartbeat",
    )
    await db.mark_pending(rid)
    await db.save_description(rid, "鸣潮游戏内哥莱姆区域广场场景")
    mcp = build_mcp_server(db, vlm_cfg=None)

    result = await mcp.call_tool("search_activity", {"query": "哥莱姆", "limit": 5})
    items = _structured(result)["items"]
    assert len(items) == 1
    assert items[0]["id"] == rid


async def test_get_recent_activity_tool(db):
    await _seed_records(db, n=3)
    mcp = build_mcp_server(db, vlm_cfg=None)

    result = await mcp.call_tool("get_recent_activity", {"hours_back": 1, "limit": 10})
    body = _structured(result)
    assert body["hours_back"] == 1
    items = body["items"]
    assert len(items) == 3
    assert all("app_name" in it for it in items)


async def test_get_app_breakdown_aggregates_durations(db):
    await _seed_records(db, n=3)
    mcp = build_mcp_server(db, vlm_cfg=None)

    result = await mcp.call_tool("get_app_breakdown", {"hours_back": 1, "top_n": 10})
    items = _structured(result)["items"]
    apps = {it["app_name"] for it in items}
    assert apps == {"Visual Studio Code", "Wuthering Waves", "Weixin"}
    assert all(isinstance(it["total_seconds"], int) and it["total_seconds"] >= 0 for it in items)
    durations = [it["total_seconds"] for it in items]
    assert durations == sorted(durations, reverse=True)


async def test_ask_agent_degrades_gracefully_without_vlm(db):
    await _seed_records(db, n=1)
    mcp = build_mcp_server(db, vlm_cfg=None)

    result = await mcp.call_tool(
        "ask_agent", {"question": "我昨天在干啥", "hours_back": 24}
    )
    body = _structured(result)
    assert body["model"] is None
    assert "not configured" in body["answer"].lower()
