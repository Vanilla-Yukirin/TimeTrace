"""TimeTrace single-process entry point — capture + server in one process.

The server-side composition is delegated to :mod:`timetrace.server.bootstrap`
so this file shares the exact same DB / VLM / worker / API setup as the
standalone ``timetrace-server`` entry. The all-in-one form just adds:

- ``InProcessBackend`` over the bootstrap's DB + PHashIndex
- ``CaptureService`` running on top of that backend
- system tray
- a ``capture`` task injected into the bootstrap's TaskGroup via
  ``serve(extra_tasks=...)``

All other server-side wiring (uvicorn, worker, reclaim loop, signal-driven
quit, VLM/DB cleanup) lives in bootstrap and is shared.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import structlog
from dotenv import load_dotenv

# Load .env before AppConfig() so VLMConfig.from_env() sees the values.
load_dotenv()

from timetrace.client.capture.service import CaptureService  # noqa: E402
from timetrace.client.core.backend import InProcessBackend  # noqa: E402
from timetrace.client.tray import start_tray_thread  # noqa: E402
from timetrace.common.config import AppConfig  # noqa: E402
from timetrace.server.bootstrap import build_server_components, serve  # noqa: E402

logger = structlog.get_logger(__name__)


async def _run(config: AppConfig, quit_event: asyncio.Event) -> None:
    components = await build_server_components(config)

    # Bolt the capture half onto the server bootstrap's TaskGroup.
    backend = InProcessBackend(components.db, phash_index=components.phash_index)
    capture_svc = CaptureService(
        config.capture,
        config.privacy,
        backend,
        storage_cfg=config.storage,
    )
    await serve(
        components,
        config,
        quit_event,
        extra_tasks={"capture": capture_svc.run()},
    )


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    config = AppConfig()
    loop = asyncio.new_event_loop()
    quit_event = asyncio.Event()

    def _request_quit() -> None:
        """Schedule graceful shutdown on the event loop (safe from any thread)."""
        try:
            loop.call_soon_threadsafe(quit_event.set)
        except RuntimeError:
            pass

    # Route Ctrl+C through the same graceful-shutdown path as tray quit so
    # uvicorn exits via should_exit=True instead of a signal re-raise that
    # interrupts the event loop mid-flight.
    signal.signal(signal.SIGINT, lambda sig, frame: _request_quit())

    start_tray_thread(config.privacy, _request_quit)

    try:
        loop.run_until_complete(_run(config, quit_event))
    except (KeyboardInterrupt, SystemExit):
        # Fallback: double Ctrl+C or OS-level interrupt bypasses our handler.
        pass
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except (KeyboardInterrupt, SystemExit, Exception):
                pass
        loop.close()


if __name__ == "__main__":
    main()
