"""Capture Service – monitors active window and takes key-frame screenshots."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog

from timetrace.capture.idle import IdleDetector
from timetrace.capture.privacy import should_capture
from timetrace.capture.screenshot import capture_active_window
from timetrace.capture.window import get_active_window
from timetrace.storage.models import CaptureContext

if TYPE_CHECKING:
    from timetrace.config import CaptureConfig, PrivacyConfig, StorageConfig
    from timetrace.storage.database import Database

logger = structlog.get_logger(__name__)


class CaptureService:
    """Lightweight capture loop: window events + periodic key-frame screenshots."""

    def __init__(
        self,
        capture_cfg: CaptureConfig,
        privacy_cfg: PrivacyConfig,
        db: Database,
        storage_cfg: StorageConfig | None = None,
    ) -> None:
        self._cfg = capture_cfg
        self._privacy = privacy_cfg
        self._db = db
        self._storage_cfg = storage_cfg

        self._idle = IdleDetector()

        self._last_capture_ts: float = 0.0
        self._last_record_id: str | None = None
        self._is_idle: bool = False

        # Track the previous window to detect switches
        self._prev_hwnd: int | None = None
        self._prev_app: str = ""

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._idle.start)
        logger.info("capture_service.started")
        try:
            while True:
                await self._tick()
                await asyncio.sleep(1.0)
        finally:
            await loop.run_in_executor(None, self._idle.stop)

    async def _tick(self) -> None:
        now = time.monotonic()
        idle_s = self._idle.idle_seconds

        # --- Idle state transitions ---
        if idle_s >= self._cfg.idle_threshold_s:
            if not self._is_idle:
                self._is_idle = True
                if self._last_record_id:
                    await self._db.close_record(self._last_record_id)
                    self._last_record_id = None
                logger.info("capture.idle_start", idle_s=idle_s)
            return  # Don't capture while idle
        else:
            if self._is_idle:
                self._is_idle = False
                logger.info("capture.idle_end")

        # --- Get active window ---
        win = get_active_window()
        if win is None:
            return

        ctx = CaptureContext(
            app_name=win.app_name,
            process_name=win.process_name,
            window_title=win.window_title,
            url=win.url,
        )

        if not should_capture(ctx, self._privacy):
            return

        # --- Window-switch detection ---
        window_switched = win.hwnd != self._prev_hwnd and win.hwnd != 0
        if window_switched:
            if self._last_record_id:
                try:
                    await self._db.close_record(self._last_record_id)
                except Exception:
                    logger.warning(
                        "capture.close_record_failed",
                        record_id=self._last_record_id,
                        exc_info=True,
                    )
            record_id = await self._db.insert_record(
                ctx, reason="switch", event_type="window_switch"
            )
            await self._save_screenshot(record_id, win.hwnd, now)
            self._last_record_id = record_id
            self._prev_hwnd = win.hwnd
            self._prev_app = win.app_name
            logger.debug(
                "capture.window_switch",
                app=win.app_name,
                title=win.window_title[:60],
            )
            return

        # --- Heartbeat / max-interval补帧 ---
        elapsed = now - self._last_capture_ts
        if elapsed >= self._cfg.max_capture_interval_s:
            record_id = await self._db.insert_record(
                ctx, reason="heartbeat", event_type="heartbeat"
            )
            await self._save_screenshot(record_id, win.hwnd, now)
            self._last_record_id = record_id
            logger.debug("capture.heartbeat", app=win.app_name)

    async def _save_screenshot(self, record_id: str, hwnd: int, now: float) -> None:
        """Capture screenshot + thumbnail and persist to DB."""
        self._last_capture_ts = now

        if self._storage_cfg is None or not self._privacy.store_images:
            await self._db.mark_pending(record_id)
            return

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            capture_active_window,
            record_id,
            self._storage_cfg,
            hwnd,
        )

        if result is not None:
            rel_img, rel_thumb, sha256, width, height = result
            await self._db.insert_screenshot(
                record_id=record_id,
                path=str(rel_img),
                thumb_path=str(rel_thumb),
                width=width,
                height=height,
                hash_sha256=sha256,
            )

        await self._db.mark_pending(record_id)
