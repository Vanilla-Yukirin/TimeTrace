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
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import structlog
import uvicorn

from timetrace.common.config import AppConfig
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth
from timetrace.server.db import Database
from timetrace.server.embedding.client import EmbeddingClient
from timetrace.server.phash_index.index import PHashIndex
from timetrace.server.storage.blob import LocalBlobStorage
from timetrace.server.users import UserStore
from timetrace.server.vlm.client import VLMClient
from timetrace.server.vlm.health import VLMHealthGate
from timetrace.server.worker.loop import AnalysisWorker

logger = structlog.get_logger(__name__)

_STALE_TASK_RECLAIM_INTERVAL_S = 60
# AI 看板定时生成：首次延迟（给采集/启动让路）+ 间隔。LLM 调用开销大，间隔
# 取 30min（用户要求的最短档）。
_REPORT_INITIAL_DELAY_S = 30
_REPORT_INTERVAL_S = 1800


@dataclass
class ServerComponents:
    """Bundle of every server-side singleton, ready for ``serve()``."""

    db: Database
    phash_index: PHashIndex
    blob_storage: LocalBlobStorage
    auth: ServerAuth
    users: UserStore
    vlm_client: VLMClient | None
    embedding_client: EmbeddingClient | None
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

    if config.embedding is not None:
        embedding_client: EmbeddingClient | None = EmbeddingClient(config.embedding)
        logger.info(
            "embedding.ready",
            model=config.embedding.model,
            base_url=config.embedding.base_url,
            dim=config.embedding.dim,
        )
    else:
        embedding_client = None
        logger.info("embedding.disabled", reason="no_model_configured")

    blob_storage = LocalBlobStorage(config.storage.data_dir)
    auth, was_generated, generated = ServerAuth.load_or_generate()
    if was_generated and generated is not None:
        # First-start banner — user copies this into client.toml's auth_token.
        logger.info(
            "auth.token_generated",
            label=generated.label,
            value=generated.value,
        )

    # Login-system: seed admin/admin (or env-configured username) on first
    # start. ``ensure_admin_seeded`` is idempotent and a no-op once a row
    # exists, so subsequent boots are cheap.
    users = UserStore(db, config.auth)
    seeded = await users.ensure_admin_seeded()
    if seeded:
        # NEVER print the plaintext password — journal / syslog persists it
        # forever, and if someone later wires TIMETRACE_ADMIN_INITIAL_PASSWORD
        # this would leak it. ``password_default=True`` says "the literal
        # 'admin' default is in effect"; False would mean the operator
        # provided their own initial via env / config.
        logger.warning(
            "auth.admin_seed.first_start",
            username=config.auth.admin_username,
            password_default=config.auth.admin_initial_password == "admin",
            note="change immediately via Web UI / change-password on first login",
        )

    worker = AnalysisWorker(
        db,
        vlm=vlm_client,
        gate=gate,
        cfg=config.worker,
        storage_cfg=config.storage,
        embedding=embedding_client,
    )

    app = create_app(
        db,
        storage_cfg=config.storage,
        phash_index=phash_index,
        vlm_client=vlm_client,
        blob_storage=blob_storage,
        auth=auth,
        vlm_cfg=config.vlm,
        users=users,
        auth_cfg=config.auth,
    )
    app.state.api_host = config.api_host
    app.state.api_port = config.api_port

    return ServerComponents(
        db=db,
        phash_index=phash_index,
        blob_storage=blob_storage,
        auth=auth,
        users=users,
        vlm_client=vlm_client,
        embedding_client=embedding_client,
        worker=worker,
        app=app,
    )


async def serve(
    components: ServerComponents,
    config: AppConfig,
    quit_event: asyncio.Event,
    *,
    extra_tasks: dict[str, Coroutine[Any, Any, None]] | None = None,
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
            if task.get_name() in {"worker", "reclaim", "report_scheduler"} | extra_task_names:
                task.cancel()

    async def _reclaim_loop() -> None:
        while True:
            await asyncio.sleep(_STALE_TASK_RECLAIM_INTERVAL_S)
            await components.db.reclaim_stale_tasks()
            # Cheap; sweeps expired browser sessions so the table doesn't grow
            # unbounded. Per-request resolve_session() already lazy-rejects them.
            purged = await components.db.purge_expired_sessions()
            if purged:
                logger.info("auth.sessions_purged", count=purged)

    async def _report_scheduler() -> None:
        # AI 看板：定时让 agent 生成 HTML 洞察报告。无 VLM 时直接退出，不影响
        # 其它任务；异常被吞掉只记日志，单次失败不拖垮 TaskGroup。
        if config.vlm is None:
            logger.info("report.scheduler_disabled", reason="no_vlm")
            return
        from timetrace.server.report.generator import SCOPE_HOURS, ReportGenerator

        gen = ReportGenerator(components.db, config.vlm)
        await asyncio.sleep(_REPORT_INITIAL_DELAY_S)
        while True:
            # Refresh EVERY scope (recent_3h / recent_24h / recent_7d), each isolated
            # so one scope's failure doesn't skip the others. ~10-60s per report on a
            # single GPU → up to ~3min per round; fine at the 30-min cadence.
            for sc in SCOPE_HOURS:
                try:
                    await gen.generate(sc)
                except Exception:  # noqa: BLE001
                    logger.warning("report.scheduler_generate_failed", scope=sc, exc_info=True)
            await asyncio.sleep(_REPORT_INTERVAL_S)

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(components.worker.run(), name="worker")
            tg.create_task(server.serve(), name="api")
            tg.create_task(_watch_quit(), name="quit_watcher")
            tg.create_task(_reclaim_loop(), name="reclaim")
            tg.create_task(_report_scheduler(), name="report_scheduler")
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
        if components.embedding_client is not None:
            try:
                await components.embedding_client.aclose()
            except Exception:  # noqa: BLE001
                logger.warning("embedding.aclose_failed", exc_info=True)
        try:
            await components.db.close()
        except Exception:  # noqa: BLE001
            logger.warning("db.close_failed", exc_info=True)
    logger.info("server.shutdown_complete")
