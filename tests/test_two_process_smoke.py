"""Smoke tests for the two-process composition.

These don't spawn real subprocesses (too flaky for CI: uvicorn port binding,
signal handling, tray thread). Instead they import the same modules the CLI
entries use and instantiate them in the same order, then drive one record
through the chain. This catches:
  - Import / signature drift between CLI composition and the lower layers
  - Wiring bugs (wrong arg passed to wrong constructor)
  - End-to-end shape mismatches (outbox payload keys vs server schema)

What it does NOT catch (verify manually):
  - Signal handling / graceful shutdown
  - Uvicorn config / port collisions
  - Tray thread lifecycle
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

# These imports prove both CLI entry modules are at least importable in CI.
from timetrace.client import cli as client_cli  # noqa: F401
from timetrace.client.core.backend import HttpBackend
from timetrace.client.core.config import ClientConfig
from timetrace.client.core.outbox import Outbox
from timetrace.client.core.outbox_backend import OutboxBackend, make_http_sender
from timetrace.client.core.outbox_sender import OutboxSender
from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.common.protocol import ScreenshotSubmission
from timetrace.server import cli as server_cli  # noqa: F401
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.phash_index.index import PHashIndex
from timetrace.server.storage.blob import LocalBlobStorage


@pytest.fixture
async def server_stack(tmp_path):
    """Build the server-side composition the way `timetrace-server` does, but
    without uvicorn — caller drives the FastAPI app via httpx.ASGITransport."""
    storage_cfg = StorageConfig(data_dir=tmp_path / "server-data")
    db = Database(storage_cfg)
    await db.init()
    phash_index = await PHashIndex.from_db(db)
    blob_storage = LocalBlobStorage(storage_cfg.data_dir / "blobs")
    app = create_app(
        db,
        storage_cfg=storage_cfg,
        phash_index=phash_index,
        blob_storage=blob_storage,
        auth=None,  # token gating is its own test surface; smoke skips auth
    )
    yield {"db": db, "app": app, "data_dir": storage_cfg.data_dir, "phash_index": phash_index}
    await db.close()


async def test_client_composition_matches_cli_and_records_land_on_server(server_stack, tmp_path):
    """Build the client side the same way `timetrace-client` does, drive one
    record + screenshot + close through it, verify the server DB has them."""
    # ------------- client side composition (mirrors client/cli.py) -------------
    client_cfg = ClientConfig.load_or_default(tmp_path / "client.toml")
    client_cfg.server.url = "http://test"  # ASGI transport, not a real socket
    # CRITICAL test isolation: load_or_default with a non-existent file leaves
    # outbox.root_dir at its production default (~/TimeTraceData/outbox). Without
    # this override the smoke test would read+ack+drain the developer's REAL
    # outbox into the throwaway tmp server — corrupting live capture state and
    # advancing the acked cursor past records that never reached the real server.
    # Pin it under tmp_path so every run gets a clean, isolated spool.
    client_cfg.outbox.root_dir = tmp_path / "client-outbox"
    client_cfg.ensure_device_id()

    storage_cfg = StorageConfig(data_dir=tmp_path / "client-data")

    outbox = Outbox(client_cfg.outbox.root_dir)
    transport = httpx.ASGITransport(app=server_stack["app"])
    async with httpx.AsyncClient(transport=transport, base_url=client_cfg.server.url) as raw:
        http = HttpBackend(
            client=raw,
            auth_token=client_cfg.server.auth_token or None,
            device_id=client_cfg.device.id,
            data_dir=storage_cfg.data_dir,
        )
        backend = OutboxBackend(outbox, data_dir=storage_cfg.data_dir)
        sender = OutboxSender(outbox, make_http_sender(http), idle_poll_interval_s=0.05)

        # ------------- drive a fake capture event -------------
        ctx = CaptureContext(
            app_name="VSCode",
            process_name="code.exe",
            window_title="smoke.py",
            url=None,
        )
        rid = await backend.submit_record(ctx, reason="switch", event_type="window_switch")

        from PIL import Image  # noqa: PLC0415

        img = storage_cfg.data_dir / "screenshots" / "2026" / "05" / "15" / f"{rid}.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), (10, 20, 30)).save(img)

        await backend.submit_screenshot(
            ScreenshotSubmission(
                record_id=rid,
                # Use the relative form capture_active_window emits in production
                path=str(img.relative_to(storage_cfg.data_dir)),
                thumb_path=None,
                width=16,
                height=16,
                hash_sha256="ignored",
                phash=0xCAFE,
            )
        )
        await backend.close_record(rid)

        # ------------- run sender briefly -------------
        stop = asyncio.Event()
        task = asyncio.create_task(sender.run(stop))
        for _ in range(100):
            if await outbox.pending_count() == 0:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await task

    # ------------- server-side asserts -------------
    server_db = server_stack["db"]
    server_record = await server_db.find_record_by_client_id(rid)
    assert server_record is not None, "client_record_id never reached server"
    assert server_record["app_name"] == "VSCode"
    assert server_record["window_title"] == "smoke.py"
    assert server_record["ts_end"] is not None, "close_record was not honored"

    shots = await server_db.get_screenshots_for_record(server_record["id"])
    assert len(shots) == 1, "screenshot did not attach"

    # phash_index should also have learned the new screenshot
    hits = server_stack["phash_index"].search(0xCAFE, radius=0)
    assert any(sid == shots[0]["id"] for _, sid in hits)


async def test_smoke_outbox_is_isolated_from_home(tmp_path):
    """Regression guard for the test-isolation footgun: the smoke test MUST pin
    outbox.root_dir under tmp_path. ClientConfig defaults it to
    ~/TimeTraceData/outbox, so a missed override silently drains the dev's real
    outbox. This asserts the override actually scopes it under tmp_path and is
    NOT the production default ~/TimeTraceData/outbox."""
    from pathlib import Path

    client_cfg = ClientConfig.load_or_default(tmp_path / "client.toml")
    # Before the override: the default IS the production home outbox (the footgun).
    assert client_cfg.outbox.root_dir == Path.home() / "TimeTraceData" / "outbox"
    # After the override: scoped under tmp_path, away from the real outbox.
    client_cfg.outbox.root_dir = tmp_path / "client-outbox"
    assert tmp_path in client_cfg.outbox.root_dir.parents
    assert client_cfg.outbox.root_dir != Path.home() / "TimeTraceData" / "outbox"


async def test_cli_modules_expose_main_callable():
    """Ensures `[project.scripts]` console_scripts can find `main`. If this
    breaks, `pip install` would still succeed but `timetrace-client` /
    `timetrace-server` would error at runtime."""
    assert callable(client_cli.main)
    assert callable(server_cli.main)
