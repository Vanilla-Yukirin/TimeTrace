"""Tests for AnalysisWorker pipeline: success / retry / final / atomic claim."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from timetrace.common.config import StorageConfig, WorkerConfig
from timetrace.common.models import CaptureContext
from timetrace.server.storage.database import Database
from timetrace.server.vlm.client import VLMError
from timetrace.server.vlm.health import VLMHealthGate
from timetrace.server.worker.loop import AnalysisWorker


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


class _StubVLM:
    """Mimics VLMClient.describe for the worker without hitting any network."""

    def __init__(self, *, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    async def describe(self, image: Image.Image, window_title: str | None = None) -> dict:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return self.payload

    async def heartbeat(self) -> bool:
        return True

    async def aclose(self) -> None:  # pragma: no cover - unused
        return


async def _seed_pending(db: Database, tmp_path: Path) -> str:
    """Create a record + screenshot file + analysis_results row in pending_vlm."""
    ctx = CaptureContext(app_name="App", process_name="app", window_title="title")
    rid = await db.insert_record(ctx, reason="heartbeat")

    # Write a real PNG into storage_cfg.data_dir-relative path
    rel = "screenshots/2026/04/22/test.png"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (200, 100, 50)).save(abs_path)

    await db.insert_screenshot(
        record_id=rid,
        path=rel,
        thumb_path=None,
        width=16,
        height=16,
        hash_sha256="sha",
    )
    await db.mark_pending(rid)
    return rid


def _make_worker(db: Database, vlm: Any, tmp_path: Path, *, max_retries: int = 5) -> AnalysisWorker:
    storage_cfg = StorageConfig(data_dir=tmp_path)
    cfg = WorkerConfig(
        vlm_concurrency=1,
        max_retries=max_retries,
        backoff_base_s=1.0,
        backoff_max_s=10.0,
    )
    gate = VLMHealthGate(vlm, fail_threshold=999, recover_threshold=1, probe_interval_s=1.0)
    return AnalysisWorker(db, vlm=vlm, gate=gate, cfg=cfg, storage_cfg=storage_cfg)


# --------------------------------------------------------------------------- #
# Success path                                                                  #
# --------------------------------------------------------------------------- #


async def test_worker_writes_description_on_success(db, tmp_path):
    rid = await _seed_pending(db, tmp_path)
    vlm = _StubVLM(payload={"keywords": ["k"], "summary": "s", "description": "d"})
    worker = _make_worker(db, vlm, tmp_path)

    task = await db.claim_next_task("pending_vlm")
    await worker._handle_one(task, worker_id=0)

    async with db.conn.execute(
        "SELECT vlm_desc, status FROM analysis_results WHERE record_id=?", (rid,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "vlm_done"
    assert row["vlm_desc"] is not None
    assert "d" in row["vlm_desc"]
    assert "摘要：s" in row["vlm_desc"]
    assert "关键词：k" in row["vlm_desc"]


# --------------------------------------------------------------------------- #
# Retry path                                                                    #
# --------------------------------------------------------------------------- #


async def test_worker_retries_on_failure(db, tmp_path):
    rid = await _seed_pending(db, tmp_path)
    vlm = _StubVLM(error=VLMError("transient network blip"))
    worker = _make_worker(db, vlm, tmp_path)

    task = await db.claim_next_task("pending_vlm")
    before_ms = int(time.time() * 1000)
    await worker._handle_one(task, worker_id=0)

    async with db.conn.execute(
        "SELECT status, retry_count, next_retry_at, error_msg "
        "FROM analysis_results WHERE record_id=?",
        (rid,),
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "pending_vlm"
    assert row["retry_count"] == 1
    assert row["next_retry_at"] is not None
    assert row["next_retry_at"] >= before_ms
    assert "transient network blip" in (row["error_msg"] or "")


async def test_worker_marks_error_final_after_max_retries(db, tmp_path):
    rid = await _seed_pending(db, tmp_path)
    # Pre-load retry_count = max
    await db.conn.execute("UPDATE analysis_results SET retry_count=? WHERE record_id=?", (5, rid))
    await db.conn.commit()

    vlm = _StubVLM(error=VLMError("still broken"))
    worker = _make_worker(db, vlm, tmp_path, max_retries=5)

    task = await db.claim_next_task("pending_vlm")
    await worker._handle_one(task, worker_id=0)

    async with db.conn.execute(
        "SELECT status FROM analysis_results WHERE record_id=?", (rid,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "error_final"


async def test_worker_skips_when_no_screenshot(db, tmp_path):
    """A record without an associated screenshot transitions to vlm_done without a VLM call."""
    ctx = CaptureContext(app_name="App", process_name="app", window_title="t")
    rid = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(rid)

    vlm = _StubVLM(payload={"keywords": [], "summary": "", "description": ""})
    worker = _make_worker(db, vlm, tmp_path)

    task = await db.claim_next_task("pending_vlm")
    await worker._handle_one(task, worker_id=0)

    async with db.conn.execute(
        "SELECT status FROM analysis_results WHERE record_id=?", (rid,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "vlm_done"
    assert vlm.calls == 0  # VLM was never invoked


# --------------------------------------------------------------------------- #
# Atomic claim under concurrency                                                #
# --------------------------------------------------------------------------- #


async def test_claim_next_task_atomic_under_concurrent_access(db, tmp_path):
    """Five concurrent claim_next_task on a single pending row → exactly one wins."""
    rid = await _seed_pending(db, tmp_path)

    results = await asyncio.gather(*(db.claim_next_task("pending_vlm") for _ in range(5)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0]["record_id"] == rid


async def test_claim_next_task_returns_retry_count(db, tmp_path):
    rid = await _seed_pending(db, tmp_path)
    await db.conn.execute("UPDATE analysis_results SET retry_count=? WHERE record_id=?", (3, rid))
    await db.conn.commit()

    task = await db.claim_next_task("pending_vlm")
    assert task is not None
    assert task["record_id"] == rid
    assert task["retry_count"] == 3
