"""Capture Service – monitors active window and takes key-frame screenshots."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog

from timetrace.capture.privacy import should_capture
from timetrace.storage.models import CaptureContext

if TYPE_CHECKING:
    from timetrace.config import CaptureConfig, PrivacyConfig
    from timetrace.storage.database import Database

logger = structlog.get_logger(__name__)


class CaptureService:
    """Lightweight capture loop: window events + periodic key-frame screenshots."""

    def __init__(
        self,
        capture_cfg: CaptureConfig,
        privacy_cfg: PrivacyConfig,
        db: Database,
    ) -> None:
        self._cfg = capture_cfg
        self._privacy = privacy_cfg
        self._db = db
        self._last_capture_ts: float = 0.0

    async def run(self) -> None:
        logger.info("capture_service.started")
        while True:
            await self._tick()
            await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        now = time.time()
        ctx = self._get_active_context()

        if not should_capture(ctx, self._privacy):
            return

        elapsed = now - self._last_capture_ts
        if elapsed < self._cfg.min_capture_interval_s:
            return

        await self._maybe_capture(ctx, now, reason="heartbeat")

    async def _maybe_capture(self, ctx: CaptureContext, now: float, reason: str) -> None:
        elapsed = now - self._last_capture_ts
        if reason == "heartbeat" and elapsed < self._cfg.max_capture_interval_s:
            return

        logger.debug("capture.trigger", reason=reason, app=ctx.app_name)
        record_id = await self._db.insert_record(ctx, reason=reason)
        await self._db.mark_pending(record_id)
        self._last_capture_ts = now

    def _get_active_context(self) -> CaptureContext:
        """Return a stub context (real implementation uses pywin32 on Windows)."""
        return CaptureContext(
            app_name="",
            process_name="",
            window_title="",
            url=None,
        )
