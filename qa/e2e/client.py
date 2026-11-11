"""Real client composition with a synthetic OS observation source."""

import asyncio
import errno
import signal
import sys
import time
from pathlib import Path

import structlog
from PIL import Image, ImageDraw

from timetrace.client.capture.source import WindowInfo
from timetrace.client.cli import _run
from timetrace.client.core.config import ClientConfig
from timetrace.common.logging import configure_logging


class FixtureScreen:
    def __init__(self, capture):
        self.capture = capture
        self.started = time.monotonic()
        # A closed 5-minute window, without changing the machine clock.
        self.epoch = (int(time.time()) // 300 - 4) * 300 + 10

    def start(self):
        self.started = time.monotonic()

    def stop(self):
        pass

    @property
    def idle_seconds(self):
        return 10000 if not self.capture or time.monotonic() - self.started >= 7 else 0

    def wall_time(self):
        return self.epoch + time.monotonic() - self.started

    def active_window(self):
        if not self.capture:
            return None
        index = min(2, int((time.monotonic() - self.started) // 2.3))
        return WindowInfo("E2E Editor", "fixture.exe", f"E2E frame {index}", index + 1, 1)

    def pixels(self, hwnd):
        image = Image.new("RGB", (640, 400), (35 + hwnd * 30, 45, 80))
        ImageDraw.Draw(image).text((30, 50), f"TimeTrace E2E frame {hwnd}", fill="white")
        return image


async def main():
    assert Path.home() == Path("/home/timetrace")
    root = Path.home() / "TimeTraceData"
    configure_logging("client", logs_dir=root / "logs")
    config_path = root / "client.toml"
    cfg = ClientConfig.load_or_default(config_path)
    if not config_path.exists():
        cfg.storage.data_dir = root
        cfg.outbox.root_dir = root / "outbox"
        cfg.device.id = "b2ce6dbd-e18c-40e4-8705-138ef26bf343"
        cfg.device.name = "E2E Client"
        cfg.capture.min_capture_interval_s = 0.1
        cfg.capture.switch_capture_delay_s = 0.1
        cfg.capture.image_format = "webp"
        cfg.control.port = 18764
        cfg.save(config_path)
    if "--deny-ack" in sys.argv:
        # Linux cannot reproduce NTFS sharing modes. Inject the same failure
        # at Path.replace; native Windows tests exercise the actual OS denial.
        replace = Path.replace

        def replace_with_ack_fault(path, target):
            if (
                Path(target) == cfg.outbox.root_dir / "state.json"
                and not Path("/artifacts/release-ack").exists()
            ):
                Path("/artifacts/ack-blocked").touch()
                error = PermissionError(errno.EACCES, "fixture: state replacement denied")
                error.winerror = 5
                raise error
            return replace(path, target)

        Path.replace = replace_with_ack_fault
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
