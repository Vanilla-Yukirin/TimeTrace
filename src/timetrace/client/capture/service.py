"""Capture Service – monitors active window and takes key-frame screenshots."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import structlog

from timetrace.client.capture.idle import IdleDetector
from timetrace.client.capture.privacy import should_capture
from timetrace.client.capture.screenshot import capture_active_window
from timetrace.client.capture.window import get_active_window
from timetrace.common.models import CaptureContext
from timetrace.common.protocol import ScreenshotSubmission

if TYPE_CHECKING:
    from timetrace.client.core.backend import BackendClient
    from timetrace.common.config import CaptureConfig, PrivacyConfig, StorageConfig

logger = structlog.get_logger(__name__)


class CaptureService:
    """Lightweight capture loop: window events + periodic key-frame screenshots."""

    def __init__(
        self,
        capture_cfg: CaptureConfig,
        privacy_cfg: PrivacyConfig,
        backend: BackendClient,
        storage_cfg: StorageConfig | None = None,
    ) -> None:
        self._cfg = capture_cfg
        self._privacy = privacy_cfg
        self._backend = backend
        self._storage_cfg = storage_cfg

        self._idle = IdleDetector()

        self._last_capture_ts: float = 0.0
        # NB: type is `str` regardless of backend, but the *semantics* differ:
        #   - InProcessBackend → server-assigned record UUID (live in DB)
        #   - OutboxBackend / HttpBackend → client_record_id UUID (no server
        #     row exists yet when this is set; the row materialises after
        #     OutboxSender drains the corresponding ingest entry)
        # The /v1/ingest/record/{id}/close route accepts either form (the
        # 1168971 by-client-id fallback), so capture doesn't need to branch
        # on backend type when calling close_record.
        self._last_record_id: str | None = None
        self._is_idle: bool = False
        self._pending_screenshot_task: asyncio.Task | None = None

        # Track the previous window to detect switches
        self._prev_hwnd: int | None = None
        self._prev_app: str = ""

    async def run(self) -> None:
        # Run pynput listener in a daemon thread so it doesn't block executor
        # shutdown (pynput.start() blocks until stop() is called).
        threading.Thread(target=self._idle.start, name="idle-listen", daemon=True).start()
        logger.info("capture_service.started")
        try:
            while True:
                await self._tick()
                await asyncio.sleep(1.0)
        finally:
            if self._last_record_id:
                await self._safe_close_record(self._last_record_id, where="shutdown")
            # Use a daemon thread so that a hung pynput stop() cannot prevent
            # the process from exiting.  The default executor uses non-daemon
            # threads, which would block process exit if stop() stalls.
            threading.Thread(target=self._idle.stop, name="idle-stop", daemon=True).start()
            await self._cancel_pending_screenshot()

    async def _safe_close_record(self, record_id: str, *, where: str) -> None:
        """Close a record, logging+swallowing any error.

        The capture loop must survive a single failed close — backend hiccups
        or a server briefly down shouldn't tear down the whole capture path.
        ``where`` is a structured log key that pinpoints which call-site
        triggered the failure (shutdown / idle_start / window_switch /
        heartbeat) when post-mortem'ing logs.
        """
        try:
            await self._backend.close_record(record_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "capture.close_record_failed",
                where=where,
                record_id=record_id,
                exc_info=True,
            )

    async def _tick(self) -> None:
        now = time.monotonic()
        idle_s = self._idle.idle_seconds

        # --- Idle state transitions ---
        if idle_s >= self._cfg.idle_threshold_s:
            if not self._is_idle:
                self._is_idle = True
                if self._last_record_id:
                    await self._safe_close_record(self._last_record_id, where="idle_start")
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
                await self._safe_close_record(self._last_record_id, where="window_switch")
            record_id = await self._backend.submit_record(
                ctx, reason="switch", event_type="window_switch"
            )

            # Cancel any pending screenshot task and start a new delayed one
            logger.info(
                "capture.window_switch",
                app=win.app_name,
                title=win.window_title[:60],
            )
            await self._cancel_pending_screenshot()
            self._pending_screenshot_task = asyncio.create_task(
                self._delayed_screenshot(record_id, win.hwnd)
            )

            self._last_record_id = record_id
            self._prev_hwnd = win.hwnd
            self._prev_app = win.app_name
            return

        # --- Heartbeat / max-interval补帧 ---
        elapsed = now - self._last_capture_ts
        if elapsed >= self._cfg.max_capture_interval_s:
            if self._last_record_id:
                await self._safe_close_record(self._last_record_id, where="heartbeat")
            record_id = await self._backend.submit_record(
                ctx, reason="heartbeat", event_type="heartbeat"
            )
            await self._save_screenshot(record_id, win.hwnd, now)
            self._last_record_id = record_id
            logger.debug("capture.heartbeat", app=win.app_name)

    async def _cancel_pending_screenshot(self) -> None:
        """Cancel any pending screenshot task without blocking indefinitely.

        Detach the reference first so that _tick() is never blocked by a task
        that is stuck inside run_in_executor (capture_active_window).
        After cancelling, we wait at most 0.2 s for the task to acknowledge the
        cancellation (fast path: sleeping in the delay).  If it is still running
        after the timeout it means the executor thread has not finished yet — we
        log a warning and move on rather than blocking the capture loop or the
        shutdown path.
        """
        task = self._pending_screenshot_task
        self._pending_screenshot_task = None  # detach immediately

        if task is None or task.done():
            return

        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=0.2)
        if not done:
            logger.warning("capture.pending_screenshot_timed_out")

    async def _delayed_screenshot(self, record_id: str, expected_hwnd: int) -> None:
        """Take a screenshot after a delay, verifying the window hasn't changed."""
        try:
            await asyncio.sleep(self._cfg.switch_capture_delay_s)

            current_win = get_active_window()
            if current_win is None or current_win.hwnd != expected_hwnd:
                logger.debug(
                    "capture.screenshot_cancelled",
                    reason="window_switched_during_delay",
                    expected_hwnd=expected_hwnd,
                    actual_hwnd=current_win.hwnd if current_win else None,
                )
                return

            # Respect minimum capture interval (rate-limit rapid sequential screenshots)
            elapsed_since_last = time.monotonic() - self._last_capture_ts
            if elapsed_since_last < self._cfg.min_capture_interval_s:
                logger.debug(
                    "capture.screenshot_skipped",
                    reason="min_interval_not_elapsed",
                    elapsed_s=round(elapsed_since_last, 2),
                )
                return

            await self._save_screenshot(record_id, expected_hwnd, time.monotonic())
            logger.debug("capture.screenshot_taken", record_id=record_id)

        except asyncio.CancelledError:
            logger.debug("capture.screenshot_cancelled", reason="new_window_switch")
            raise
        finally:
            # Release the reference once this task is done (natural completion or cancel)
            if self._pending_screenshot_task is asyncio.current_task():
                self._pending_screenshot_task = None

    async def _save_screenshot(self, record_id: str, hwnd: int, now: float) -> None:
        """Capture screenshot + thumbnail and persist via the backend."""
        self._last_capture_ts = now

        if self._storage_cfg is None or not self._privacy.store_images:
            await self._backend.mark_pending(record_id)
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
            rel_img, rel_thumb, sha256, width, height, phash = result
            await self._backend.submit_screenshot(
                ScreenshotSubmission(
                    record_id=record_id,
                    path=str(rel_img),
                    thumb_path=str(rel_thumb),
                    width=width,
                    height=height,
                    hash_sha256=sha256,
                    phash=phash,
                )
            )

        await self._backend.mark_pending(record_id)
