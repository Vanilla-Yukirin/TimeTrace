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
from timetrace.server.db import Database
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

    async def describe(
        self,
        image: Image.Image,
        window_title: str | None = None,
        app_note: str | None = None,
    ) -> dict:
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
    vlm = _StubVLM(
        payload={"keywords": ["k"], "summary": "s", "description": "d", "category": "work"}
    )
    worker = _make_worker(db, vlm, tmp_path)

    task = await db.claim_next_task("pending_vlm")
    await worker._handle_one(task, worker_id=0)

    async with db.conn.execute(
        "SELECT vlm_desc, status, category_final FROM analysis_results WHERE record_id=?", (rid,)
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "vlm_done"
    assert row["vlm_desc"] is not None
    assert "d" in row["vlm_desc"]
    assert "摘要：s" in row["vlm_desc"]
    assert "关键词：k" in row["vlm_desc"]
    # The VLM's chosen category is persisted as category_final (no rule → VLM wins).
    assert row["category_final"] == "work"


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


# --------------------------------------------------------------------------- #
# Embedding stage (best-effort after vlm_done)                                  #
# --------------------------------------------------------------------------- #


class _StubEmbedding:
    """Mimics EmbeddingClient for the worker; returns a fixed byte string."""

    def __init__(
        self,
        *,
        vec_bytes: bytes = b"\x00" * 16,
        error: Exception | None = None,
    ) -> None:
        self.vec_bytes = vec_bytes
        self.error = error
        self.calls = 0
        self.model = "stub-embed"
        self.dim = 4

    async def embed(self, text: str) -> bytes:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.vec_bytes

    async def aclose(self) -> None:  # pragma: no cover
        return


async def test_worker_calls_embedding_after_vlm_done(db, tmp_path):
    """Happy path: VLM succeeds → embedding called → text_embedding column set."""
    rid = await _seed_pending(db, tmp_path)
    vlm = _StubVLM(payload={"keywords": ["k"], "summary": "s", "description": "d"})
    embedding = _StubEmbedding(vec_bytes=b"\x01\x02\x03\x04" * 4)
    worker = _make_worker(db, vlm, tmp_path)
    worker._embedding = embedding  # inject post-construct (avoid threading through helper)

    task = await db.claim_next_task("pending_vlm")
    await worker._handle_one(task, worker_id=0)

    assert embedding.calls == 1
    async with db.conn.execute(
        "SELECT text_embedding, text_embedding_model, status "
        "FROM analysis_results WHERE record_id=?",
        (rid,),
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "vlm_done"
    assert row["text_embedding"] == b"\x01\x02\x03\x04" * 4
    assert row["text_embedding_model"] == "stub-embed"


async def test_worker_embedding_failure_does_not_block_vlm_done(db, tmp_path):
    """Regression: embedding stage must NEVER prevent vlm_done. If embedding
    endpoint is down, record still completes — backfill sweeps later."""
    from timetrace.server.embedding.client import EmbeddingError

    rid = await _seed_pending(db, tmp_path)
    vlm = _StubVLM(payload={"keywords": [], "summary": "s", "description": "d"})
    embedding = _StubEmbedding(error=EmbeddingError("endpoint down"))
    worker = _make_worker(db, vlm, tmp_path)
    worker._embedding = embedding

    task = await db.claim_next_task("pending_vlm")
    await worker._handle_one(task, worker_id=0)

    assert embedding.calls == 1
    async with db.conn.execute(
        "SELECT text_embedding, status FROM analysis_results WHERE record_id=?",
        (rid,),
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "vlm_done"  # vlm_done despite embedding failure
    assert row["text_embedding"] is None  # left for backfill


async def test_worker_without_embedding_client_short_circuits(db, tmp_path):
    """When EmbeddingClient is None (env not configured), worker doesn't try."""
    rid = await _seed_pending(db, tmp_path)
    vlm = _StubVLM(payload={"keywords": [], "summary": "s", "description": "d"})
    worker = _make_worker(db, vlm, tmp_path)
    # worker._embedding is None by default (helper doesn't set it)
    assert worker._embedding is None

    task = await db.claim_next_task("pending_vlm")
    await worker._handle_one(task, worker_id=0)

    async with db.conn.execute(
        "SELECT text_embedding, status FROM analysis_results WHERE record_id=?",
        (rid,),
    ) as cur:
        row = await cur.fetchone()
    assert row["status"] == "vlm_done"
    assert row["text_embedding"] is None


# --------------------------------------------------------------------------- #
# Embedding backfill sweep                                                      #
# --------------------------------------------------------------------------- #


async def _seed_vlm_done_without_embedding(db: Database, desc: str) -> str:
    """Create a record already at vlm_done with a description but no embedding."""
    ctx = CaptureContext(app_name="App", process_name="app", window_title="t")
    rid = await db.insert_record(ctx, reason="heartbeat")
    await db.mark_pending(rid)
    await db.save_description(rid, desc)
    await db.transition(rid, "vlm_done")
    return rid


async def test_backfill_embeds_all_null_rows(db, tmp_path):
    ids = [await _seed_vlm_done_without_embedding(db, f"desc {i}") for i in range(3)]
    vlm = _StubVLM(payload={"keywords": [], "summary": "", "description": ""})
    embedding = _StubEmbedding(vec_bytes=b"\x01\x02\x03\x04")
    worker = _make_worker(db, vlm, tmp_path)
    worker._embedding = embedding

    await worker._backfill_embeddings()

    assert embedding.calls == 3
    for rid in ids:
        async with db.conn.execute(
            "SELECT text_embedding FROM analysis_results WHERE record_id=?", (rid,)
        ) as cur:
            row = await cur.fetchone()
        assert row["text_embedding"] == b"\x01\x02\x03\x04"


async def test_backfill_noop_when_all_embedded(db, tmp_path):
    rid = await _seed_vlm_done_without_embedding(db, "desc")
    await db.save_text_embedding(rid, b"\xaa\xbb", "m")
    vlm = _StubVLM(payload={"keywords": [], "summary": "", "description": ""})
    embedding = _StubEmbedding()
    worker = _make_worker(db, vlm, tmp_path)
    worker._embedding = embedding

    await worker._backfill_embeddings()
    assert embedding.calls == 0  # nothing left to do


async def test_backfill_aborts_after_consecutive_failures(db, tmp_path):
    """Endpoint-down simulation: every embed fails → abort after threshold,
    don't spin forever on the same NULL rows."""
    from timetrace.server.embedding.client import EmbeddingError

    for i in range(10):
        await _seed_vlm_done_without_embedding(db, f"desc {i}")
    vlm = _StubVLM(payload={"keywords": [], "summary": "", "description": ""})
    embedding = _StubEmbedding(error=EmbeddingError("endpoint down"))
    worker = _make_worker(db, vlm, tmp_path)
    worker._embedding = embedding

    await worker._backfill_embeddings()

    # Stops at the consecutive-failure threshold (5), doesn't try all 10.
    assert embedding.calls == 5
    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM analysis_results WHERE text_embedding IS NOT NULL"
    ) as cur:
        assert (await cur.fetchone())["n"] == 0


async def test_backfill_skips_poison_row_continues_rest(db, tmp_path):
    """A single failing row doesn't starve healthy rows behind it."""
    from timetrace.server.embedding.client import EmbeddingError

    poison = await _seed_vlm_done_without_embedding(db, "POISON")
    good = [await _seed_vlm_done_without_embedding(db, f"good {i}") for i in range(3)]

    class _SelectiveEmbedding(_StubEmbedding):
        async def embed(self, text: str) -> bytes:
            self.calls += 1
            if text == "POISON":
                raise EmbeddingError("bad row")
            return b"\x09\x09"

    embedding = _SelectiveEmbedding()
    vlm = _StubVLM(payload={"keywords": [], "summary": "", "description": ""})
    worker = _make_worker(db, vlm, tmp_path)
    worker._embedding = embedding

    await worker._backfill_embeddings()

    # poison stays NULL, all good rows embedded
    async with db.conn.execute(
        "SELECT text_embedding FROM analysis_results WHERE record_id=?", (poison,)
    ) as cur:
        assert (await cur.fetchone())["text_embedding"] is None
    for rid in good:
        async with db.conn.execute(
            "SELECT text_embedding FROM analysis_results WHERE record_id=?", (rid,)
        ) as cur:
            assert (await cur.fetchone())["text_embedding"] == b"\x09\x09"
