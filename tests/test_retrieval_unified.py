"""Unified retrieval (W4): keyword + semantic fusion shared by REST / agent /
MCP, candidate-stage filters, narrative FTS, period-aware stats, and report
grounding."""

from __future__ import annotations

import datetime as _dt
import json
import time

import numpy as np
import pytest

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent import tools as agent_tools
from timetrace.server.agent.runner import _SYSTEM_PROMPT
from timetrace.server.db import Database
from timetrace.server.report.generator import _prefetch
from timetrace.server.retrieval import retrieve_records
from timetrace.server.summary.rollup import MetricsCascadeBuilder

# Tiny "semantic space": each concept is one axis; synonyms share an axis.
_AXES = {"数据库": 0, "sqlite": 0, "存储引擎": 0, "游戏": 1, "鸣潮": 1, "论文": 2, "overleaf": 2}


def _vec(text: str) -> bytes:
    v = np.full(3, 0.01, dtype=np.float32)
    for word, axis in _AXES.items():
        if word in text.lower():
            v[axis] += 1.0
    return v.tobytes()


class FakeEmbedder:
    model = "fake"
    dim = 3

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> bytes:
        self.calls.append(text)
        return _vec(text)


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


async def _add(db, ts, app, desc, cat="work", dur=60_000):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=app)
    rid = await db.insert_record(ctx, reason="test", ts_start=ts)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts + dur, rid))
        await db.conn.commit()
    await db.set_category_final(rid, cat)
    await db.save_description(rid, desc)
    await db.save_text_embedding(rid, _vec(desc), "fake")
    return rid


def _now_ms() -> int:
    return int(time.time() * 1000)


async def test_semantic_channel_finds_synonyms_keyword_misses(db):
    t = _now_ms() - 3_600_000
    hit = await _add(db, t, "Chrome", "在读 SQLite 文档，研究 WAL")
    await _add(db, t + 60_000, "Game", "在玩鸣潮", cat="entertainment")
    emb = FakeEmbedder()
    out = await agent_tools.search_activity(db, "存储引擎", hours_back=24, embedding=emb)
    assert out["semantic_channel"] == "ok"
    assert out["items"][0]["id"] == hit
    assert out["items"][0]["matched_by"] == ["semantic"]
    # without an embedder the same query degrades to keyword-only and finds nothing
    plain = await agent_tools.search_activity(db, "存储引擎", hours_back=24)
    assert plain["semantic_channel"] == "unavailable" and plain["items"] == []


async def test_filters_apply_inside_candidate_channels(db):
    t = _now_ms() - 3_600_000
    # many strong matches in one app would crowd a global top-N…
    for i in range(30):
        await _add(db, t + i * 1000, "Chrome", f"SQLite 数据库 #{i}")
    only = await _add(db, t + 40_000, "Code", "写 SQLite 数据库迁移")
    res = await retrieve_records(
        db, FakeEmbedder(), "数据库", 0, _now_ms(), limit=5, apps=["Code"]
    )
    # …but scoped retrieval still returns the in-scope record
    assert [rid for rid, _ in res.ranked] == [only]


async def test_dispatch_never_takes_embedding_from_model_args(db):
    out = await agent_tools.dispatch_tool(
        db, "search_activity", {"query": "x", "embedding": "evil"}, embedding=None
    )
    assert "error" not in out


async def test_search_summaries_uses_fulltext_and_short_like(db):
    t = int(_dt.datetime(2001, 6, 9, 9).timestamp() * 1000)
    await _add(db, t, "Code", "x")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)
    row = await db.get_summary("day", "2001-06-09")
    await db.save_summary_narrative(
        row["id"],
        description="上午在 overleaf 上改论文第三章",
        evaluation="专注",
        body_json=json.dumps({"key_points": ["改论文"]}, ensure_ascii=False),
        src_tokens=1,
        out_tokens=1,
        compression_ratio=None,
    )
    kw = dict(grain="day", period="custom", start_iso="2001-06-09 00:00", end_iso="2001-06-11")
    long_q = await agent_tools.search_summaries(db, query="overleaf", **kw)
    short_q = await agent_tools.search_summaries(db, query="论文", **kw)
    miss = await agent_tools.search_summaries(db, query="游戏", **kw)
    assert long_q["count"] == 1 and short_q["count"] == 1 and miss["count"] == 0


async def test_breakdown_period_today_is_civil_not_rolling(db, monkeypatch):
    now = int(_dt.datetime(2026, 10, 22, 15).timestamp() * 1000)
    monkeypatch.setattr(agent_tools.time, "time", lambda: now / 1000)
    yesterday_evening = int(_dt.datetime(2026, 10, 21, 20).timestamp() * 1000)
    this_morning = int(_dt.datetime(2026, 10, 22, 9).timestamp() * 1000)
    await _add(db, yesterday_evening, "Game", "鸣潮", cat="entertainment")
    await _add(db, this_morning, "Code", "写代码")
    rolling = await agent_tools.get_app_breakdown(db, hours_back=24)
    today = await agent_tools.get_app_breakdown(db, period="today")
    assert {i["app_name"] for i in rolling["items"]} == {"Game", "Code"}
    assert {i["app_name"] for i in today["items"]} == {"Code"}
    assert today["period"] == "today" and today["hours_back"] is None
    bad = await agent_tools.get_category_stats(db, period="yesterday")
    assert "error" in bad


async def test_report_prefetch_carries_stats_and_narratives(db):
    t = _now_ms() - 2 * 3_600_000
    await _add(db, t, "Code", "x")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)
    rows = await db.get_summaries_overlapping("6h", t, t + 1)
    await db.save_summary_narrative(
        rows[0]["id"],
        description="下午在重构检索层",
        evaluation="",
        body_json=json.dumps({"key_points": ["合并检索通道"]}, ensure_ascii=False),
        src_tokens=1,
        out_tokens=1,
        compression_ratio=None,
    )
    text = await _prefetch(db, "recent_24h", t - 3_600_000, _now_ms())
    assert "seconds_by_category" in text and "重构检索层" in text and "合并检索通道" in text


def test_agent_prompt_is_summary_first():
    assert "search_summaries" in _SYSTEM_PROMPT and "period" in _SYSTEM_PROMPT
