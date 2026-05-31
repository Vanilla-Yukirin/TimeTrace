"""Tests for the report store (DB) + ReportGenerator (stubbed agent loop)."""

from __future__ import annotations

import pytest

from timetrace.common.config import StorageConfig, VLMConfig
from timetrace.server.db import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


async def test_insert_and_get_latest_report(db):
    await db.insert_report(
        scope="recent_24h", period_start=1, period_end=2, fmt="html", content="<p>a</p>", model="m"
    )
    await db.insert_report(
        scope="recent_24h", period_start=3, period_end=4, fmt="html", content="<p>b</p>", model="m"
    )
    latest = await db.get_latest_report("recent_24h")
    assert latest["content"] == "<p>b</p>"
    assert latest["scope"] == "recent_24h"
    assert await db.get_latest_report("recent_7d") is None


async def test_report_generator_stores_cleaned_html(db, monkeypatch):
    import timetrace.server.report.generator as gmod

    class _StubRunner:
        def __init__(self, *a, **k):
            pass

        async def run(self, messages, **k):
            yield {"type": "step", "phase": "tool_call", "tool": "get_app_breakdown", "args": {}}
            yield {"type": "token", "text": "```html\n<div>今天很努力</div>\n```"}
            yield {"type": "done", "records_consulted": 1, "model": "m", "tools_used": ["x"]}

        async def aclose(self):
            pass

    monkeypatch.setattr(gmod, "AgentRunner", _StubRunner)
    gen = gmod.ReportGenerator(db, VLMConfig(base_url="x", api_key="x", model="m"))
    res = await gen.generate("recent_24h")
    assert res["format"] == "html"
    # code fence stripped
    assert res["content"] == "<div>今天很努力</div>"
    latest = await db.get_latest_report("recent_24h")
    assert latest["content"] == "<div>今天很努力</div>"


async def test_report_generator_propagates_error(db, monkeypatch):
    import timetrace.server.report.generator as gmod

    class _ErrRunner:
        def __init__(self, *a, **k):
            pass

        async def run(self, messages, **k):
            yield {"type": "error", "message": "boom"}

        async def aclose(self):
            pass

    monkeypatch.setattr(gmod, "AgentRunner", _ErrRunner)
    gen = gmod.ReportGenerator(db, VLMConfig(base_url="x", api_key="x", model="m"))
    with pytest.raises(RuntimeError, match="boom"):
        await gen.generate("recent_24h")
