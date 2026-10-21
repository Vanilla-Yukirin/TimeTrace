"""Multi-device narrative context (W2): device-grouped leaf frames, fair
truncation, per-device metrics, child key points flowing up, and legacy hash
stability."""

from __future__ import annotations

import datetime as _dt
import json

import pytest

from timetrace.common.config import RollupConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.db import Database
from timetrace.server.summary.narrative import _SYSTEM_PROMPT, NarrativeBuilder
from timetrace.server.summary.rollup import MetricsCascadeBuilder
from timetrace.server.summary.windows import scope_key

CUT = 4
DEV_A = "11111111-1111-4111-8111-111111111111"
DEV_B = "22222222-2222-4222-8222-222222222222"

_PAYLOAD = json.dumps(
    {"description": "并行使用两台设备", "key_points": ["A 写代码", "B 查资料"], "evaluation": "ok"},
    ensure_ascii=False,
)


class _MockLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        self.calls.append(user)
        return _PAYLOAD


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    for dev, name in ((DEV_A, "办公电脑"), (DEV_B, "家用电脑")):
        await database.validate_or_register_device(dev, token_fingerprint=None, token_label=None)
        await database.update_device(dev, name=name, description="")
    yield database
    await database.close()


def _ms(h, mi=0, s=0):
    return int(_dt.datetime(2001, 6, 9, h, mi, s).timestamp() * 1000)


async def _add(db, ts, dur, app, desc, device_id):
    ctx = CaptureContext(app_name=app, process_name=app.lower(), window_title=app)
    rid = await db.insert_record(ctx, reason="test", ts_start=ts, device_id=device_id)
    async with db.lock:
        await db.conn.execute("UPDATE records SET ts_end=? WHERE id=?", (ts + dur, rid))
        await db.conn.commit()
    await db.set_category_final(rid, "work")
    await db.save_description(rid, desc)
    return rid


async def test_leaf_groups_frames_per_device_with_labels(db):
    t = _ms(9)
    # interleaved, overlapping use of two machines
    await _add(db, t, 60_000, "Code", "A 在写 outbox 代码", DEV_A)
    await _add(db, t + 10_000, 60_000, "Chrome", "B 在查 SQLite 文档", DEV_B)
    await _add(db, t + 70_000, 60_000, "Code", "A 继续写测试", DEV_A)
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)
    leaf = await db.get_summary("5min", scope_key(t, "5min", CUT))

    metrics = json.loads(leaf["metrics_json"])
    assert metrics["device_ms"] == {DEV_A: 120_000, DEV_B: 60_000}

    mock = _MockLLM()
    await NarrativeBuilder(db, mock).build_one(leaf)
    prompt = mock.calls[0]
    a, b = prompt.index("【设备：办公电脑】"), prompt.index("【设备：家用电脑】")
    section_a = prompt[a:b] if a < b else prompt[a:]
    assert "outbox" in section_a and "继续写测试" in section_a and "SQLite" not in section_a
    assert "设备时长" in prompt and "墙钟活跃" in prompt
    assert "这台电脑" not in _SYSTEM_PROMPT


async def test_single_device_window_has_no_device_headers(db):
    t = _ms(10)
    await _add(db, t, 60_000, "Code", "只用一台", DEV_A)
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)
    leaf = await db.get_summary("5min", scope_key(t, "5min", CUT))
    mock = _MockLLM()
    await NarrativeBuilder(db, mock).build_one(leaf)
    assert "【设备" not in mock.calls[0]
    assert "墙钟活跃" not in mock.calls[0]


async def test_truncation_is_fair_across_devices(db):
    t = _ms(11)
    # device A floods the window; B has a single late frame
    for i in range(60):
        await _add(db, t + i * 4_000, 3_000, "Code", "A" * 120 + f"#{i}", DEV_A)
    await _add(db, t + 280_000, 3_000, "Chrome", "B 的唯一一帧", DEV_B)
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)
    leaf = await db.get_summary("5min", scope_key(t, "5min", CUT))
    mock = _MockLLM()
    await NarrativeBuilder(db, mock).build_one(leaf)
    assert "B 的唯一一帧" in mock.calls[0]
    assert "截断" in mock.calls[0]


async def test_rollup_context_carries_child_key_points(db):
    t = _ms(12)
    await _add(db, t, 60_000, "Code", "x", DEV_A)
    await MetricsCascadeBuilder(db, RollupConfig()).build_day(t)
    async with db.lock:
        await db.conn.execute(
            "UPDATE summaries SET description=?, body_json=? WHERE grain='5min'",
            ("子窗叙述", json.dumps({"key_points": ["关键产出：合并 PR"]}, ensure_ascii=False)),
        )
        await db.conn.commit()
    one_h = await db.get_summary("1h", scope_key(t, "1h", CUT))
    mock = _MockLLM()
    await NarrativeBuilder(db, mock).build_one(one_h)
    assert "关键产出：合并 PR" in mock.calls[0]


async def test_legacy_rows_keep_their_source_hash(db):
    """NULL-device rows hash exactly as before W2, so history is not re-queued."""
    import hashlib

    t = _ms(13)
    rid = await _add(db, t, 60_000, "Code", "legacy", None)
    builder = MetricsCascadeBuilder(db, RollupConfig())
    start = scope_key(t, "5min", CUT)
    await builder.build_day(t)
    leaf = await db.get_summary("5min", start)
    legacy_payload = f"{rid}|{t}|{t + 60_000}|Code|work"
    assert leaf["source_hash"] == hashlib.sha256(legacy_payload.encode()).hexdigest()[:16]
