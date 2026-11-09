"""Real client composition with a synthetic OS observation source."""

import asyncio
import hashlib
import signal
import sys
import time
from pathlib import Path

import structlog
from PIL import Image, ImageDraw

from timetrace.client.capture.source import WindowInfo
from timetrace.client.cli import _run
from timetrace.client.core.config import ClientConfig, EndpointSection
from timetrace.common.logging import configure_logging
from timetrace.common.phash_hash import compute_phash


class FixtureScreen:
    def __init__(self, capture):
        self.capture = capture
        self.started = time.monotonic()
        # A closed 5-minute window, without changing the machine clock.
        self.epoch = (int(time.time()) // 300 - 4) * 300 + 10

    def start(self):
        pass

    def stop(self):
        pass

    @property
    def idle_seconds(self):
        return 10000 if not self.capture or time.monotonic() - self.started >= 7 else 0

    def wall_time(self):
        return self.epoch + time.monotonic() - self.started

    def active_window(self):
        index = min(2, int((time.monotonic() - self.started) // 2.3))
        return WindowInfo("E2E Editor", "fixture.exe", f"E2E frame {index}", index + 1, 1)

    def screenshot(self, record_id, storage_cfg, hwnd):
        image = Image.new("RGB", (640, 400), (35 + hwnd * 30, 45, 80))
        ImageDraw.Draw(image).text((30, 50), f"TimeTrace E2E frame {hwnd}", fill="white")
        rel = Path("screenshots") / f"{record_id}.png"
        thumb = Path("thumbs") / f"{record_id}.png"
        for target in [rel, thumb]:
            (storage_cfg.data_dir / target).parent.mkdir(parents=True, exist_ok=True)
            image.save(storage_cfg.data_dir / target)
        return (
            rel,
            thumb,
            hashlib.sha256(image.tobytes()).hexdigest(),
            640,
            400,
            compute_phash(image),
        )


async def main():
    root = Path.home() / "TimeTraceData"
    configure_logging("client", logs_dir=root / "logs")
    cfg = ClientConfig()
    cfg.storage.data_dir = root
    cfg.outbox.root_dir = root / "outbox"
    cfg.server.auth_token = "e2e-only-machine-token"
    cfg.server.endpoints = [EndpointSection(name="fixture", url="http://127.0.0.1:18080")]
    cfg.device.id = "b2ce6dbd-e18c-40e4-8705-138ef26bf343"
    cfg.device.name = "E2E Client"
    cfg.capture.min_capture_interval_s = 0.1
    cfg.capture.switch_capture_delay_s = 0.1
    cfg.control.port = 18764
    quit_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        loop.add_signal_handler(sig, quit_event.set)
    try:
        await _run(
            cfg, quit_event, capture_source=FixtureScreen("--capture" in sys.argv), show_tray=False
        )
    except Exception:
        structlog.get_logger().exception("client.crashed")
        raise


if __name__ == "__main__":
    asyncio.run(main())
