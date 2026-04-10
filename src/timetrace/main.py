"""TimeTrace application entry point."""

from __future__ import annotations

import asyncio
import logging

import structlog

from timetrace.api.app import create_app
from timetrace.capture.service import CaptureService
from timetrace.config import AppConfig
from timetrace.storage.database import Database
from timetrace.worker.loop import AnalysisWorker

logger = structlog.get_logger(__name__)


async def _run(config: AppConfig) -> None:
    db = Database(config.storage)
    await db.init()

    capture_svc = CaptureService(config.capture, config.privacy, db)
    worker = AnalysisWorker(db)
    app = create_app(db)

    import uvicorn

    server_config = uvicorn.Config(
        app,
        host=config.api_host,
        port=config.api_port,
        log_level="info",
    )
    server = uvicorn.Server(server_config)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(capture_svc.run(), name="capture")
        tg.create_task(worker.run(), name="worker")
        tg.create_task(server.serve(), name="api")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = AppConfig()
    asyncio.run(_run(config))


if __name__ == "__main__":
    main()
