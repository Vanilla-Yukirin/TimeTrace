"""Wire-level data contracts shared by client and server tiers.

These are the payloads BackendClient implementations carry across the boundary
between capture (client tier) and ingest (server tier). The in-process backend
passes ScreenshotSubmission as a plain dataclass; the HTTP backend serializes
IngestRecordPayload as the multipart `record` part. Keeping them both in
`common/` lets both ends import without dragging in tier-specific dependencies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field


@dataclass
class ScreenshotSubmission:
    """One screenshot + thumbnail to be associated with a record.

    `phash` is a raw 64-bit integer (not the BLOB form stored in SQLite).
    Conversion to bytes for persistence happens inside the backend, so the
    capture side never has to know about storage encoding.
    """

    record_id: str
    path: str
    thumb_path: str | None
    width: int
    height: int
    hash_sha256: str
    phash: int | None = None
    privacy_level: str = "normal"


# --------------------------------------------------------------------------- #
# HTTP wire schema (POST /v1/ingest/record)                                    #
# --------------------------------------------------------------------------- #


Capability = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$"),
]


def validate_device_id(value: str, *, field_name: str = "device.id") -> str:
    """Return a canonical UUID or raise without rewriting caller identity.

    Uppercase/braced UUIDs are parseable, but silently normalizing a configured
    device can reattribute an existing outbox. Report the exact canonical value
    instead and require the operator to make that identity decision explicitly.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ValueError(
            f"{field_name} must use canonical lowercase UUID form; "
            f"the canonical value is {canonical!r}"
        )
    return canonical


class DeviceMetadata(BaseModel):
    """Non-secret client facts refreshed whenever the device ingests data."""

    name: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=512)
    client_version: str = Field(default="", max_length=64)
    capabilities: list[Capability] = Field(default_factory=list, max_length=32)


class IngestRecordPayload(BaseModel):
    """JSON `record` part of a POST /v1/ingest/record multipart request.

    The optional `image` and `thumb` parts (binary) are validated against
    `image_md5` / `thumb_md5` if those fields are set. Everything except
    `client_record_id` and `ts_start` has a sensible default so a minimal
    "ping that there was activity here" record is also valid.
    """

    client_record_id: str = Field(..., min_length=1)
    ts_start: int  # epoch ms; client time — server does not rewrite it
    ts_end: int | None = None

    app_name: str = ""
    process_name: str = ""
    window_title: str = ""
    url: str | None = None

    capture_reason: str = "heartbeat"
    event_type: str = "heartbeat"

    # Main screenshot (optional, attached as multipart `image` part)
    image_md5: str | None = None
    image_width: int | None = None
    image_height: int | None = None
    image_phash: int | None = None  # raw 64-bit
    image_format: str = "png"

    # Thumbnail (optional, attached as multipart `thumb` part)
    thumb_md5: str | None = None
    thumb_format: str = "jpg"

    schema_version: int = 1
    device: DeviceMetadata | None = None


class IngestRecordResponse(BaseModel):
    record_id: str
    screenshot_id: str | None = None
    was_new: bool
    server_received_at: int  # epoch ms
