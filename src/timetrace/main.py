"""TimeTrace application entry point."""

from __future__ import annotations

import asyncio
import logging
import signal

import structlog

from timetrace.api.app import create_app
from timetrace.capture.service import CaptureService
from timetrace.config import AppConfig
from timetrace.storage.database import Database
from timetrace.tray import start_tray_thread
from timetrace.worker.loop import AnalysisWorker

logger = structlog.get_logger(__name__)

_STALE_TASK_RECLAIM_INTERVAL_S = 60


async def _run(config: AppConfig, quit_event: asyncio.Event) -> None:
    db = Database(config.storage)
    await db.init()

    capture_svc = CaptureService(
        config.capture,
        config.privacy,
        db,
        storage_cfg=config.storage,
    )
    worker = AnalysisWorker(db)
    app = create_app(db, storage_cfg=config.storage)
    app.state.api_host = config.api_host
    app.state.api_port = config.api_port

    import uvicorn

    server_config = uvicorn.Config(
        app,
        host=config.api_host,
        port=config.api_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    async def _watch_quit() -> None:
        """Wait for the asyncio quit event, then cancel all sibling tasks."""
        await quit_event.wait()
        logger.info("main.stop_requested")
        server.should_exit = True
        for task in asyncio.all_tasks():
            if task.get_name() in ("capture", "worker", "reclaim"):
                task.cancel()

    async def _reclaim_loop() -> None:
        while True:
            await asyncio.sleep(_STALE_TASK_RECLAIM_INTERVAL_S)
            await db.reclaim_stale_tasks()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(capture_svc.run(), name="capture")
        tg.create_task(worker.run(), name="worker")
        tg.create_task(server.serve(), name="api")
        tg.create_task(_watch_quit(), name="quit_watcher")
        tg.create_task(_reclaim_loop(), name="reclaim")

    await db.close()
    logger.info("main.shutdown_complete")


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
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            except (KeyboardInterrupt, SystemExit, Exception):
                pass
        loop.close()


if __name__ == "__main__":
    main()
