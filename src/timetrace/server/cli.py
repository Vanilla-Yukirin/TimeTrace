"""``timetrace-server`` entry point — server half only (no capture, no tray).

Composition is delegated to :mod:`timetrace.server.bootstrap` so this file
and ``main.py`` (the all-in-one entry) cannot drift apart on the server-side
graph. CLI here is just: load env / config, build, serve, handle Ctrl+C.

For the all-in-one single-process default, see ``src/timetrace/main.py``.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import structlog
from dotenv import load_dotenv

# load_dotenv() runs BEFORE AppConfig so VLMConfig.from_env() sees the values.
load_dotenv()

from timetrace.common.config import AppConfig  # noqa: E402
from timetrace.server.bootstrap import build_server_components, serve  # noqa: E402

logger = structlog.get_logger(__name__)


async def _run(config: AppConfig, quit_event: asyncio.Event) -> None:
    components = await build_server_components(config)
    await serve(components, config, quit_event)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    config = AppConfig()
    loop = asyncio.new_event_loop()
    quit_event = asyncio.Event()

    def _request_quit() -> None:
        try:
            loop.call_soon_threadsafe(quit_event.set)
        except RuntimeError:
            pass

    signal.signal(signal.SIGINT, lambda sig, frame: _request_quit())

    try:
        loop.run_until_complete(_run(config, quit_event))
    except (KeyboardInterrupt, SystemExit):
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
