"""POST /v1/ingest/record — primary upload endpoint for HttpBackend.

Multipart shape:
  record: JSON-encoded IngestRecordPayload (required)
  image:  bytes, e.g. image/png — optional, validated against image_md5 if set
  thumb:  bytes, e.g. image/jpeg — optional, validated against thumb_md5 if set

Response: IngestRecordResponse. `was_new=False` means a row with the same
`client_record_id` already existed and we returned its id idempotently —
the client treats both 200s the same way and clears the outbox entry.

The route refuses to attach a screenshot to a replay (was_new=False) so the
second submission can't accidentally double-write the same blob; if the first
submission lost the screenshot mid-upload the client should regenerate the
client_record_id rather than retry.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from timetrace.common.models import CaptureContext
from timetrace.common.phash_hash import phash_to_blob
from timetrace.common.protocol import IngestRecordPayload, IngestRecordResponse

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ingest"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _date_path(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{dt.year}/{dt.month:02d}/{dt.day:02d}"


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

    ctx = CaptureContext(
        app_name=payload.app_name,
        process_name=payload.process_name,
        window_title=payload.window_title,
        url=payload.url,
    )
    record_id, was_new = await db.ingest_or_get_record(
        ctx,
        reason=payload.capture_reason,
        event_type=payload.event_type,
        client_record_id=payload.client_record_id,
    )

    screenshot_id: str | None = None
    if was_new and image_bytes is not None:
        if blob_storage is None:
            raise HTTPException(
                status_code=503,
                detail="server is not configured for blob ingestion (no BlobStorage)",
            )

        date = _date_path(payload.ts_start)
        image_key = f"screenshots/{date}/{record_id}.{payload.image_format}"
        await blob_storage.put(image_key, image_bytes, expected_md5=payload.image_md5)

        thumb_key: str | None = None
        if thumb_bytes is not None:
            thumb_key = f"thumbs/{date}/{record_id}.{payload.thumb_format}"
            await blob_storage.put(thumb_key, thumb_bytes, expected_md5=payload.thumb_md5)

        screenshot_id = await db.insert_screenshot(
            record_id=record_id,
            path=image_key,
            thumb_path=thumb_key,
            width=payload.image_width or 0,
            height=payload.image_height or 0,
            hash_sha256=hashlib.sha256(image_bytes).hexdigest(),
            phash=phash_to_blob(payload.image_phash) if payload.image_phash is not None else None,
        )

        if payload.image_phash is not None and phash_index is not None:
            phash_index.insert(screenshot_id, payload.image_phash, payload.ts_start)

    if was_new:
        await db.mark_pending(record_id)

    logger.info(
        "ingest.record",
        record_id=record_id,
        was_new=was_new,
        has_image=image_bytes is not None,
        has_thumb=thumb_bytes is not None,
    )

    return IngestRecordResponse(
        record_id=record_id,
        screenshot_id=screenshot_id,
        was_new=was_new,
        server_received_at=_now_ms(),
    )
