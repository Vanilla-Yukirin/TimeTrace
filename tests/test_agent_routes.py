"""HTTP-layer tests for /v1/agent/chat and /v1/reports/* via in-process ASGI.

No real LLM: these lock the route wiring + degraded behavior (no-VLM → SSE error
/ 503, empty → 404, stored report round-trips). The live model path is smoke-
tested separately against SiliconFlow.
"""

from __future__ import annotations

import json

import httpx
import pytest

from timetrace.common.config import StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.db import Database


@pytest.fixture
async def client(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    db = Database(cfg)
    await db.init()
    # No vlm_cfg, no users → agent/report routes mounted, auth-open, LLM absent.
    app = create_app(db, storage_cfg=cfg)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, db
    await db.close()


async def test_reports_latest_404_when_empty(client):
    c, _ = client
    res = await c.get("/v1/reports/latest?scope=recent_24h")
    assert res.status_code == 404


async def test_reports_latest_unknown_scope_422(client):
    c, _ = client
    res = await c.get("/v1/reports/latest?scope=bogus")
    assert res.status_code == 422


async def test_reports_latest_returns_stored(client):
    c, db = client
    await db.insert_report(
        scope="recent_24h",
        period_start=1,
        period_end=2,
        fmt="html",
        content="<div>hi</div>",
        model="m",
    )
    res = await c.get("/v1/reports/latest?scope=recent_24h")
    assert res.status_code == 200
    body = res.json()
    assert body["content"] == "<div>hi</div>"
    assert body["scope"] == "recent_24h"


async def test_reports_generate_503_without_vlm(client):
    c, _ = client
    res = await c.post("/v1/reports/generate", json={"scope": "recent_24h"})
    assert res.status_code == 503


async def test_reports_generate_stream_errors_without_vlm(client):
    c, _ = client
    res = await c.post("/v1/reports/generate/stream", json={"scope": "recent_24h"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    payloads = [
        json.loads(line[5:].strip()) for line in res.text.splitlines() if line.startswith("data:")
    ]
    assert payloads, "expected at least one SSE data event"
    assert payloads[0]["type"] == "error"
    assert "未配置" in payloads[0]["message"]


async def test_agent_chat_streams_error_without_vlm(client):
    c, _ = client
    res = await c.post(
        "/v1/agent/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    # Single SSE record: data: {"type":"error",...}
    payloads = [
        json.loads(line[5:].strip()) for line in res.text.splitlines() if line.startswith("data:")
    ]
    assert payloads, "expected at least one SSE data event"
    assert payloads[0]["type"] == "error"
    assert "未配置" in payloads[0]["message"]
