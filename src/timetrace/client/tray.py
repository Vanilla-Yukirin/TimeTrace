"""System tray icon (pystray) for TimeTrace.

Runs in its own thread so it doesn't block the asyncio event loop.
Provides pause/resume and quit controls.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from timetrace.common.config import PrivacyConfig

logger = structlog.get_logger(__name__)


def _make_icon():
    """Create a minimal 16x16 PIL image for the tray icon."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, 14, 14], outline=(80, 160, 255), width=2)
    draw.line([8, 8, 8, 4], fill=(80, 160, 255), width=1)
    draw.line([8, 8, 11, 8], fill=(80, 160, 255), width=1)
    return img


class TrayIcon:
    """Wraps a pystray.Icon and exposes a simple thread-based lifecycle."""

    def __init__(
        self,
        privacy_cfg: PrivacyConfig,
        on_quit: Callable[[], None],
    ) -> None:
        self._privacy = privacy_cfg
        self._on_quit = on_quit
        self._icon = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Run the tray icon (blocking – call from a dedicated thread)."""
        try:
            import pystray

            icon = pystray.Icon(
                name="TimeTrace",
                icon=_make_icon(),
                title="TimeTrace",
                menu=self._build_menu(pystray),
            )
            self._icon = icon
            icon.run()
        except Exception:
            logger.warning("tray.failed_to_start", exc_info=True)

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _build_menu(self, pystray):
        def pause_label(icon) -> str:
            return "继续采集" if self._privacy.paused else "暂停采集"

        def on_pause(icon, item) -> None:  # noqa: ANN001
            self._privacy.paused = not self._privacy.paused
            status = "paused" if self._privacy.paused else "resumed"
            logger.info("tray.capture_toggle", status=status)
            icon.title = "TimeTrace（已暂停）" if self._privacy.paused else "TimeTrace"

        def on_quit(icon, item) -> None:  # noqa: ANN001
            logger.info("tray.quit_requested")
            icon.stop()
            self._on_quit()

        return pystray.Menu(
            pystray.MenuItem(pause_label, on_pause),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 TimeTrace", on_quit),
        )


def start_tray_thread(
    privacy_cfg: PrivacyConfig,
    on_quit: Callable[[], None],
) -> threading.Thread:
    """Start the tray icon in a daemon thread.  Returns the thread."""
    tray = TrayIcon(privacy_cfg, on_quit)
    t = threading.Thread(target=tray.run, name="tray", daemon=True)
    t.start()
    logger.info("tray.thread_started")
    return t
