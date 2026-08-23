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
import sys

import structlog
from dotenv import load_dotenv

load_dotenv()

from timetrace import __version__  # noqa: E402
from timetrace.client.capture.service import CaptureService  # noqa: E402
from timetrace.client.core.backend import HttpBackend  # noqa: E402
from timetrace.client.core.config import ClientConfig  # noqa: E402
from timetrace.client.core.endpoints import EndpointSelector  # noqa: E402
from timetrace.client.core.outbox import Outbox  # noqa: E402
from timetrace.client.core.outbox_backend import OutboxBackend, make_http_sender  # noqa: E402
from timetrace.client.core.outbox_sender import OutboxSender  # noqa: E402
from timetrace.client.core.ssh_tunnel import SshTunnelManager  # noqa: E402
from timetrace.client.tray import start_tray_thread  # noqa: E402
from timetrace.common.config import StorageConfig  # noqa: E402

logger = structlog.get_logger(__name__)

_CLIENT_CAPABILITIES = ("capture", "screenshots", "outbox")


_HELP_TEXT = """\
timetrace-client — capture-only client half of TimeTrace.

Usage:
  timetrace-client                 Run the capture loop (default).
  timetrace-client init [opts]     Interactive first-time setup. Use
                                   --non-interactive for env-driven setup.
  timetrace-client print-config    Dump the resolved client.toml + env values.
  timetrace-client -h | --help     Show this help.

Configuration: %USERPROFILE%/TimeTraceData/client.toml (override via env vars
listed in `timetrace.client.core.config`).
"""


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
    quit_event: asyncio.Event,
    runtime: dict | None = None,
) -> None:
    client_cfg.validate_device_metadata(
        source="timetrace-client startup",
        client_version=__version__,
        capabilities=_CLIENT_CAPABILITIES,
    )
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

        # 2) Endpoint selection (multi-path failover) — pick an initial active
        # endpoint before the first send so the transport has a base URL.
        selector = EndpointSelector(client_cfg.server.all_endpoints())
        if runtime is not None:
            runtime["selector"] = selector  # let the tray read live health
        await selector.select()

        async def _on_send_failure() -> None:
            # Fast failover: a failed send re-probes + may switch the active path.
            await selector.select()

        # 3) Network transport — base URL resolved per-request from the selector;
        # registered for aclose() before anything that could raise.
        http = HttpBackend(
            base_url_provider=selector.current_url,
            auth_token=client_cfg.server.auth_token or None,
            device_id=client_cfg.device.id or None,
            device_name=client_cfg.device.name,
            device_description=client_cfg.device.description,
            client_version=__version__,
            capabilities=_CLIENT_CAPABILITIES,
            data_dir=client_cfg.storage.data_dir,
            # Screenshots can be a few hundred KB and the link to a remote
            # server may be slow (residential uplink); 30s was too tight and
            # wedged the outbox re-sending the same blob on every timeout.
            timeout_s=120.0,
        )
        stack.push_async_callback(http.aclose)

        # 3) Capture-facing backend (queues into outbox)
        backend = OutboxBackend(outbox, data_dir=client_cfg.storage.data_dir)

        # 4) Capture service feeds the outbox
        capture_svc = CaptureService(
            client_cfg.capture,
            client_cfg.privacy,
            backend,
            storage_cfg=client_cfg.storage,
        )

        # 5) Sender drains outbox → HTTP
        sender = OutboxSender(
            outbox,
            make_http_sender(http),
            max_kbps=client_cfg.upload.max_kbps,
            max_image_bytes=int(client_cfg.upload.max_image_mb * 1024 * 1024),
            on_send_failure=_on_send_failure,
            concurrency=client_cfg.upload.concurrency,
        )
        sender_stop = asyncio.Event()

        # 6) SSH tunnels for any type=ssh endpoints — started up front so the
        # selector's healthz probe can succeed against the local forward.
        tunnels = SshTunnelManager(client_cfg.server.enabled_endpoints())

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
            tg.create_task(selector.run(sender_stop), name="endpoints")
            if tunnels.count:
                tg.create_task(tunnels.run(sender_stop), name="ssh_tunnels")
            tg.create_task(_watch_quit(), name="quit_watcher")
    logger.info("client.shutdown_complete")


