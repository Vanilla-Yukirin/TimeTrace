"""System tray icon (pystray) for TimeTrace.

Runs in its own thread so it doesn't block the asyncio event loop.
Provides pause/resume and quit controls.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from timetrace.client.core.config import EndpointSection
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
        *,
        endpoints: list[EndpointSection] | None = None,
        save_config: Callable[[], Any] | None = None,
        runtime: dict[str, Any] | None = None,
    ) -> None:
        self._privacy = privacy_cfg
        self._on_quit = on_quit
        # Shared (same objects the EndpointSelector reads) so toggling .enabled
        # here is picked up on the selector's next periodic re-probe. None →
        # no "连接" submenu (headless / single-endpoint legacy).
        self._endpoints = endpoints or []
        self._save_config = save_config
        # Holder set by the daemon once the EndpointSelector exists; used to show
        # live health (● active / ○ healthy / ✕ down) without a hard dependency.
        self._runtime = runtime if runtime is not None else {}
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
            # Windows pystray caches menu text at build time; refresh so the
            # endpoint health glyphs (●/○/✕) track the selector. Only needed
            # when we actually render the connection submenu.
            if self._endpoints:
                threading.Thread(
                    target=self._refresh_menu_loop, name="tray-refresh", daemon=True
                ).start()
            icon.run()
        except Exception:
            logger.warning("tray.failed_to_start", exc_info=True)

    def _refresh_menu_loop(self) -> None:
        """Periodically force a menu refresh so the live health glyphs update."""
        while True:
            time.sleep(5.0)
            icon = self._icon
            if icon is None:
                continue
            try:
                icon.update_menu()
            except Exception:  # noqa: BLE001
                pass

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

        items = [pystray.MenuItem(pause_label, on_pause)]
        if self._endpoints:
            items.append(pystray.MenuItem("连接", self._build_endpoint_menu(pystray)))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("退出 TimeTrace", on_quit))
        return pystray.Menu(*items)

    def _endpoint_glyph(self, ep: EndpointSection) -> str:
        """Live status glyph for an endpoint, read best-effort from the selector
        the daemon stashed in ``runtime``. ● active / ○ healthy / ✕ down / ? n/a."""
        selector = self._runtime.get("selector")
        if selector is None:
            return "?"
        current = selector.current()
        if current is not None and current.name == ep.name:
            return "●"
        health = selector.health.get(ep.name)
        if health is True:
            return "○"
        if health is False:
            return "✕"
        return "?"

    def _build_endpoint_menu(self, pystray):
        """One checkable item per endpoint: toggle enabled, persist, let the
        selector pick it up on its next re-probe. Order is config-only (not here)."""

        def make_toggle(ep: EndpointSection):
            def _toggle(icon, item) -> None:  # noqa: ANN001
                ep.enabled = not ep.enabled
                logger.info("tray.endpoint_toggle", name=ep.name, enabled=ep.enabled)
                if self._save_config is not None:
                    try:
                        self._save_config()
                    except Exception:  # noqa: BLE001
                        logger.warning("tray.endpoint_save_failed", exc_info=True)

            return _toggle

        items = []
        for ep in self._endpoints:
            items.append(
                pystray.MenuItem(
                    (lambda item, ep=ep: f"{self._endpoint_glyph(ep)} {ep.name}  ({ep.url})"),
                    make_toggle(ep),
                    checked=(lambda item, ep=ep: ep.enabled),
                )
            )
        return pystray.Menu(*items)


def start_tray_thread(
    privacy_cfg: PrivacyConfig,
    on_quit: Callable[[], None],
    *,
    endpoints: list[EndpointSection] | None = None,
    save_config: Callable[[], Any] | None = None,
    runtime: dict[str, Any] | None = None,
) -> threading.Thread:
    """Start the tray icon in a daemon thread.  Returns the thread."""
    tray = TrayIcon(
        privacy_cfg,
        on_quit,
        endpoints=endpoints,
        save_config=save_config,
        runtime=runtime,
    )
    t = threading.Thread(target=tray.run, name="tray", daemon=True)
    t.start()
    logger.info("tray.thread_started")
    return t
