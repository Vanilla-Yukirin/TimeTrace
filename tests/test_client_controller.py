from __future__ import annotations

import asyncio
import threading

import pytest

from timetrace.client.controller import ClientController, ControllerMutationError
from timetrace.client.core.config import ClientConfig, EndpointSection
from timetrace.client.core.endpoints import EndpointSelector
from timetrace.client.core.outbox import Outbox, OutboxEntry


async def _controller(tmp_path, *, saver=None, opener=lambda url: True):  # noqa: ANN001
    cfg = ClientConfig()
    cfg.server.auth_token = "top-secret"
    cfg.server.endpoints = [
        EndpointSection(name="lan", url="http://alice:pw@lan.test:8765/?token=x"),
        EndpointSection(name="public", url="https://public.test", enabled=True),
    ]

    async def probe(url: str) -> bool:
        return True

    selector = EndpointSelector(cfg.server.endpoints, probe=probe)
    await selector.select()
    quit_event = asyncio.Event()
    controller = ClientController(
        cfg,
        Outbox(tmp_path / "outbox"),
        selector,
        quit_event,
        save_controls=saver,
        browser_opener=opener,
        stats_ttl_s=0,
    )
    return controller, quit_event


async def test_pause_is_fail_closed_and_retries_failed_persistence(tmp_path):
    attempts = 0

    def saver(paused, endpoints):  # noqa: ANN001
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise OSError("disk full")

    controller, _ = await _controller(tmp_path, saver=saver)
    first = await controller.set_paused(True)
    assert first.paused is True
    assert first.persisted is False
    assert controller.config.privacy.paused is True
    assert controller.cached_snapshot().pause_persisted is False

    second = await controller.set_paused(True)
    assert second.persisted is True
    assert attempts == 2
    assert controller.cached_snapshot().pause_persisted is True


async def test_resume_persists_before_enabling_capture(tmp_path):
    calls = 0

    def saver(paused, endpoints):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if not paused:
            raise OSError("read only")

    controller, _ = await _controller(tmp_path, saver=saver)
    assert (await controller.set_paused(True)).persisted is True
    result = await controller.set_paused(False)
    assert result.paused is True
    assert result.persisted is False
    assert controller.config.privacy.paused is True


async def test_endpoint_mutation_validates_then_persists(tmp_path):
    saved: list[tuple[bool, ...] | None] = []

    def saver(paused, endpoints):  # noqa: ANN001
        saved.append(endpoints)

    controller, _ = await _controller(tmp_path, saver=saver)
    snapshot = await controller.set_endpoint_enabled(1, False)
    assert snapshot.endpoints[1].enabled is False
    assert saved[-1] == (True, False)

    with pytest.raises(ControllerMutationError, match="at least one"):
        await controller.set_endpoint_enabled(0, False)


async def test_endpoint_save_failure_does_not_change_live_config(tmp_path):
    def saver(paused, endpoints):  # noqa: ANN001
        raise OSError("no space")

    controller, _ = await _controller(tmp_path, saver=saver)
    with pytest.raises(ControllerMutationError, match="could not be updated"):
        await controller.set_endpoint_enabled(1, False)
    assert controller.config.server.endpoints[1].enabled is True


async def test_snapshot_reports_observed_metrics_without_secrets(tmp_path):
    controller, _ = await _controller(tmp_path)
    outbox = controller._outbox  # noqa: SLF001
    await outbox.append({"kind": "ingest"}, image_bytes=b"abc")
    controller.record_capture("screenshot")
    entry = OutboxEntry("x", 0, {}, None, None)
    controller.record_upload(entry)
    controller.record_send_error(entry, RuntimeError("payload-secret"))

    snapshot = await controller.snapshot(force_stats=True)
    assert snapshot.outbox_pending == 1
    assert snapshot.outbox_bytes > 3
    assert snapshot.outbox_oldest_at is not None
    assert snapshot.last_capture_at is not None
    assert snapshot.last_upload_at is not None
    assert snapshot.last_error == "upload: RuntimeError"
    assert "alice" not in snapshot.endpoints[0].url
    assert "token" not in snapshot.endpoints[0].url


async def test_threadsafe_tray_actions_browser_and_shutdown_are_injectable(tmp_path):
    opened: list[str] = []
    controller, quit_event = await _controller(
        tmp_path, opener=lambda url: opened.append(url) or True
    )
    controller.set_control_url("http://127.0.0.1:8764")

    thread = threading.Thread(target=controller.open_control_panel_from_thread)
    thread.start()
    thread.join()
    for _ in range(20):
        if opened:
            break
        await asyncio.sleep(0.01)
    assert opened == ["http://127.0.0.1:8764"]

    assert controller.request_shutdown() is True
    assert controller.request_shutdown() is False
    assert quit_event.is_set()
