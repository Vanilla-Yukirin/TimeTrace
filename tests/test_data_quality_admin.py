"""Explicit data-quality maintenance is dry-run first and backup guarded."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server import admin_cmd
from timetrace.server.db import Database
from timetrace.server.db.sqlite import _MAX_ORPHAN_BRIDGE_MS


def _seed_bad_span(cfg: StorageConfig) -> str:
    async def _run() -> str:
        db = Database(cfg)
        await db.init()
        try:
            start = 1_747_300_000_000
            rid = await db.insert_record(
                CaptureContext(app_name="A", process_name="a", window_title="a"),
                reason="heartbeat",
                ts_start=start,
            )
            await db.conn.execute(
                "UPDATE records SET ts_end=? WHERE id=?",
                (start + _MAX_ORPHAN_BRIDGE_MS + 1, rid),
            )
            await db.conn.commit()
            return rid
        finally:
            await db.close()

    return asyncio.run(_run())


def _read_end(cfg: StorageConfig, record_id: str) -> tuple[int, int]:
    async def _run() -> tuple[int, int]:
        db = Database(cfg)
        await db.init()
        try:
            row = await db.get_record_by_id(record_id)
            return row["ts_start"], row["ts_end"]
        finally:
            await db.close()

    return asyncio.run(_run())


def _seed_empty_vlm_results(cfg: StorageConfig) -> tuple[str, str, str]:
    async def _run() -> tuple[str, str, str]:
        db = Database(cfg)
        await db.init()
        try:
            with_image = await db.insert_record(
                CaptureContext(app_name="A", process_name="a", window_title="with-image"),
                reason="heartbeat",
            )
            without_image = await db.insert_record(
                CaptureContext(app_name="A", process_name="a", window_title="without-image"),
                reason="heartbeat",
            )
            manual_label = await db.insert_record(
                CaptureContext(app_name="A", process_name="a", window_title="manual-label"),
                reason="heartbeat",
            )
            await db.insert_screenshot(
                record_id=with_image,
                path="screenshots/a.png",
                thumb_path=None,
                width=1,
                height=1,
                hash_sha256="hash",
            )
            await db.insert_screenshot(
                record_id=manual_label,
                path="screenshots/manual.png",
                thumb_path=None,
                width=1,
                height=1,
                hash_sha256="manual-hash",
            )
            for record_id in (with_image, without_image):
                await db.mark_pending(record_id)
                await db.transition(record_id, "vlm_done")
            # Agent/user labels intentionally create a terminal row without a
            # VLM model or description. Repair must not let VLM overwrite it.
            await db.set_category_final(manual_label, "work")
            return with_image, without_image, manual_label
        finally:
            await db.close()

    return asyncio.run(_run())


def test_repair_data_quality_dry_run_does_not_mutate(tmp_path, monkeypatch):
    cfg = StorageConfig(data_dir=tmp_path)
    rid = _seed_bad_span(cfg)
    monkeypatch.setattr(admin_cmd, "AppConfig", lambda: SimpleNamespace(storage=cfg))
    lines: list[str] = []

    assert admin_cmd.run(["repair-data-quality"], out=lines.append) == 0
    start, end = _read_end(cfg, rid)
    assert end - start > _MAX_ORPHAN_BRIDGE_MS
    assert "dry-run only" in "\n".join(lines)
    assert not list(cfg.db_path.parent.glob("*.pre-quality-repair-*.db"))


def test_repair_data_quality_apply_backs_up_and_repairs(tmp_path, monkeypatch):
    cfg = StorageConfig(data_dir=tmp_path)
    rid = _seed_bad_span(cfg)
    monkeypatch.setattr(admin_cmd, "AppConfig", lambda: SimpleNamespace(storage=cfg))
    lines: list[str] = []

    assert admin_cmd.run(["repair-data-quality", "--apply"], out=lines.append) == 0
    start, end = _read_end(cfg, rid)
    assert end == start
    backups = list(cfg.db_path.parent.glob("*.pre-quality-repair-*.db"))
    assert len(backups) == 1 and backups[0].stat().st_size > 0
    assert "applied =" in "\n".join(lines)


def test_repair_requeues_only_empty_results_that_have_images(tmp_path, monkeypatch):
    cfg = StorageConfig(data_dir=tmp_path)
    with_image, without_image, manual_label = _seed_empty_vlm_results(cfg)
    monkeypatch.setattr(admin_cmd, "AppConfig", lambda: SimpleNamespace(storage=cfg))

    async def _inspect() -> dict[str, int]:
        db = Database(cfg)
        await db.init()
        try:
            return await db.inspect_data_quality()
        finally:
            await db.close()

    before = asyncio.run(_inspect())
    assert before["completed_with_image_but_empty_description"] == 1
    assert before["completed_without_image"] == 1

    assert admin_cmd.run(["repair-data-quality", "--apply"], out=lambda _line: None) == 0

    async def _read() -> tuple[str, str, str, str]:
        db = Database(cfg)
        await db.init()
        try:
            rows = {}
            for record_id in (with_image, without_image, manual_label):
                async with db.conn.execute(
                    "SELECT status, category_final FROM analysis_results WHERE record_id=?",
                    (record_id,),
                ) as cur:
                    row = await cur.fetchone()
                    rows[record_id] = (row["status"], row["category_final"])
            return (
                rows[with_image][0],
                rows[without_image][0],
                rows[manual_label][0],
                rows[manual_label][1],
            )
        finally:
            await db.close()

    assert asyncio.run(_read()) == ("pending_vlm", "vlm_done", "vlm_done", "work")
