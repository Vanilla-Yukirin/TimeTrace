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
import logging
import signal
import threading

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


async def _run(
    client_cfg: ClientConfig,
    storage_cfg: StorageConfig,
    capture_cfg: CaptureConfig,
    privacy_cfg: PrivacyConfig,
    quit_event: asyncio.Event,
) -> None:
    # 1) Persistence layer
    outbox = Outbox(client_cfg.outbox.root_dir)
    logger.info(
        "client.outbox_loaded",
        root=str(client_cfg.outbox.root_dir),
        pending=await outbox.pending_count(),
    )

    # 2) Network transport
    http = HttpBackend(
        base_url=client_cfg.server.url,
        auth_token=client_cfg.server.auth_token or None,
        device_id=client_cfg.device.id or None,
        data_dir=storage_cfg.data_dir,
    )

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

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(capture_svc.run(), name="capture")
            tg.create_task(sender.run(sender_stop), name="sender")
            tg.create_task(_watch_quit(), name="quit_watcher")
    finally:
        try:
            await http.aclose()
        except Exception:  # noqa: BLE001
            logger.warning("http.aclose_failed", exc_info=True)
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

    # Drop a hint about where the user should be looking for config.
    logger.info(
        "client.starting",
        server=client_cfg.server.url,
        device_id=client_cfg.device.id,
        outbox_pending_hint="see logs after start",
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


# Silence unused-import warnings for `threading` that some linters whine about
# in heavily-conditional CLI entry points; tray uses it internally.
_ = threading  # noqa: B018


if __name__ == "__main__":
    main()