def main() -> None:
    # Subcommand dispatch — kept hand-rolled rather than argparse subparsers
    # because the default-no-args path runs the daemon, and argparse doesn't
    # express that cleanly without losing the bare ``timetrace-client`` UX.
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(_HELP_TEXT)
        return
    if args and args[0] == "init":
        from timetrace.client.init_cmd import run as init_run  # noqa: PLC0415

        sys.exit(init_run(args[1:]))
    if args and args[0] == "print-config":
        _print_config()
        return
    if args:
        print(f"Unknown command: {args[0]}\n", file=sys.stderr)
        print(_HELP_TEXT, file=sys.stderr)
        sys.exit(2)

    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    # Load order: file → env overrides. Env wins so headless deploys can ship
    # a baseline `client.toml` and tune per-host via systemd `Environment=`.
    try:
        client_cfg = ClientConfig.load_or_default().apply_env_overrides()
        client_cfg.validate_device_metadata(
            source="timetrace-client startup",
            client_version=__version__,
            capabilities=_CLIENT_CAPABILITIES,
        )
    except ValueError as exc:
        logger.error("client.config_invalid", error=str(exc))
        sys.exit(2)
    # First-launch ergonomics: mint a device_id and persist client.toml so the
    # user can edit it instead of staring at "where do I put my token".
    if not client_cfg.device.id:
        client_cfg.ensure_device_id()
        path = client_cfg.save()
        logger.info("client.config_seeded", path=str(path), device_id=client_cfg.device.id)

    # Materialize the endpoint list in-memory (legacy single `url` → one entry)
    # so the tray + EndpointSelector share the same EndpointSection objects:
    # toggling .enabled in the tray is then seen by the selector and persisted
    # via client_cfg.save. Does NOT rewrite client.toml unless the user toggles.
    if not client_cfg.server.endpoints:
        client_cfg.server.endpoints = client_cfg.server.all_endpoints()

    _warn_if_shares_data_dir_with_server(client_cfg.storage)

    runtime: dict = {}  # daemon stashes the EndpointSelector here for the tray
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
        start_tray_thread(
            client_cfg.privacy,
            _request_quit,
            endpoints=client_cfg.server.endpoints,
            save_config=client_cfg.save,
            runtime=runtime,
        )
    except Exception:  # noqa: BLE001
        logger.warning("client.tray_start_failed", exc_info=True)

    logger.info(
        "client.starting",
        endpoints=[f"{e.name}={e.url}" for e in client_cfg.server.enabled_endpoints()],
        device_id=client_cfg.device.id,
    )

    try:
        loop.run_until_complete(_run(client_cfg, quit_event, runtime))
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


def _print_config() -> None:
    """Dump the resolved client.toml + env-overlay values to stdout.

    Useful for "what is the daemon actually about to do" debugging without
    starting the heavy capture loop. Token is shown by length only — never
    the value — so the output can be safely pasted into a bug report.
    """
    cfg = ClientConfig.load_or_default().apply_env_overrides()
    token_view = f"<{len(cfg.server.auth_token)} chars>" if cfg.server.auth_token else "<empty>"
    print(f"server.url            = {cfg.server.url}")
    print(f"server.auth_token     = {token_view}")
    eps = cfg.server.all_endpoints()
    src = "configured" if cfg.server.endpoints else "synthesized from url"
    print(f"server.endpoints      = {len(eps)} ({src})")
    for i, ep in enumerate(eps):
        flag = "on " if ep.enabled else "off"
        extra = (
            f"  ssh={ep.ssh_host}:{ep.ssh_port}->{ep.remote_host}:{ep.remote_port}"
            if ep.type == "ssh"
            else ""
        )
        print(f"  [{i}] {flag} {ep.name:12} {ep.type:4} {ep.url}{extra}")
    print(f"device.id             = {cfg.device.id or '<unset>'}")
    print(f"device.name           = {cfg.device.name or '<unset>'}")
    print(f"device.description    = {cfg.device.description or '<unset>'}")
    print(f"outbox.root_dir       = {cfg.outbox.root_dir}")
    print(f"upload.max_kbps       = {cfg.upload.max_kbps}")
    print(f"upload.max_image_mb   = {cfg.upload.max_image_mb}")
    print(f"upload.concurrency    = {cfg.upload.concurrency}")
    print(f"storage.data_dir      = {cfg.storage.data_dir}")
    print(f"capture.min_interval  = {cfg.capture.min_capture_interval_s}s")
    print(f"capture.max_interval  = {cfg.capture.max_capture_interval_s}s")
    print(f"capture.idle_threshold= {cfg.capture.idle_threshold_s}s")
    print(f"capture.mode          = {cfg.capture.capture_mode}")
    print(f"privacy.mode          = {cfg.privacy.mode}")
    print(f"privacy.paused        = {cfg.privacy.paused}")
    print(f"privacy.store_images  = {cfg.privacy.store_images}")
    print(f"privacy.app_blacklist = {cfg.privacy.app_blacklist}")
    print(f"privacy.title_keywords= {cfg.privacy.title_keywords}")


if __name__ == "__main__":
    main()
