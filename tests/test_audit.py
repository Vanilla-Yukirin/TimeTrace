"""E2E tests for the audit-log feed (GET /v1/audit/records).

Drives the real SqliteDatabase state machine (insert_record / mark_pending /
claim_next_task / save_description / set_category_final / transition / error
helpers) through the in-process FastAPI app, then asserts the route's derived
status / latency / flag fields. No worker, no network — just the DB + route.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.api.app import create_app
from timetrace.server.db import Database


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


def _ctx(title: str = "t", app: str = "App") -> CaptureContext:
    return CaptureContext(app_name=app, process_name="app", window_title=title)


def _audit(client: TestClient, **params) -> dict:
    resp = client.get("/v1/audit/records", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {it["id"]: it for it in items}


async def test_audit_empty(db):
    app = create_app(db)
    with TestClient(app) as client:
        data = _audit(client)
    assert data["items"] == []
    assert data["next_cursor"] is None
    assert isinstance(data["server_now"], int)


async def test_audit_newest_first(db):
    now = int(time.time() * 1000)
    ids = [
        await db.insert_record(
            _ctx(title=f"w{i}"), reason="heartbeat", ts_start=now - 10_000 + i * 1000
        )
        for i in range(3)
    ]
    app = create_app(db)
    with TestClient(app) as client:
        items = _audit(client)["items"]
    # DESC by ts_start: the last-inserted (largest ts_start) comes first.
    assert [it["id"] for it in items] == list(reversed(ids))


async def test_audit_status_taxonomy(db):
    # processing FIRST so claim_next_task picks exactly this row (only pending).
    r_proc = await db.insert_record(_ctx(title="proc"), reason="heartbeat")
    await db.mark_pending(r_proc)
    claimed = await db.claim_next_task("pending_vlm")
    assert claimed is not None and claimed["record_id"] == r_proc

    r_cap = await db.insert_record(_ctx(title="cap"), reason="heartbeat")

    r_queued = await db.insert_record(_ctx(title="queued"), reason="heartbeat")
    await db.mark_pending(r_queued)

    r_retry = await db.insert_record(_ctx(title="retry"), reason="heartbeat")
    await db.mark_pending(r_retry)
    future = int(time.time() * 1000) + 60_000
    await db.conn.execute(
        "UPDATE analysis_results SET retry_count=1, next_retry_at=? WHERE record_id=?",
        (future, r_retry),
    )
    await db.conn.commit()

    r_done = await db.insert_record(_ctx(title="done"), reason="heartbeat")
    await db.insert_screenshot(
        record_id=r_done,
        path="screenshots/x.png",
        thumb_path=None,
        width=1,
        height=1,
        hash_sha256="h1",
    )
    await db.mark_pending(r_done)
    await db.save_description(r_done, "a description")
    await db.set_category_final(r_done, "work")
    await db.transition(r_done, "vlm_done")

    # described but never classified → status 'done', completed False, needs_classification True
    r_unclassified = await db.insert_record(_ctx(title="unclassified"), reason="heartbeat")
    await db.mark_pending(r_unclassified)
    await db.save_description(r_unclassified, "desc only")
    await db.transition(r_unclassified, "vlm_done")

    # no screenshot, no desc → TERMINAL image-less skip (nothing to requeue)
    r_skip = await db.insert_record(_ctx(title="skip"), reason="heartbeat")
    await db.mark_pending(r_skip)
    await db.transition(r_skip, "vlm_done")

    # vlm_done + no desc BUT a screenshot exists → a late shot arrived; this is
    # re-queueable (requeue_skipped_for_vlm), so it still "needs VLM".
    r_skip_shot = await db.insert_record(_ctx(title="skip-shot"), reason="heartbeat")
    await db.insert_screenshot(
        record_id=r_skip_shot,
        path="screenshots/y.png",
        thumb_path=None,
        width=1,
        height=1,
        hash_sha256="h2",
    )
    await db.mark_pending(r_skip_shot)
    await db.transition(r_skip_shot, "vlm_done")

    r_fail = await db.insert_record(_ctx(title="fail"), reason="heartbeat")
    await db.mark_pending(r_fail)
    await db.mark_error_final(r_fail, "boom")

    # manual label on a fresh record (set_category_final seeds vlm_done, no desc/model)
    r_label = await db.insert_record(_ctx(title="label"), reason="heartbeat")
    await db.set_category_final(r_label, "study")

    app = create_app(db)
    with TestClient(app) as client:
        rows = _by_id(_audit(client, limit=100)["items"])

    assert rows[r_cap]["status"] == "captured"
    assert rows[r_cap]["needs_vlm"] is True
    assert rows[r_cap]["completed"] is False

    assert rows[r_queued]["status"] == "queued"
    assert rows[r_retry]["status"] == "retry_waiting"
    assert rows[r_proc]["status"] == "processing"
    assert rows[r_proc]["analysis_status"] == "processing_vlm"

    assert rows[r_done]["status"] == "done"
    assert rows[r_done]["completed"] is True
    assert rows[r_done]["needs_vlm"] is False
    assert rows[r_done]["needs_classification"] is False
    assert rows[r_done]["category_final"] == "work"
    assert rows[r_done]["desc_chars"] == len("a description")
    assert rows[r_done]["screenshot_count"] == 1

    assert rows[r_unclassified]["status"] == "done"
    assert rows[r_unclassified]["completed"] is False
    assert rows[r_unclassified]["needs_classification"] is True

    assert rows[r_skip]["status"] == "skipped_no_image"
    assert rows[r_skip]["needs_vlm"] is False  # terminal: no screenshot → never requeued
    assert rows[r_skip]["screenshot_count"] == 0

    assert rows[r_skip_shot]["status"] == "skipped_no_image"
    assert rows[r_skip_shot]["needs_vlm"] is True  # has a screenshot → re-queueable
    assert rows[r_skip_shot]["screenshot_count"] == 1

    assert rows[r_fail]["status"] == "failed"
    assert rows[r_fail]["error_msg"] == "boom"

    assert rows[r_label]["status"] == "labeled"
    assert rows[r_label]["category_final"] == "study"

    # Phase-B contract: the meaningless 达标 flag is dropped; new fields present.
    sample = rows[r_done]
    assert "classification_met" not in sample
    assert "decision_trace" in sample
    assert "end_to_end_ms" in sample


async def test_audit_single_vs_dual_process(db):
    now = int(time.time() * 1000)
    r_dual = await db.insert_record(
        _ctx(title="dual"), reason="heartbeat", client_record_id="cli-1", ts_start=now - 5000
    )
    r_single = await db.insert_record(_ctx(title="single"), reason="heartbeat")

    app = create_app(db)
    with TestClient(app) as client:
        rows = _by_id(_audit(client)["items"])

    dual = rows[r_dual]
    assert dual["single_process"] is False
    assert dual["client_record_id"] == "cli-1"
    # created_at (server clock ~now) − ts_start (now−5s) ≈ 5s of upload delay.
    assert dual["ingest_delay_ms"] is not None
    assert 3000 < dual["ingest_delay_ms"] < 7000

    single = rows[r_single]
    assert single["single_process"] is True
    assert single["ingest_delay_ms"] is None  # N/A, not a fake 0


async def test_audit_keyset_pagination(db):
    now = int(time.time() * 1000)
    ids = [
        await db.insert_record(
            _ctx(title=f"p{i}"), reason="heartbeat", ts_start=now - 50_000 + i * 1000
        )
        for i in range(5)
    ]
    newest_first = list(reversed(ids))

    app = create_app(db)
    with TestClient(app) as client:
        seen: list[str] = []
        cursor = None
        for _ in range(4):  # safety bound; 5 rows / page-2 → 3 pages
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            page = _audit(client, **params)
            seen.extend(it["id"] for it in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break

    assert seen == newest_first  # full coverage, DESC, no overlap/dupes
    assert cursor is None  # terminal page had < limit rows → no next cursor


async def test_audit_phase_b_fields(db):
    """A record driven through the new write paths surfaces real latencies +
    classification provenance in the audit feed."""
    rid = await db.insert_record(_ctx(title="full"), reason="heartbeat")
    await db.insert_screenshot(
        record_id=rid,
        path="screenshots/z.png",
        thumb_path=None,
        width=1,
        height=1,
        hash_sha256="hz",
    )
    await db.mark_pending(rid)  # queued_at
    claimed = await db.claim_next_task("pending_vlm")  # locked_at
    assert claimed is not None and claimed["record_id"] == rid
    trace = {
        "final_category": "work",
        "confidence": 0.571,
        "candidates": [{"cat": "work", "score": 2.0}, {"cat": "social", "score": 1.5}],
        "signals": {"rule": {"hit": True, "cat": "work"}, "vlm": {"cat": "social", "conf": 1.0}},
    }
    await db.save_description(rid, "a desc", vlm_model="m-1", vlm_latency_ms=1234)
    await db.set_category_final(
        rid, "work", category_suggested="social", confidence=0.571, decision_trace=json.dumps(trace)
    )
    await db.transition(rid, "vlm_done")  # done_at

    app = create_app(db)
    with TestClient(app) as client:
        row = _by_id(_audit(client)["items"])[rid]

    assert row["vlm_duration_ms"] == 1234
    assert row["vlm_model"] == "m-1"
    assert row["queue_wait_ms"] is not None and row["queue_wait_ms"] >= 0
    assert row["total_latency_ms"] is not None  # done_at − created_at
    assert row["end_to_end_ms"] is not None  # done_at − ts_start
    assert row["category_suggested"] == "social"
    assert row["category_final"] == "work"
    assert row["confidence"] == 0.571
    assert row["decision_trace"] is not None
    assert json.loads(row["decision_trace"])["signals"]["rule"]["cat"] == "work"
    assert "classification_met" not in row  # the meaningless 达标 flag is gone


async def test_audit_old_record_has_null_latencies(db):
    """A record that never hit the new write paths shows null latencies/trace
    (no backfill — honest '—' in the UI), and the keys still exist."""
    rid = await db.insert_record(_ctx(title="old"), reason="heartbeat")
    app = create_app(db)
    with TestClient(app) as client:
        row = _by_id(_audit(client)["items"])[rid]
    assert row["queue_wait_ms"] is None
    assert row["vlm_duration_ms"] is None
    assert row["total_latency_ms"] is None
    assert row["end_to_end_ms"] is None
    assert row["decision_trace"] is None
