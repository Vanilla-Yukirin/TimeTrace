"""Standalone screenshot scheduling. Every new ingest contains an image."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

import structlog

from timetrace.client.capture.image_encoding import encode_frame
from timetrace.client.capture.privacy import should_capture
from timetrace.client.capture.source import WindowInfo
from timetrace.common.models import CaptureContext

if TYPE_CHECKING:
    from PIL import Image

    from timetrace.client.capture.settings import ScreenshotSettings
    from timetrace.client.core.config import ClientConfig
    from timetrace.client.core.outbox_backend import OutboxBackend

logger = structlog.get_logger(__name__)
_OBSERVATION_GAP_S = 5
_MAX_RECORD_MS = 300_000


class PixelSource(Protocol):
    @property
    def idle_seconds(self) -> float: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def active_window(self) -> WindowInfo | None: ...
    def pixels(self, hwnd: int) -> Image.Image: ...
    def wall_time(self) -> float: ...


class WindowsPixelSource:
    def __init__(self):
        from timetrace.client.capture.idle import IdleDetector

        self._idle = IdleDetector()

    @property
    def idle_seconds(self) -> float:
        return self._idle.idle_seconds

    def start(self):
        self._idle.start()

    def stop(self):
        self._idle.stop()

    def active_window(self):
        from timetrace.client.capture.window import get_active_window

        return get_active_window()

    def pixels(self, hwnd):
        from timetrace.client.capture.screenshot import grab_pixels

        return grab_pixels(hwnd, strict=True)

    def wall_time(self):
        return time.time()


class ScreenshotRecorder:
    def __init__(
        self,
        config: ClientConfig,
        backend: OutboxBackend,
        *,
        source: PixelSource | None = None,
        on_event: Callable[[str, str], None],
        on_capture: Callable[[str], None],
    ):
        self.config = config
        self.backend = backend
        self.source = source if source is not None else WindowsPixelSource()
        self.on_event = on_event
        self.on_capture = on_capture
        self.settings: ScreenshotSettings | None = None
        self.hwnd: int | None = None
        self.switched_at = 0.0
        self.switch_pending = False
        self.next_interval = 0.0
        self.last_attempt = -math.inf
        self.last_hash: str | None = None
        self.last_tick: float | None = None
        self.last_wall = 0
        self.open_record: tuple[str, int] | None = None

    async def run(self):
        threading.Thread(target=self.source.start, name="idle-listen", daemon=True).start()
        try:
            while True:
                await self.tick(time.monotonic())
                await asyncio.sleep(0.25)
        finally:
            await self._close(self.last_wall)
            threading.Thread(target=self.source.stop, name="idle-stop", daemon=True).start()

    async def _close(self, wall: int):
        if self.open_record is None:
            return
        record, start = self.open_record
        self.open_record = None
        try:
            await self.backend.close_record(
                record, ts_end_ms=max(start, min(wall, start + _MAX_RECORD_MS))
            )
        except OSError as exc:
            self.on_event("error", f"记录结束时间未写入 · {type(exc).__name__}")
            logger.warning("capture.close_record_failed", exc_info=True)

    def _context(self, win: WindowInfo | None) -> CaptureContext | None:
        if win is None or not win.hwnd or not self.config.privacy.store_images:
            return None
        ctx = CaptureContext(
            app_name=win.app_name,
            process_name=win.process_name,
            window_title=win.window_title,
            url=win.url,
        )
        return ctx if should_capture(ctx, self.config.privacy) else None

    async def tick(self, now: float):
        wall = int(self.source.wall_time() * 1000)
        if self.last_tick is not None and now - self.last_tick > _OBSERVATION_GAP_S:
            await self._close(self.last_wall)
            self.hwnd = None
        self.last_tick, self.last_wall = now, wall

        settings = self.config.capture
        if settings is not self.settings:
            self.settings = settings
            self.hwnd = None
            self.next_interval = now + settings.interval_s
        interval = settings.strategy == "interval"
        interval_due = interval and now >= self.next_interval
        if interval_due:
            # Keep the cadence after a delay or suspend without replaying missed ticks.
            steps = int((now - self.next_interval) // settings.interval_s) + 1
            self.next_interval += steps * settings.interval_s

        win = self.source.active_window()
        ctx = self._context(win)
        if (
            win is None
            or ctx is None
            or (not interval and self.source.idle_seconds >= settings.idle_threshold_s)
        ):
            await self._close(wall)
            self.hwnd = None
            return
        if win.hwnd != self.hwnd:
            await self._close(wall)
            self.hwnd, self.switched_at, self.switch_pending = win.hwnd, now, True
            self.on_event("switch", win.app_name[:80])

        if interval:
            if not interval_due:
                return
            reason = "interval"
        else:
            elapsed = now - self.last_attempt
            if elapsed < settings.min_capture_interval_s:
                return
            if self.switch_pending:
                if now - self.switched_at < settings.switch_capture_delay_s:
                    return
                reason = "switch"
            elif elapsed >= settings.max_capture_interval_s:
                reason = "heartbeat"
            else:
                return

        self.last_attempt, self.switch_pending = now, False
        previous = self.last_hash if not interval and settings.deduplicate else None
        try:
            image = await asyncio.to_thread(self.source.pixels, win.hwnd)
            if not self._still_foreground(win.hwnd, settings):
                self.on_event("skip", "截图期间前台窗口已变化")
                return
            frame = await asyncio.to_thread(
                encode_frame,
                image,
                settings,
                limit=int(self.config.upload.max_image_mb * 1024 * 1024),
                previous_hash=previous,
            )
            if frame is None:
                self.on_event("skip", "像素相同，跳过重复截图")
                return
            await self._close(wall)
            if not self._still_foreground(win.hwnd, settings):
                return
            record = await self.backend.submit_frame(ctx, frame, ts_start=wall, reason=reason)
        except Exception as exc:  # Capture/encoding/storage failures must not stop the daemon.
            self.on_event("error", f"截图未入队 · {type(exc).__name__}")
            logger.warning("capture.frame_failed", exc_info=True)
            return
        self.open_record = record, wall
        self.last_hash = frame.pixel_hash
        self.on_capture(reason)
        self.on_event("capture", f"{frame.extension.upper()} · {len(frame.image) // 1024} KB")

    def _still_foreground(self, hwnd: int, settings: ScreenshotSettings) -> bool:
        current = self.source.active_window()
        return (
            self.config.capture is settings
            and current is not None
            and current.hwnd == hwnd
            and self._context(current) is not None
        )
