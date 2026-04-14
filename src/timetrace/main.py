"""TimeTrace application entry point."""

from __future__ import annotations

import asyncio
import logging

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
        # Cancel infinite-loop tasks so the TaskGroup can exit cleanly.
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

    # Tray needs to signal into the asyncio event loop from its own thread.
    def _on_quit() -> None:
        try:
            loop.call_soon_threadsafe(quit_event.set)
        except RuntimeError:
            pass  # Event loop already closed; process is already exiting

    start_tray_thread(config.privacy, _on_quit)

    try:
        loop.run_until_complete(_run(config, quit_event))
    except* (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
