"""NarrativeBuilder (Phase 3 slice 3a) — mock-LLM unit tests.

No box VLM: the LLM is injected. Verifies the builder assembles the right
context (leaf reads frames, rollup reads child narratives), parses structured
output robustly, and computes the compression scent.
"""

from __future__ import annotations

import datetime as _dt
import json
import time

import pytest

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.db import Database
from timetrace.server.summary.narrative import (
    NarrativeBuilder,
    NarrativeCascade,
    _parse_narrative,
)
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


async def _add(db, ts, dur, app, title, desc, cat):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=title)
    rid = await db.insert_record(ctx, reason="test", ts_start=ts)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts + dur, rid))
        await db.conn.commit()
    await db.set_category_final(rid, cat)  # creates the analysis row
    await db.save_description(rid, desc)  # fills vlm_desc on it
    return rid


class _MockLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        self.calls.append(user)
        return self.payload


_PAYLOAD = json.dumps(
    {
        "description": "上午在调 outbox 队列代码，并看了一个 PR",
        "key_points": ["调试 outbox 队列", "审阅 GitHub PR"],
        "evaluation": "一段专注的工作切片",
    },
    ensure_ascii=False,
)


async def test_build_one_leaf_reads_frames_and_parses(db):
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 60_000, "Code", "main.py", "编辑 outbox 队列代码", "work")
    await _add(db, ts + 60_000, 60_000, "Chrome", "github PR", "看 PR review", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)
    leaf = await db.get_summary("5min", scope_key(ts, "5min", CUT))

    mock = _MockLLM(_PAYLOAD)
    out = await NarrativeBuilder(db, mock).build_one(leaf)

    assert "outbox" in out["description"]
    assert out["evaluation"]
    body = json.loads(out["body_json"])
    assert body["key_points"] == ["调试 outbox 队列", "审阅 GitHub PR"]
    assert body["source_units"] == 2  # two frames fed to the LLM
    assert out["src_tokens"] > 0 and out["out_tokens"] > 0
    assert out["compression_ratio"] is not None
    # the assembled prompt actually carried the frame descriptions
    assert "outbox" in mock.calls[0] and "PR review" in mock.calls[0]


async def test_build_one_rollup_reads_child_narratives(db):
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 60_000, "Code", "main.py", "x", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)
    # give the 5min leaf a narrative, then build its parent (1h) narrative from it
    async with db.lock:
        await db.conn.execute(
            "UPDATE summaries SET description=? WHERE grain='5min'",
            ("子窗叙述：在写金字塔叙述层",),
        )
        await db.conn.commit()
    one_h = await db.get_summary("1h", scope_key(ts, "1h", CUT))

    mock = _MockLLM(_PAYLOAD)
    await NarrativeBuilder(db, mock).build_one(one_h)
    # the 1h prompt is a summary-of-summaries: it carried the 5min child's narrative
    assert "金字塔叙述层" in mock.calls[0]


def test_parse_narrative_handles_fenced_json():
    raw = '```json\n{"description":"x","key_points":["a"],"evaluation":"y"}\n```'
    p = _parse_narrative(raw)
    assert p["description"] == "x"
    assert p["key_points"] == ["a"]
    assert p["evaluation"] == "y"


def test_parse_narrative_falls_back_on_garbage():
    p = _parse_narrative("not json at all")
    assert p["description"] == "not json at all"
    assert p["key_points"] == []


def test_parse_narrative_salvages_truncated_json():
    # A max_tokens truncation: description-first, clipped mid-value, JSON never
    # closes. We should recover the (clipped) description, not dump the ```json.
    raw = '```json\n{\n  "description": "16:00 起在调脚本，随后切到 QQ 群看了一会'
    p = _parse_narrative(raw)
    assert p["description"].startswith("16:00 起在调脚本")
    assert "```json" not in p["description"]
    assert "description" not in p["description"]  # no leaked JSON key
    assert p["key_points"] == []  # the later fields were cut off


def test_parse_narrative_salvages_partial_with_keypoints():
    # description complete + key_points landed, but evaluation got truncated.
    raw = (
        '{"description": "写了金字塔叙述层",'
        ' "key_points": ["分级预算", "截断兜底"],'
        ' "evaluation": "一段专注的'
    )
    p = _parse_narrative(raw)
    assert p["description"] == "写了金字塔叙述层"
    assert p["key_points"] == ["分级预算", "截断兜底"]
    assert p["evaluation"].startswith("一段专注的")


async def test_leaf_with_no_described_frames_skips_llm(db):
    # A window_switch burst: records + category, but NO vlm_desc. The builder
    # must NOT call the LLM (it could only come back empty) — it writes a
    # deterministic metrics-only narrative so the loop never retries it.
    ts = _ms(2001, 6, 9, 9, 0)
    ctx = CaptureContext(app_name="Explorer", process_name="explorer", window_title="x")
    rid = await db.insert_record(ctx, reason="window_switch", ts_start=ts)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts + 60_000, rid))
        await db.conn.commit()
    await db.set_category_final(rid, "other")  # analysis row exists, but no description
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)
    leaf = await db.get_summary("5min", scope_key(ts, "5min", CUT))

    mock = _MockLLM(_PAYLOAD)
    out = await NarrativeBuilder(db, mock).build_one(leaf)
    assert mock.calls == []  # LLM never called
    assert "无可叙述" in out["description"]
    assert json.loads(out["body_json"])["metrics_only"] is True


