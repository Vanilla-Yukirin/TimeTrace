"""Per-device summary pyramid v2 (W6)."""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent import tools as agent_tools
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.summary.narrative import (
    DevicePyramidNarrator,
    NarrativeBuilder,
    _round_robin,
)
from timetrace.server.summary.rollup import MetricsCascadeBuilder
from timetrace.server.summary.windows import scope_key

CUT = 4
DEV_A = "11111111-1111-4111-8111-111111111111"
DEV_B = "22222222-2222-4222-8222-222222222222"


class _EchoLLM:
    """Returns a narrative naming the frames it was given."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        self.calls.append(user)
        tag = "A" if "办公电脑" in user else "B" if "家用电脑" in user else "G"
        return json.dumps(
            {
                "description": f"叙述-{tag}",
                "key_points": [f"重点-{tag}"],
                "evaluation": f"评-{tag}",
            },
            ensure_ascii=False,
        )


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    for dev, name in ((DEV_A, "办公电脑"), (DEV_B, "家用电脑")):
        await database.validate_or_register_device(dev, token_fingerprint=None, token_label=None)
        await database.update_device(dev, name=name, description="")
    yield database
    await database.close()


def _ms(h, mi=0):
    return int(_dt.datetime(2001, 6, 9, h, mi).timestamp() * 1000)


async def _add(db, ts, app, desc, device_id, dur=60_000):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=app)
    rid = await db.insert_record(ctx, reason="t", ts_start=ts, device_id=device_id)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts + dur, rid))
        await db.conn.commit()
    await db.set_category_final(rid, "work")
    await db.save_description(rid, desc)
    return rid


async def _seed(db):
    t = _ms(9)
    await _add(db, t, "Code", "A 写迁移脚本", DEV_A)
    await _add(db, t + 10_000, "Chrome", "B 查文档", DEV_B)
    await _add(db, t + 3_600_000, "Code", "A 下一个小时", DEV_A)  # A only, 10:00
    await _add(db, t + 7_200_000, "Notepad", "旧记录", None)  # unknown device, 11:00
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)
    return t


async def test_rollup_builds_one_pyramid_per_device(db):
    t = await _seed(db)
    leaf_key = scope_key(t, "5min", CUT)
    leaves = await db.get_device_summaries("5min", t, t + 1, scope_key=leaf_key)
    assert {r["device_scope"] for r in leaves} == {DEV_A, DEV_B}
    a = next(r for r in leaves if r["device_scope"] == DEV_A)
    assert json.loads(a["metrics_json"])["app_ms"] == {"Code": 60_000}
    days = await db.get_device_summaries("day", t - 86_400_000, t + 86_400_000)
    by_dev = {r["device_scope"]: json.loads(r["metrics_json"])["record_count"] for r in days}
    assert by_dev == {DEV_A: 2, DEV_B: 1, "_unknown_device": 1}
    # global cascade untouched and still sums every device
    glob = await db.get_summary("day", scope_key(t, "day", CUT))
    assert json.loads(glob["metrics_json"])["record_count"] == 4


async def test_narrator_narrates_per_device_and_composes_global(db):
    t = await _seed(db)
    llm = _EchoLLM()
    narrator = DevicePyramidNarrator(db, NarrativeBuilder(db, llm))
    now = t + 30 * 86_400_000
    await narrator.narrate_range(t - 86_400_000, t + 86_400_000, now)

    # device prompts never contain the other device's frames
    for call in llm.calls:
        assert not ("A 写迁移脚本" in call and "B 查文档" in call)
    # the global 5min leaf with both devices is composed, not narrated by the LLM
    glob_leaf = await db.get_summary("5min", scope_key(t, "5min", CUT))
    assert glob_leaf["status"] == "narrated"
    assert "【办公电脑】叙述-A" in glob_leaf["description"]
    assert "【家用电脑】叙述-B" in glob_leaf["description"]
    body = json.loads(glob_leaf["body_json"])
    assert set(body["composed_from_devices"]) == {DEV_A, DEV_B}
    assert "【办公电脑】重点-A" in body["key_points"]
    # a single-device global window is a verbatim copy
    glob_10 = await db.get_summary("5min", scope_key(t + 3_600_000, "5min", CUT))
    assert glob_10["description"] == "叙述-A"
    # global rows never cost an LLM call: every call was for a device row
    llm_rows = 0
    for g in ("5min", "1h", "6h", "day", "week"):
        for r in await db.get_device_summaries(g, 0, now):
            body = json.loads(r["body_json"] or "{}")
            if r["status"] == "narrated" and not body.get("metrics_only"):
                llm_rows += 1
    assert len(llm.calls) == llm_rows
    day = await db.get_summary("day", scope_key(t, "day", CUT))
    assert "【未知设备】" in day["description"]


async def test_global_falls_back_to_llm_without_device_rows(db):
    t = _ms(9)
    await _add(db, t, "Code", "只有全局", DEV_A)
    await MetricsCascadeBuilder(db, RollupConfig(device_pyramid=False)).build_day(t)
    llm = _EchoLLM()
    await DevicePyramidNarrator(db, NarrativeBuilder(db, llm)).narrate_range(
        t - 86_400_000, t + 86_400_000, t + 30 * 86_400_000
    )
    leaf = await db.get_summary("5min", scope_key(t, "5min", CUT))
    assert leaf["status"] == "narrated" and leaf["description"]


async def test_rebuild_keeps_device_narrative_progress(db):
    t = await _seed(db)
    narrator = DevicePyramidNarrator(db, NarrativeBuilder(db, _EchoLLM()))
    await narrator.narrate_range(t - 86_400_000, t + 86_400_000, t + 30 * 86_400_000)
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)  # same inputs
    rows = await db.get_device_summaries("5min", t - 86_400_000, t + 86_400_000)
    assert all(r["status"] == "narrated" for r in rows)


def test_round_robin_interleaves_devices():
    rows = [{"device_scope": "a", "i": i} for i in range(3)] + [{"device_scope": "b", "i": 9}]
    assert [r["device_scope"] for r in _round_robin(rows)][:2] == ["a", "b"]


async def test_read_paths_expose_device_pyramid(db):
    t = await _seed(db)
    await DevicePyramidNarrator(db, NarrativeBuilder(db, _EchoLLM())).narrate_range(
        t - 86_400_000, t + 86_400_000, t + 30 * 86_400_000
    )
    app = create_app(db)
    with TestClient(app) as client:
        body = client.get("/v1/summaries", params={"day": "2001-06-09"}).json()
        assert {d["label"] for d in body["devices"]} == {"办公电脑", "家用电脑", "未知设备"}
        only_b = client.get(
            "/v1/summaries", params={"day": "2001-06-09", "device_id": DEV_B}
        ).json()
        assert [w["description"] for w in only_b["grains"]["5min"]] == ["叙述-B"]
    kw = dict(period="custom", start_iso="2001-06-09 04:00", end_iso="2001-06-10 04:00")
    res = await agent_tools.search_summaries(db, grain="5min", device_id=DEV_A, **kw)
    assert [i["description"] for i in res["items"]] == ["叙述-A", "叙述-A"]
    hit = await agent_tools.search_summaries(
        db, grain="5min", device_id=DEV_B, query="叙述-B", **kw
    )
    assert hit["count"] == 1
