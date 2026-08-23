"""POST /v1/ingest/record — primary upload endpoint for HttpBackend.

Multipart shape:
  record: JSON-encoded IngestRecordPayload (required)
  image:  bytes, e.g. image/png — optional, validated against image_md5 if set
  thumb:  bytes, e.g. image/jpeg — optional, validated against thumb_md5 if set

Response: IngestRecordResponse. `was_new=False` means a row with the same
`client_record_id` already existed and we returned its id idempotently —
the client treats both 200s the same way and clears the outbox entry.

The route allows attaching a screenshot to an existing record (was_new=False
+ image bytes) so that HttpBackend can drive the capture pipeline as two
separate calls (record-then-screenshot) without losing the screenshot when
the second call fires after the first has already created the record.
At-most-once delivery of the same payload is the outbox's responsibility,
not the route's.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ValidationError

from timetrace.common.models import CaptureContext
from timetrace.common.phash_hash import phash_to_blob
from timetrace.common.protocol import IngestRecordPayload, IngestRecordResponse
from timetrace.server.db import DeviceBindingError, DeviceRecordConflict

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ingest"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _date_path(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{dt.year}/{dt.month:02d}/{dt.day:02d}"


def _device_id(request: Request) -> str | None:
    raw = request.headers.get("X-Device-Id")
    if raw is None:
        return None
    try:
        parsed = uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="X-Device-Id must be a UUID") from exc
    canonical = str(parsed)
    if raw != canonical:
        raise HTTPException(
            status_code=422,
            detail="X-Device-Id must use canonical lowercase UUID form",
        )
    return canonical


def _device_principal(request: Request) -> tuple[str | None, str | None]:
    principal = getattr(request.state, "bearer_principal", None)
    return (
        principal.token_fingerprint if principal else None,
        principal.token_label if principal else None,
    )


@router.post("/ingest/record", response_model=IngestRecordResponse)
async def ingest_record(
    request: Request,
    record: str = Form(...),
    image: UploadFile | None = File(None),
    thumb: UploadFile | None = File(None),
) -> IngestRecordResponse:
    db = request.app.state.db
    blob_storage = getattr(request.app.state, "blob_storage", None)
    phash_index = request.app.state.phash_index

    try:
        payload = IngestRecordPayload.model_validate_json(record)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    image_bytes: bytes | None = None
    if image is not None:
        image_bytes = await image.read()
        if payload.image_md5:
            actual = _md5(image_bytes)
            if actual != payload.image_md5:
                raise HTTPException(
                    status_code=400,
                    detail=f"image md5 mismatch: expected {payload.image_md5}, got {actual}",
                )

    thumb_bytes: bytes | None = None
    if thumb is not None:
        thumb_bytes = await thumb.read()
        if payload.thumb_md5:
            actual = _md5(thumb_bytes)
            if actual != payload.thumb_md5:
                raise HTTPException(
                    status_code=400,
                    detail=f"thumb md5 mismatch: expected {payload.thumb_md5}, got {actual}",
                )

    device_id = _device_id(request)
    token_fingerprint, token_label = _device_principal(request)

    ctx = CaptureContext(
        app_name=payload.app_name,
        process_name=payload.process_name,
        window_title=payload.window_title,
        url=payload.url,
    )
    try:
        if device_id is None:
            record_id, was_new = await db.ingest_or_get_record(
                ctx,
                reason=payload.capture_reason,
                event_type=payload.event_type,
                client_record_id=payload.client_record_id,
                ts_start=payload.ts_start,
            )
        else:
            record_id, was_new = await db.ingest_device_or_get_record(
                ctx,
                reason=payload.capture_reason,
                event_type=payload.event_type,
                client_record_id=payload.client_record_id,
                device_id=device_id,
                token_fingerprint=token_fingerprint,
                token_label=token_label,
                # Honour the client's capture clock (protocol contract: the server
                # does not rewrite it during a delayed outbox drain).
                ts_start=payload.ts_start,
            )
    except (DeviceBindingError, DeviceRecordConflict) as exc:
        status_code = 403 if isinstance(exc, DeviceBindingError) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    screenshot_id: str | None = None
    if image_bytes is not None:
        if blob_storage is None:
            raise HTTPException(
                status_code=503,
                detail="server is not configured for blob ingestion (no BlobStorage)",
            )

        # Bucket the blob by the *record's* ts_start (the moment the activity
        # happened), not by payload.ts_start (which HttpBackend.submit_screenshot
        # sets to upload time). Otherwise an outbox flush past midnight files
        # late-night activity under the next day's directory.
        record_ts_start = await db.get_record_ts_start(record_id) or payload.ts_start
        date = _date_path(record_ts_start)
        image_key = f"screenshots/{date}/{record_id}.{payload.image_format}"
        await blob_storage.put(image_key, image_bytes, expected_md5=payload.image_md5)

        thumb_key: str | None = None
        if thumb_bytes is not None:
            thumb_key = f"thumbs/{date}/{record_id}.{payload.thumb_format}"
            await blob_storage.put(thumb_key, thumb_bytes, expected_md5=payload.thumb_md5)

        screenshot_id, screenshot_was_new = await db.insert_screenshot(
            record_id=record_id,
            path=image_key,
            thumb_path=thumb_key,
            width=payload.image_width or 0,
            height=payload.image_height or 0,
            hash_sha256=hashlib.sha256(image_bytes).hexdigest(),
            phash=phash_to_blob(payload.image_phash) if payload.image_phash is not None else None,
        )

        # Skip pHash side-index update on replay so the BK-tree doesn't end up
        # with two entries pointing at the same screenshot.
        if screenshot_was_new and payload.image_phash is not None and phash_index is not None:
            phash_index.insert(screenshot_id, payload.image_phash, record_ts_start)

        # The screenshot just landed. If the worker already short-circuited this
        # record to vlm_done with no image (metadata arrives first, screenshot
        # seconds later — the 1s-poll worker usually wins that race), re-enqueue
        # it so it gets described + classified now that the image is here.
        if screenshot_was_new and await db.requeue_skipped_for_vlm(record_id):
            logger.info("ingest.requeued_after_screenshot", record_id=record_id)

    # mark_pending is idempotent (INSERT OR IGNORE under the hood) so it's safe
    # to call on every ingest, including replays where was_new=False.
    await db.mark_pending(record_id)

    if device_id is not None:
        metadata = payload.device
        await db.touch_device(
            device_id,
            name=metadata.name if metadata else None,
            description=metadata.description if metadata else None,
            client_version=metadata.client_version if metadata else None,
            capabilities=metadata.capabilities if metadata else None,
        )

    logger.info(
        "ingest.record",
        record_id=record_id,
        was_new=was_new,
        has_image=image_bytes is not None,
        has_thumb=thumb_bytes is not None,
        device_id=device_id or "",
    )

    return IngestRecordResponse(
        record_id=record_id,
        screenshot_id=screenshot_id,
        was_new=was_new,
        server_received_at=_now_ms(),
    )


class CloseRecordRequest(BaseModel):
    ts_end: int | None = None


@router.post("/ingest/record/{record_id}/close")
async def close_record(
    record_id: str,
    request: Request,
    body: CloseRecordRequest | None = None,
) -> dict:
    """Stamp ts_end on an existing record.

    ``record_id`` may be either the server-assigned record id OR the
    ``client_record_id`` the client supplied at ingest. Accepting both lets a
    restarted HttpBackend (which has lost its in-memory id mapping) still
    close records by the only id it always knows, the client one.

    The body is optional; when present ``ts_end`` is the client's epoch ms
    (so the boundary reflects the user's actual window-switch moment rather
    than network arrival time). When absent, server clock is used.
    """
    db = request.app.state.db
    device_id = _device_id(request)
    token_fingerprint, token_label = _device_principal(request)
    ts_end = body.ts_end if body is not None else None
    effective_ts_end = ts_end if ts_end is not None else _now_ms()

    try:
        resolved_id = await db.close_device_record(
            record_id,
            device_id=device_id,
            token_fingerprint=token_fingerprint,
            token_label=token_label,
            ts_end=effective_ts_end,
        )
    except DeviceBindingError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if resolved_id is None:
        raise HTTPException(status_code=404, detail=f"record not found: {record_id!r}")

    return {"record_id": resolved_id, "ts_end": effective_ts_end}
