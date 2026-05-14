"""Wire-level data contracts shared by client and server tiers.

These are the payloads BackendClient implementations carry across the boundary
between capture (client tier) and ingest (server tier). The in-process backend
passes them as plain dataclasses; the HTTP backend (P3a) will serialize the
same shapes over multipart. Keeping them in `common/` lets both ends import
without dragging in tier-specific dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass


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
