"""``timetrace-server`` entry point — server half only (no capture, no tray).

Composition:
  - Database (SqliteDatabase)
  - PHashIndex (rebuilt from DB)
  - LocalBlobStorage
  - ServerAuth (auto-generates on first start)
  - VLMClient (optional, from env)
  - AnalysisWorker
  - FastAPI app via create_app(...)
  - uvicorn server

Intentionally NOT included: CaptureService, tray. Those belong to the
``timetrace-client`` entry. A loopback-only deployment can run both on
the same machine in two terminals; a remote deployment runs this on a
server and the client elsewhere.

For the all-in-one single-process default, see ``src/timetrace/main.py``
(reachable via ``uv run timetrace`` — kept as a stable fallback while
the two-process mode matures).
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
from timetrace.server.api.app import create_app  # noqa: E402
from timetrace.server.auth import ServerAuth  # noqa: E402
from timetrace.server.db import Database  # noqa: E402
from timetrace.server.phash_index.index import PHashIndex  # noqa: E402
from timetrace.server.storage.blob import LocalBlobStorage  # noqa: E402
from timetrace.server.vlm.client import VLMClient  # noqa: E402
from timetrace.server.vlm.health import VLMHealthGate  # noqa: E402
from timetrace.server.worker.loop import AnalysisWorker  # noqa: E402

logger = structlog.get_logger(__name__)

_STALE_TASK_RECLAIM_INTERVAL_S = 60


async def _run(config: AppConfig, quit_event: asyncio.Event) -> None:
    db = Database(config.storage)
    await db.init()

    phash_index = await PHashIndex.from_db(db)

    if config.vlm is not None:
        vlm_client = VLMClient(config.vlm)
        gate = VLMHealthGate(vlm_client)
        logger.info("vlm.ready", model=config.vlm.model, base_url=config.vlm.base_url)
    else:
        vlm_client = None
        gate = None
        logger.info("vlm.disabled", reason="no_api_key")

    blob_storage = LocalBlobStorage(config.storage.data_dir)
    auth, was_generated, generated = ServerAuth.load_or_generate()
    if was_generated and generated is not None:
        logger.info("auth.token_generated", label=generated.label, value=generated.value)

    worker = AnalysisWorker(
        db,
        vlm=vlm_client,
        gate=gate,
        cfg=config.worker,
        storage_cfg=config.storage,
    )

    app = create_app(
        db,
        storage_cfg=config.storage,
        phash_index=phash_index,
        vlm_client=vlm_client,
        blob_storage=blob_storage,
        auth=auth,
    )
    app.state.api_host = config.api_host
    app.state.api_port = config.api_port

    import uvicorn  # noqa: PLC0415

    server_config = uvicorn.Config(
        app,
        host=config.api_host,
        port=config.api_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    async def _watch_quit() -> None:
        await quit_event.wait()
        logger.info("server.stop_requested")
        server.should_exit = True
        for task in asyncio.all_tasks():
            if task.get_name() in ("worker", "reclaim"):
                task.cancel()

    async def _reclaim_loop() -> None:
        while True:
            await asyncio.sleep(_STALE_TASK_RECLAIM_INTERVAL_S)
            await db.reclaim_stale_tasks()

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(worker.run(), name="worker")
            tg.create_task(server.serve(), name="api")
            tg.create_task(_watch_quit(), name="quit_watcher")
            tg.create_task(_reclaim_loop(), name="reclaim")
    finally:
        if vlm_client is not None:
            try:
                await vlm_client.aclose()
            except Exception:  # noqa: BLE001
                logger.warning("vlm.aclose_failed", exc_info=True)
        try:
            await db.close()
        except Exception:  # noqa: BLE001
            logger.warning("db.close_failed", exc_info=True)
    logger.info("server.shutdown_complete")


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
