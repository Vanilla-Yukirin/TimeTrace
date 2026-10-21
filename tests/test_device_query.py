"""Device query loop (W5): device directory, device-scoped search/stats/tools,
device labels in results, and validation."""

from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent import tools as agent_tools
from timetrace.server.agent.runner import AgentRunner
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.mcp_layer.server import build_mcp_server

DEV_A = "11111111-1111-4111-8111-111111111111"
DEV_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    for dev, name, desc in ((DEV_A, "办公电脑", "公司台式机，写代码"), (DEV_B, "家用电脑", "")):
        await database.validate_or_register_device(dev, token_fingerprint=None, token_label=None)
        await database.update_device(dev, name=name, description=desc)
    yield database
    await database.close()


async def _add(db, ts, app, desc, device_id, dur=60_000, cat="work"):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=app)
    rid = await db.insert_record(ctx, reason="t", ts_start=ts, device_id=device_id)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts + dur, rid))
        await db.conn.commit()
    await db.set_category_final(rid, cat)
    await db.save_description(rid, desc)
    await db.save_text_embedding(rid, np.ones(3, dtype=np.float32).tobytes(), "fake")
    return rid


async def _seed(db):
    t = int(time.time() * 1000) - 2 * 3_600_000
    # same instant on two devices: parallel use
    a = await _add(db, t, "Code", "编辑 SQLite 迁移脚本", DEV_A)
    b = await _add(db, t, "Chrome", "阅读 SQLite 文档", DEV_B)
    legacy = await _add(db, t + 120_000, "Notepad", "SQLite 笔记", None)
    return t, a, b, legacy


async def test_devices_route_and_validation(db):
    await _seed(db)
    app = create_app(db)
    with TestClient(app) as client:
        body = client.get("/v1/devices").json()
        assert {d["label"] for d in body["items"]} == {"办公电脑", "家用电脑"}
        assert body["unknown"]["record_count"] == 1
        assert (
            client.get("/v1/search/text", params={"q": "SQLite", "device_id": "x"}).status_code
            == 422
        )
        upper = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        assert (
            client.get("/v1/records", params={"start": 0, "end": 1, "device_id": upper}).status_code
            == 422
        )


async def test_text_search_scopes_per_device_and_returns_device(db):
    _, a, b, legacy = await _seed(db)
    app = create_app(db)
    with TestClient(app) as client:
        every = client.get("/v1/search/text", params={"q": "SQLite"}).json()["items"]
        assert {i["record_id"] for i in every} == {a, b, legacy}
        only_b = client.get("/v1/search/text", params={"q": "SQLite", "device_id": DEV_B}).json()
        assert [i["record_id"] for i in only_b["items"]] == [b]
        assert only_b["items"][0]["device_id"] == DEV_B
        unknown = client.get(
            "/v1/search/text", params={"q": "SQLite", "device_id": "_unknown_device"}
        ).json()
        assert [i["record_id"] for i in unknown["items"]] == [legacy]


async def test_agent_tools_scope_and_label(db):
    await _seed(db)
    res = await agent_tools.search_activity(db, "SQLite", hours_back=24, device_id=DEV_A)
    assert [i["device"] for i in res["items"]] == ["办公电脑"]
    apps = await agent_tools.get_app_breakdown(db, period="today", device_id=DEV_B)
    assert [i["app_name"] for i in apps["items"]] == ["Chrome"]
    cats = await agent_tools.get_category_stats(db, hours_back=24, device_id="_unknown_device")
    assert cats["total_seconds"] == 60
    recent = await agent_tools.get_recent_activity(db, hours_back=24)
    assert {i["device"] for i in recent["items"]} == {"办公电脑", "家用电脑", "未知设备"}
    bad = await agent_tools.get_app_breakdown(db, device_id="nope")
    assert "error" in bad


async def test_query_stats_wall_clock_vs_per_device(db):
    t, *_ = await _seed(db)
    kw = dict(
        period="custom",
        start_iso=agent_tools.ms_to_iso(t - 1000),
        end_iso=agent_tools.ms_to_iso(t + 600_000),
    )
    wall = await agent_tools.query_stats(db, metric="active_seconds", **kw)
    per_dev = await agent_tools.query_stats(db, metric="seconds_by_device", **kw)
    # A and B overlap fully (60s), legacy is separate (60s) → union 120s, sum 180s
    assert wall["total_seconds"] == 120
    assert per_dev["total_seconds"] == 180
    assert {i["key"] for i in per_dev["items"]} == {"办公电脑", "家用电脑", "未知设备"}
    only_a = await agent_tools.query_stats(db, metric="active_seconds", device_id=DEV_A, **kw)
    assert only_a["total_seconds"] == 60 and only_a["device_id"] == DEV_A


async def test_list_devices_tool_and_prompt_context(db):
    await _seed(db)
    out = await agent_tools.dispatch_tool(db, "list_devices", {})
    assert out["count"] == 2 and out["unknown_device"]["record_count"] == 1
    assert any(d["description"] == "公司台式机，写代码" for d in out["items"])

    class _Cfg:
        base_url = "http://127.0.0.1:9"
        api_key = "x"
        model = "m"
        disable_thinking = False

    runner = AgentRunner(db, _Cfg())
    ctx = await runner._device_context()
    await runner.aclose()
    assert DEV_A in ctx and "公司台式机" in ctx


async def test_mcp_exposes_device_tools(db):
    await _seed(db)
    mcp = build_mcp_server(db, vlm_cfg=None)
    names = {t.name for t in await mcp.list_tools()}
    assert {"list_devices", "query_stats"} <= names
