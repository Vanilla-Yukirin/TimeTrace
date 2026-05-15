"""Server-side composition shared between ``timetrace-server`` and the legacy
single-process ``timetrace`` entry.

Three pieces:

- :class:`ServerComponents` — frozen handle on every server-side singleton
  (DB, PHashIndex, BlobStorage, Auth, VLM, Worker, FastAPI app).
- :func:`build_server_components` — instantiates the whole graph. Touches
  the disk (DB init, token gen) and the network (no, but VLM client is
  ready to). Returns the dataclass.
- :func:`serve` — runs uvicorn + worker + reclaim loop in a TaskGroup,
  exits on ``quit_event``. Accepts ``extra_tasks`` so the all-in-one
  entry can attach the capture loop without forking the function.

Without this module ``main.py`` and ``server/cli.py`` re-implemented the
same 60 lines in two places. They drifted in the three days between landing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass

import structlog
import uvicorn

from timetrace.common.config import AppConfig
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth
from timetrace.server.db import Database
from timetrace.server.phash_index.index import PHashIndex
from timetrace.server.storage.blob import LocalBlobStorage
from timetrace.server.vlm.client import VLMClient
from timetrace.server.vlm.health import VLMHealthGate
from timetrace.server.worker.loop import AnalysisWorker

logger = structlog.get_logger(__name__)

_STALE_TASK_RECLAIM_INTERVAL_S = 60


@dataclass
class ServerComponents:
    """Bundle of every server-side singleton, ready for ``serve()``."""

    db: Database
    phash_index: PHashIndex
    blob_storage: LocalBlobStorage
    auth: ServerAuth
    vlm_client: VLMClient | None
    worker: AnalysisWorker
    app: object  # FastAPI; loose-typed to avoid pulling fastapi into the dataclass


async def build_server_components(config: AppConfig) -> ServerComponents:
    """Build the full server stack but DON'T run it yet. ``serve()`` does that."""
    db = Database(config.storage)
    await db.init()

    phash_index = await PHashIndex.from_db(db)

    if config.vlm is not None:
        vlm_client: VLMClient | None = VLMClient(config.vlm)
        gate: VLMHealthGate | None = VLMHealthGate(vlm_client)
        logger.info("vlm.ready", model=config.vlm.model, base_url=config.vlm.base_url)
    else:
        vlm_client = None
        gate = None
        logger.info("vlm.disabled", reason="no_api_key")

    blob_storage = LocalBlobStorage(config.storage.data_dir)
    auth, was_generated, generated = ServerAuth.load_or_generate()
    if was_generated and generated is not None:
        # First-start banner — user copies this into client.toml's auth_token.
        logger.info(
            "auth.token_generated",
            label=generated.label,
            value=generated.value,
        )

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

    return ServerComponents(
        db=db,
        phash_index=phash_index,
        blob_storage=blob_storage,
        auth=auth,
        vlm_client=vlm_client,
        worker=worker,
        app=app,
    )


async def serve(
    components: ServerComponents,
    config: AppConfig,
    quit_event: asyncio.Event,
    *,
    extra_tasks: dict[str, Awaitable[None]] | None = None,
) -> None:
    """Run worker + uvicorn + reclaim loop until ``quit_event`` fires.

    ``extra_tasks`` is a name → coroutine map the all-in-one entry uses to
    inject capture / tray-related work. ``_watch_quit`` cancels each named
    task on shutdown alongside the built-in ones, so the TaskGroup unwinds
    cleanly regardless of who added what.
    """
    server_config = uvicorn.Config(
        components.app,
        host=config.api_host,
        port=config.api_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    extra_task_names: set[str] = set(extra_tasks.keys()) if extra_tasks else set()

    async def _watch_quit() -> None:
        await quit_event.wait()
        logger.info("server.stop_requested")
        server.should_exit = True
        for task in asyncio.all_tasks():
            if task.get_name() in {"worker", "reclaim"} | extra_task_names:
                task.cancel()

    async def _reclaim_loop() -> None:
        while True:
            await asyncio.sleep(_STALE_TASK_RECLAIM_INTERVAL_S)
            await components.db.reclaim_stale_tasks()

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(components.worker.run(), name="worker")
            tg.create_task(server.serve(), name="api")
            tg.create_task(_watch_quit(), name="quit_watcher")
            tg.create_task(_reclaim_loop(), name="reclaim")
            for name, coro in (extra_tasks or {}).items():
                tg.create_task(coro, name=name)
    finally:
        # Best-effort cleanup even when the TaskGroup raises (httpx pool from
        # vlm_client must be closed or aiohttp will warn at exit).
        if components.vlm_client is not None:
            try:
                await components.vlm_client.aclose()
            except Exception:  # noqa: BLE001
                logger.warning("vlm.aclose_failed", exc_info=True)
        try:
            await components.db.close()
        except Exception:  # noqa: BLE001
            logger.warning("db.close_failed", exc_info=True)
    logger.info("server.shutdown_complete")
