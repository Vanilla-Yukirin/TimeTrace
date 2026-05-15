"""``timetrace-client`` entry point — client half only (no API, no DB, no worker).

Composition:
  - ClientConfig (server URL + auth token + device_id + outbox dir + upload cap)
  - Outbox (append-only, fsync-on-write)
  - HttpBackend (talks to remote/local timetrace-server over /v1/ingest/*)
  - OutboxBackend (BackendClient → outbox.append, fed into CaptureService)
  - OutboxSender (drains outbox → HttpBackend, with backoff + optional rate limit)
  - CaptureService (window watch + screenshot + idle detection)
  - System tray

Intentionally NOT included: API server, DB, AnalysisWorker. Those live in
``timetrace-server``.

First-time setup: drop a ``client.toml`` at
``%USERPROFILE%/TimeTraceData/client.toml`` (or run ``timetrace-client init``
when that command lands). Without a valid token + server URL the client will
still start but every upload will 401 against an auth-gated server.

For the legacy single-process all-in-one mode, see
``src/timetrace/main.py`` (reachable via ``uv run timetrace``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import structlog
from dotenv import load_dotenv

load_dotenv()

from timetrace.client.capture.service import CaptureService  # noqa: E402
from timetrace.client.core.backend import HttpBackend  # noqa: E402
from timetrace.client.core.config import ClientConfig  # noqa: E402
from timetrace.client.core.outbox import Outbox  # noqa: E402
from timetrace.client.core.outbox_backend import OutboxBackend, make_http_sender  # noqa: E402
from timetrace.client.core.outbox_sender import OutboxSender  # noqa: E402
from timetrace.client.tray import start_tray_thread  # noqa: E402
from timetrace.common.config import CaptureConfig, PrivacyConfig, StorageConfig  # noqa: E402

logger = structlog.get_logger(__name__)


def _warn_if_shares_data_dir_with_server(storage_cfg: StorageConfig) -> None:
    """Detect single-machine "client + server in two terminals" mode and warn.

    Until P4 moves capture to write directly into outbox/blobs/ (skipping the
    canonical screenshots/ tree), running both client and server with the
    default data_dir on the same box stores every screenshot twice — once
    under ``data_dir/screenshots/.../{ts}_{rid}.png`` (capture's local copy)
    and once under ``data_dir/screenshots/.../{rid}.png`` (server's blob
    storage from the upload). Different filenames so no overwrite, but
    double the disk footprint.

    Heuristic: if the server's DB exists under this data_dir, server has
    been running here too. Surface a single startup warning so the user
    can either separate data_dirs or accept the duplication knowingly.
    """
    if storage_cfg.db_path.exists():
        logger.warning(
            "client.shared_data_dir_with_server",
            data_dir=str(storage_cfg.data_dir),
            note=(
                "server's SQLite DB exists at this data_dir; if you run server here too "
                "every screenshot will be stored twice until P4 (capture writes to outbox "
                "blobs only). Consider pointing client.toml's outbox at a separate dir, "
                "or run server on a different machine."
            ),
        )


async def _run(
    client_cfg: ClientConfig,
    storage_cfg: StorageConfig,
    capture_cfg: CaptureConfig,
    privacy_cfg: PrivacyConfig,
    quit_event: asyncio.Event,
) -> None:
    # AsyncExitStack guarantees HttpBackend (and any future async-cleanup
    # resource) is closed even if a constructor below it throws — without
    # the stack, an exception between HttpBackend(...) and the TaskGroup
    # try/finally would leak the httpx pool.
    async with contextlib.AsyncExitStack() as stack:
        # 1) Persistence layer
        outbox = Outbox(client_cfg.outbox.root_dir)
        logger.info(
            "client.outbox_loaded",
            root=str(client_cfg.outbox.root_dir),
            pending=await outbox.pending_count(),
        )

        # 2) Network transport — registered for aclose() before anything that
        # could raise during construction.
        http = HttpBackend(
            base_url=client_cfg.server.url,
            auth_token=client_cfg.server.auth_token or None,
            device_id=client_cfg.device.id or None,
            data_dir=storage_cfg.data_dir,
        )
        stack.push_async_callback(http.aclose)

        # 3) Capture-facing backend (queues into outbox)
        backend = OutboxBackend(outbox, data_dir=storage_cfg.data_dir)

        # 4) Capture service feeds the outbox
        capture_svc = CaptureService(
            capture_cfg,
            privacy_cfg,
            backend,
            storage_cfg=storage_cfg,
        )

        # 5) Sender drains outbox → HTTP
        sender = OutboxSender(
            outbox,
            make_http_sender(http),
            max_kbps=client_cfg.upload.max_kbps,
        )
        sender_stop = asyncio.Event()

        async def _watch_quit() -> None:
            await quit_event.wait()
            logger.info("client.stop_requested")
            sender_stop.set()
            for task in asyncio.all_tasks():
                if task.get_name() == "capture":
                    task.cancel()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(capture_svc.run(), name="capture")
            tg.create_task(sender.run(sender_stop), name="sender")
            tg.create_task(_watch_quit(), name="quit_watcher")
    logger.info("client.shutdown_complete")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    client_cfg = ClientConfig.load_or_default()
    # First-launch ergonomics: mint a device_id and persist client.toml so the
    # user can edit it instead of staring at "where do I put my token".
    if not client_cfg.device.id:
        client_cfg.ensure_device_id()
        path = client_cfg.save()
        logger.info("client.config_seeded", path=str(path), device_id=client_cfg.device.id)

    # The capture / privacy / storage configs share the AppConfig defaults; in
    # P3b-3 they'll move into client.toml too. For now reuse what AppConfig has.
    storage_cfg = StorageConfig()
    capture_cfg = CaptureConfig()
    privacy_cfg = PrivacyConfig()

    _warn_if_shares_data_dir_with_server(storage_cfg)

    loop = asyncio.new_event_loop()
    quit_event = asyncio.Event()

    def _request_quit() -> None:
        try:
            loop.call_soon_threadsafe(quit_event.set)
        except RuntimeError:
            pass

    signal.signal(signal.SIGINT, lambda sig, frame: _request_quit())

    # Tray is optional — only meaningful on a Windows desktop. The thread
    # daemonizes so a headless future variant (no display) can simply skip it.
    try:
        start_tray_thread(privacy_cfg, _request_quit)
    except Exception:  # noqa: BLE001
        logger.warning("client.tray_start_failed", exc_info=True)

    logger.info(
        "client.starting",
        server=client_cfg.server.url,
        device_id=client_cfg.device.id,
    )

    try:
        loop.run_until_complete(_run(client_cfg, storage_cfg, capture_cfg, privacy_cfg, quit_event))
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
