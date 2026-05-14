"""BackendClient Protocol and the in-process implementation used today.

Capture used to call `Database` and `PHashIndex` directly, which forced the
client tier to import from the server tier. BackendClient pushes the only
boundary capture cares about — "submit a record / submit a screenshot /
mark pending" — into the common surface, so capture stays tier-pure and
the actual transport (in-process today, HTTP at P3a) can swap underneath.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from timetrace.common.phash_hash import phash_to_blob

if TYPE_CHECKING:
    from timetrace.common.models import CaptureContext
    from timetrace.common.protocol import ScreenshotSubmission
    from timetrace.server.db import Database
    from timetrace.server.phash_index.index import PHashIndex


class BackendClient(Protocol):
    """The minimal capture-facing surface for a TimeTrace backend."""

    async def submit_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
    ) -> str:
        """Persist a new activity record. Returns the new record id."""
        ...

    async def close_record(self, record_id: str) -> None:
        """Set ts_end on a previously-submitted record."""
        ...

    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str:
        """Persist a screenshot + thumbnail and update any side indexes.

        Returns the new screenshot id. Implementations are responsible for
        feeding the pHash into whatever similarity index they keep.
        """
        ...

    async def mark_pending(self, record_id: str) -> None:
        """Enqueue a record for downstream analysis (VLM)."""
        ...


class InProcessBackend:
    """Direct adapter over Database + PHashIndex.

    Behaviour-preserving: each method forwards to the same call the capture
    loop made before P2. The only consolidation is `submit_screenshot`, which
    now also handles the phash-index update + ts_start lookup the capture loop
    used to do inline.
    """

    def __init__(self, db: Database, phash_index: PHashIndex | None = None) -> None:
        self._db = db
        self._phash_index = phash_index

    async def submit_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
    ) -> str:
        return await self._db.insert_record(ctx, reason=reason, event_type=event_type)

    async def close_record(self, record_id: str) -> None:
        await self._db.close_record(record_id)

    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str:
        phash_blob = phash_to_blob(payload.phash) if payload.phash is not None else None
        screenshot_id = await self._db.insert_screenshot(
            record_id=payload.record_id,
            path=payload.path,
            thumb_path=payload.thumb_path,
            width=payload.width,
            height=payload.height,
            hash_sha256=payload.hash_sha256,
            privacy_level=payload.privacy_level,
            phash=phash_blob,
        )
        if payload.phash is not None and self._phash_index is not None:
            ts_start = await self._db.get_record_ts_start(payload.record_id)
            if ts_start is not None:
                self._phash_index.insert(screenshot_id, payload.phash, ts_start)
        return screenshot_id

    async def mark_pending(self, record_id: str) -> None:
        await self._db.mark_pending(record_id)
