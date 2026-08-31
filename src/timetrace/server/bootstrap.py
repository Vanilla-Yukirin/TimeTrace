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
import time
from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from types import FrameType
from typing import Any

import structlog
import uvicorn

from timetrace.common.config import AppConfig
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth
from timetrace.server.db import Database
from timetrace.server.embedding.client import EmbeddingClient
from timetrace.server.llm_log import LLMRequestLog, set_default_sink
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
_UVICORN_GRACEFUL_SHUTDOWN_S = 30


class _CoordinatedServer(uvicorn.Server):
    """Make Uvicorn signals wake the shared application shutdown path."""

    def __init__(self, config: uvicorn.Config, quit_event: asyncio.Event) -> None:
        super().__init__(config)
        self._quit_event = quit_event

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # Uvicorn installs this bound method after the CLI's handlers. Wake the
        # TaskGroup before Uvicorn begins waiting for active HTTP streams so
        # workers and long-running LLM requests start unwinding immediately.
        self._quit_event.set()
        super().handle_exit(sig, frame)


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

    # Wire the unified LLM-request ledger: every instrumented LLM call site
    # (worker VLM / narrate / ask_agent) writes one row via this sink. Set once,
    # process-wide, so call sites need no DB reference. Sink errors are swallowed
    # inside llm_log so they can't break the actual LLM call.
    async def _llm_request_sink(entry: LLMRequestLog) -> None:
        await db.insert_llm_request(**asdict(entry))

    set_default_sink(_llm_request_sink)

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
        embedding_client=embedding_client,
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
        # Compose allows 40 seconds. Bound Uvicorn's active-request wait so its
        # lifespan and our DB/client cleanup still get time before SIGKILL.
        timeout_graceful_shutdown=_UVICORN_GRACEFUL_SHUTDOWN_S,
    )
    server = _CoordinatedServer(server_config, quit_event)

    extra_task_names: set[str] = set(extra_tasks.keys()) if extra_tasks else set()

    async def _watch_quit() -> None:
        await quit_event.wait()
        logger.info("server.stop_requested")
        server.should_exit = True
        for task in asyncio.all_tasks():
            cancel_names = {
                "worker",
                "reclaim",
                "report_scheduler",
                "rollup",
                "narrate",
            } | extra_task_names
            if task.get_name() in cancel_names:
                task.cancel()

    async def _reclaim_loop() -> None:
        while True:
            await components.db.reclaim_stale_tasks()
            await components.db.expire_stale_open_records()
            # Cheap; sweeps expired browser sessions so the table doesn't grow
            # unbounded. Per-request resolve_session() already lazy-rejects them.
            purged = await components.db.purge_expired_sessions()
            if purged:
                logger.info("auth.sessions_purged", count=purged)
            await asyncio.sleep(_STALE_TASK_RECLAIM_INTERVAL_S)

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

    async def _rollup_loop() -> None:
        # Memory-pyramid metrics cascade: keep recent windows (today + cut spill)
        # fresh. DISABLED by default — only runs when config.rollup.enabled
        # (TIMETRACE_ROLLUP_ENABLED=1). Gated so it can't freeze half-classified
        # historical days into metrics before the classification backfill is done
        # (this slice has no source_hash re-emission yet). LLM-free + pure SQL, so
        # it does NOT depend on the VLM endpoint. Exceptions are swallowed per
        # tick so one bad sweep can't tear down the TaskGroup.
        if not config.rollup.enabled:
            logger.info("rollup.loop_disabled", reason="config.rollup.enabled=False")
            return
        from timetrace.server.summary.rollup import MetricsCascadeBuilder

        builder = MetricsCascadeBuilder(components.db, config.rollup)
        await asyncio.sleep(config.rollup.loop_initial_delay_s)
        logger.info("rollup.loop_started", interval_s=config.rollup.loop_interval_s)
        while True:
            try:
                counts = await builder.build_recent(int(time.time() * 1000))
                if counts:
                    logger.info("rollup.tick", **{f"n_{g}": n for g, n in counts.items()})
            except Exception:  # noqa: BLE001
                logger.warning("rollup.tick_failed", exc_info=True)
            await asyncio.sleep(config.rollup.loop_interval_s)

    async def _narrate_loop() -> None:
        # Memory-pyramid narrative stage: turn finalized metric windows the rollup
        # cascade wrote into structured 流水账/重点/评价 via the VLM chat endpoint.
        # DISABLED by default (TIMETRACE_NARRATE_ENABLED=1) and independent of the
        # rollup loop — it DOES depend on the VLM endpoint, so it no-ops when no
        # VLM is configured. Each tick narrates only still-pending finalized
        # windows (force=False), bounded per grain to pace the single GPU.
        # Exceptions per tick are swallowed; an empty window stays pending and
        # retries next tick (the model's thinking length varies run to run).
        if not config.narrate.enabled:
            logger.info("narrate.loop_disabled", reason="config.narrate.enabled=False")
            return
        if config.vlm is None:
            logger.info("narrate.loop_disabled", reason="no_vlm")
            return
        from openai import AsyncOpenAI

        from timetrace.server.summary.narrative import (
            NarrativeBuilder,
            NarrativeCascade,
            OpenAINarrativeLLM,
        )

        client = AsyncOpenAI(base_url=config.vlm.base_url, api_key=config.vlm.api_key)
        llm = OpenAINarrativeLLM(
            client, config.vlm.model, disable_thinking=config.vlm.disable_thinking
        )
        cascade = NarrativeCascade(components.db, NarrativeBuilder(components.db, llm))
        await asyncio.sleep(config.narrate.loop_initial_delay_s)
        logger.info("narrate.loop_started", interval_s=config.narrate.loop_interval_s)
        try:
            while True:
                try:
                    now = int(time.time() * 1000)
                    start = now - config.narrate.loop_lookback_h * 3_600_000
                    counts = await cascade.narrate_range(
                        start, now, now, per_grain_limit=config.narrate.per_grain_limit
                    )
                    if any(counts.values()):
                        logger.info("narrate.tick", **{f"n_{g}": n for g, n in counts.items()})
                except Exception:  # noqa: BLE001
                    logger.warning("narrate.tick_failed", exc_info=True)
                await asyncio.sleep(config.narrate.loop_interval_s)
        finally:
            await client.close()

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(components.worker.run(), name="worker")
            tg.create_task(server.serve(), name="api")
            tg.create_task(_watch_quit(), name="quit_watcher")
            tg.create_task(_reclaim_loop(), name="reclaim")
            tg.create_task(_report_scheduler(), name="report_scheduler")
            tg.create_task(_rollup_loop(), name="rollup")
            tg.create_task(_narrate_loop(), name="narrate")
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
