"""Tests for the ``timetrace-server backfill`` admin subcommand.

Sync tests on purpose: ``_cmd_backfill`` calls ``asyncio.run()`` internally
(it's a one-shot CLI command), which can't be nested inside a running loop, so
seeding + verification also go through ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import datetime as _dt

from timetrace.common.config import AppConfig, StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server import admin_cmd
from timetrace.server.db import Database
from timetrace.server.summary.windows import scope_key

# Sentinel past day (finalized) — avoids touching the real clock / data.
_TS = int(_dt.datetime(2001, 6, 9, 9, 0).timestamp() * 1000)


def test_backfill_subcommand_builds_cascade(tmp_path, monkeypatch):
    async def _seed():
        db = Database(StorageConfig(data_dir=tmp_path))
        await db.init()
        rid = await db.insert_record(
            CaptureContext(app_name="Code", process_name="code", window_title="x"),
            reason="t",
            ts_start=_TS,
        )
        await db.set_category_final(rid, "work")
        await db.close()

    asyncio.run(_seed())

    # point the command's AppConfig at the tmp data dir
    monkeypatch.setattr(
        admin_cmd, "AppConfig", lambda: AppConfig(storage=StorageConfig(data_dir=tmp_path))
    )
    lines: list[str] = []
    rc = admin_cmd.run(["backfill", "2001-06-09", "2001-06-10", "--pause", "0"], out=lines.append)
    assert rc == 0
    assert any("backfilled" in ln for ln in lines)

    async def _check():
        db = Database(StorageConfig(data_dir=tmp_path))
        await db.init()
        row = await db.get_summary("day", scope_key(_TS, "day", 4))
        await db.close()
        return row

    row = asyncio.run(_check())
    assert row is not None
    assert row["record_count"] >= 1


def test_backfill_subcommand_bad_date():
    lines: list[str] = []
    rc = admin_cmd.run(["backfill", "not-a-date", "2001-06-10"], out=lines.append)
    assert rc == 2
    assert any("bad date" in ln for ln in lines)


def test_backfill_subcommand_end_before_start():
    lines: list[str] = []
    rc = admin_cmd.run(["backfill", "2001-06-10", "2001-06-09"], out=lines.append)
    assert rc == 2
    assert any("after start" in ln for ln in lines)
