"""Audit-log feed endpoint.

A LiteLLM-logs-style, one-row-per-record view: newest-first, with each record's
live pipeline status, activity-vs-received time, derived latencies and pipeline
flags. The frontend ``/audit`` page polls this. Read-only; gated by
``require_principal`` (mounted in the business_deps block of ``create_app``).

The DB method (:meth:`SqliteDatabase.query_audit_records`) returns raw joined
columns; ALL derived semantics (status chip, latencies, flags) live HERE so the
truth-rules are in one place and the SQL stays simple. See the audit-log plan
(devlogs) for the column-by-column EXISTS/NEEDS-ADD breakdown — several latency
fields are intentionally ``null`` in this phase because the worker does not yet
stamp per-stage timestamps (Phase B).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

router = APIRouter(tags=["audit"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _derive_status(
    analysis_status: str | None,
    *,
    has_desc: bool,
    category_final: str | None,
    vlm_model: str | None,
    retry_count: int,
    next_retry_at: int | None,
    now: int,
) -> str:
    """Map raw analysis state → the UI status chip.

    ``analysis_status`` (analysis_results.status) is the source of truth; a NULL
    means the record was inserted but no analysis row exists yet (``captured``).
    """
    if analysis_status is None or analysis_status == "captured":
        return "captured"
    if analysis_status.startswith("processing_"):  # processing_vlm (+ legacy form)
        return "processing"
    if analysis_status == "error_final":
        return "failed"
    if analysis_status == "vlm_done":
        if has_desc:
            return "done"
        # vlm_done + NULL desc is ambiguous: a no-image short-circuit, OR a
        # manual/agent label seeded via set_category_final (which also leaves
        # vlm_model NULL). Treat "has a category but never went through the VLM"
        # as a manual label; otherwise it's the no-image skip.
        if category_final is not None and vlm_model is None:
            return "labeled"
        return "skipped_no_image"
    if analysis_status == "pending_vlm":
        if retry_count and next_retry_at and next_retry_at > now:
            return "retry_waiting"
        return "queued"
    return analysis_status  # unknown state → surface it verbatim, don't hide it


def _shape_row(row: dict, now: int) -> dict:
    analysis_status = row["analysis_status"]
    has_desc = bool(row["has_desc"])
    category_final = row["category_final"]
    vlm_model = row["vlm_model"]
    retry_count = row["retry_count"] or 0
    next_retry_at = row["next_retry_at"]

    ts_start = row["ts_start"]
    ts_end = row["ts_end"]
    created_at = row["created_at"]
    first_shot_at = row["first_shot_at"]

    # In single-process mode capture and ingest are the same instant
    # (insert_record sets ts_start == created_at), and client_record_id is NULL.
    # Return a null ingest delay there so the UI shows "N/A" not a fake ~0.
    single_process = row["client_record_id"] is None

    status = _derive_status(
        analysis_status,
        has_desc=has_desc,
        category_final=category_final,
        vlm_model=vlm_model,
        retry_count=retry_count,
        next_retry_at=next_retry_at,
        now=now,
    )

    needs_vlm = (
        analysis_status in (None, "captured", "pending_vlm")
        or (analysis_status or "").startswith("processing_")
        # no-image skip that still has a screenshot to describe: a late shot
        # arrived after the worker short-circuited, so requeue_skipped_for_vlm
        # will re-enable VLM. A skip with NO screenshot (privacy store_images=
        # False, or capture returned None) is a TERMINAL image-less record —
        # nothing will ever requeue it, so it is not "pending VLM".
        or (
            analysis_status == "vlm_done"
            and not has_desc
            and category_final is None
            and (row["screenshot_count"] or 0) > 0
        )
    )
    needs_classification = has_desc and category_final is None
    completed = analysis_status == "vlm_done" and has_desc and category_final is not None

    return {
        "id": row["id"],
        "client_record_id": row["client_record_id"],
        "single_process": single_process,
        "event_type": row["event_type"],
        "capture_reason": row["capture_reason"],
        "app_name": row["app_name"],
        "process_name": row["process_name"],
        "window_title": row["window_title"],
        "url": row["url"],
        # times (epoch ms)
        "ts_start": ts_start,
        "ts_end": ts_end,
        "created_at": created_at,
        # derived latencies (ms; null = unknown / not-applicable). ingest_delay
        # can be slightly negative under client/server clock skew in dual-process
        # — returned raw; the UI annotates rather than fakes a value.
        "ingest_delay_ms": None if single_process else created_at - ts_start,
        "screenshot_lag_ms": (first_shot_at - created_at) if first_shot_at is not None else None,
        "activity_duration_ms": (ts_end - ts_start) if ts_end is not None else None,
        "queue_wait_ms": None,  # Phase B (no queued_at stamp yet)
        "vlm_duration_ms": row["vlm_latency_ms"],  # column exists; NULL until worker writes it
        "total_latency_ms": None,  # Phase B (no done_at; updated_at is polluted)
        # pipeline state
        "status": status,
        "record_status": row["record_status"],
        "analysis_status": analysis_status,
        "retry_count": retry_count,
        "next_retry_at": next_retry_at,
        "error_code": row["error_code"],
        "error_msg": row["error_msg"],
        # classification
        "category_final": category_final,
        "category_suggested": row["category_suggested"],
        "confidence": row["confidence"],  # NULL until Phase B wires it
        "desc_chars": row["desc_chars"],
        "vlm_model": vlm_model,
        "screenshot_count": row["screenshot_count"] or 0,
        # flags
        "needs_vlm": needs_vlm,
        "needs_classification": needs_classification,
        "classification_met": None,  # Phase B (needs confidence persisted)
        "completed": completed,
    }


@router.get("/audit/records")
async def list_audit_records(
    request: Request,
    start: int = 0,
    end: int = 9_999_999_999_999,
    limit: int = 50,
    cursor: str | None = None,
) -> dict:
    """Newest-first audit feed of records + pipeline status/latencies/flags.

    - **start** / **end**: epoch-ms window on ``ts_start`` (activity time)
    - **limit**: page size (≤ 200)
    - **cursor**: compound keyset ``"{ts_start}_{id}"`` of the last row of the
      previous page; omit for the newest page (what the live poller refetches)
    """
    db = request.app.state.db
    limit = max(1, min(limit, 200))
    rows = await db.query_audit_records(start, end, limit=limit, cursor=cursor)
    now = _now_ms()
    items = [_shape_row(r, now) for r in rows]
    last = rows[-1] if len(rows) == limit else None
    next_cursor = f"{last['ts_start']}_{last['id']}" if last is not None else None
    return {"items": items, "next_cursor": next_cursor, "server_now": now}