async def test_empty_content_raises_and_stays_pending(db):
    # An always-thinking model can burn the whole budget on reasoning and return
    # empty content. That must NOT be saved as a done narrative — it should fail
    # so the row stays pending and a re-run retries it.
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 60_000, "Code", "main.py", "写代码", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)

    cascade = NarrativeCascade(db, NarrativeBuilder(db, _MockLLM("")))  # empty payload
    now = int(time.time() * 1000)
    counts = await cascade.narrate_range(_ms(2001, 6, 9, 0, 0), _ms(2001, 6, 10, 0, 0), now)
    assert sum(counts.values()) == 0  # nothing counted as narrated
    leaf = await db.get_summary("5min", scope_key(ts, "5min", CUT))
    assert not leaf["description"]  # left empty
    assert leaf["status"] != "narrated"  # still pending → will retry


async def test_parent_deferred_until_children_narrated(db):
    # A coarse window must NOT be narrated while its finer children are still
    # pending — else it's a premature metrics paraphrase that never self-corrects.
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 60_000, "Code", "main.py", "写代码", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)
    now = int(time.time() * 1000)
    cascade = NarrativeCascade(db, NarrativeBuilder(db, _MockLLM(_PAYLOAD)))
    one_h = await db.get_summary("1h", scope_key(ts, "1h", CUT))

    # 5min child still pending → gate blocks the 1h
    assert (
        await cascade._has_pending_children("1h", one_h["window_start"], one_h["window_end"], now)
        is True
    )
    # narrate the child, then the gate opens
    leaf = await db.get_summary("5min", scope_key(ts, "5min", CUT))
    out = await cascade._builder.build_one(leaf)
    await db.save_summary_narrative(leaf["id"], **out)
    assert (
        await cascade._has_pending_children("1h", one_h["window_start"], one_h["window_end"], now)
        is False
    )


async def test_save_narrative_keeps_single_current_fts_row(db):
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 60_000, "Code", "main.py", "写代码", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)
    leaf = await db.get_summary("5min", scope_key(ts, "5min", CUT))

    await db.save_summary_narrative(
        leaf["id"],
        description="第一次叙述",
        evaluation="正常",
        body_json='{"key_points": []}',
    )
    await db.save_summary_narrative(
        leaf["id"],
        description="第二次叙述",
        evaluation="已更新",
        body_json='{"key_points": []}',
    )

    async with db.conn.execute(
        "SELECT description, evaluation FROM summaries_fts WHERE summary_id=?",
        (leaf["id"],),
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["description"] == "第二次叙述"
    assert rows[0]["evaluation"] == "已更新"


async def test_narrate_range_grains_filter(db):
    # grains filter restricts which layers get (re)narrated — used to redo only
    # parent layers after a backlog drain without touching the good leaves.
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 60_000, "Code", "main.py", "写代码", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)
    now = int(time.time() * 1000)
    cascade = NarrativeCascade(db, NarrativeBuilder(db, _MockLLM(_PAYLOAD)))

    # narrate only the leaf
    counts = await cascade.narrate_range(
        _ms(2001, 6, 9, 0, 0), _ms(2001, 6, 10, 0, 0), now, grains=("5min",)
    )
    assert counts == {"5min": 1}  # only the 5min layer was walked
    # now redo only parents (children are done → gate passes)
    counts2 = await cascade.narrate_range(
        _ms(2001, 6, 9, 0, 0), _ms(2001, 6, 10, 0, 0), now, force=True, grains=("1h", "6h", "day")
    )
    assert set(counts2) == {"1h", "6h", "day"}  # leaves untouched
    assert counts2["1h"] == 1


async def test_narrate_cascade_bottom_up_and_idempotent(db):
    ts = _ms(2001, 6, 9, 9, 0)
    await _add(db, ts, 60_000, "Code", "main.py", "写代码", "work")
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(ts)

    mock = _MockLLM(_PAYLOAD)  # payload description mentions "outbox"
    cascade = NarrativeCascade(db, NarrativeBuilder(db, mock))
    now = int(time.time() * 1000)  # 2001 windows are long finalized
    counts = await cascade.narrate_range(_ms(2001, 6, 9, 0, 0), _ms(2001, 6, 10, 0, 0), now)
    # 5min..day sit inside the range; the week window starts Monday (before this
    # single-day range), so it's correctly NOT narrated from just one day.
    assert counts["5min"] == 1 and counts["1h"] == 1
    assert counts["6h"] == 1 and counts["day"] == 1
    assert counts["week"] == 0

    leaf = await db.get_summary("5min", scope_key(ts, "5min", CUT))
    day = await db.get_summary("day", scope_key(ts, "day", CUT))
    assert leaf["description"] and leaf["status"] == "narrated"
    assert day["description"] and day["status"] == "narrated"
    # bottom-up: leaf prompt carried the frame desc; parents carried child narrative
    assert "写代码" in mock.calls[0]
    assert any("outbox" in c for c in mock.calls[1:])

    # idempotent: nothing left pending → a second pass narrates 0
    again = await cascade.narrate_range(_ms(2001, 6, 9, 0, 0), _ms(2001, 6, 10, 0, 0), now)
    assert sum(again.values()) == 0
