from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from timetrace.client.controller import ClientController
from timetrace.client.core.config import ClientConfig, EndpointSection
from timetrace.client.core.endpoints import EndpointSelector
from timetrace.client.core.outbox import Outbox
from timetrace.client.local_api import create_local_app, serve_local_control


async def _make_controller(tmp_path):
    cfg = ClientConfig()
    cfg.server.auth_token = "bearer-super-secret"
    cfg.server.endpoints = [
        EndpointSection(
            name="secret-url",
            url="https://alice:password@example.test/v1?token=query-secret#frag",
        )
    ]

    async def probe(url: str) -> bool:
        return True

    selector = EndpointSelector(cfg.server.endpoints, probe=probe)
    await selector.select()
    stop = asyncio.Event()
    controller = ClientController(
        cfg,
        Outbox(tmp_path / "outbox"),
        selector,
        stop,
        save_controls=lambda paused, endpoints: None,
        stats_ttl_s=0,
    )
    return controller, stop


async def test_local_api_security_status_and_redaction(tmp_path):
    controller, _ = await _make_controller(tmp_path)
    app = create_local_app(controller, port=8764, csrf_token="test-csrf")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8764") as client:
        root = await client.get("/")
        assert root.status_code == 200
        assert root.cookies.get("timetrace_session") == "test-csrf"
        assert root.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
        assert root.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in root.headers

        status = await client.get("/api/status")
        config = await client.get("/api/config")
        combined = status.text + config.text
        for secret in ("bearer-super-secret", "alice", "password", "query-secret"):
            assert secret not in combined
        assert config.json()["auth_token_present"] is True
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404

        bad_host = await client.get("/api/status", headers={"Host": "evil.example"})
        assert bad_host.status_code == 421

        # Middleware rejects cross-origin writes before FastAPI parses bad JSON.
        evil = await client.post(
            "/api/capture/pause",
            content="not-json",
            headers={"Origin": "https://evil.example", "Content-Type": "application/json"},
        )
        assert evil.status_code == 403

        headers = {
            "Origin": "http://127.0.0.1:8764",
            "X-TimeTrace-CSRF": "test-csrf",
            "Content-Type": "application/json",
        }
        missing_cookie_client = httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8764",
        )
        try:
            missing_cookie = await missing_cookie_client.post(
                "/api/capture/pause", headers=headers, json={}
            )
            assert missing_cookie.status_code == 403
        finally:
            await missing_cookie_client.aclose()

        legal = await client.post("/api/capture/pause", headers=headers, json={})
        assert legal.status_code == 200
        assert legal.json() == {"paused": True, "persisted": True, "warning": None}


async def test_shutdown_api_is_idempotent(tmp_path):
    controller, stop = await _make_controller(tmp_path)
    app = create_local_app(controller, port=8764, csrf_token="csrf")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8764") as client:
        await client.get("/")
        headers = {
            "Origin": "http://localhost:8764",
            "X-TimeTrace-CSRF": "csrf",
            "Content-Type": "application/json",
        }
        first = await client.post("/api/shutdown", headers=headers, json={})
        second = await client.post("/api/shutdown", headers=headers, json={})
    assert first.json() == {"accepted": True}
    assert second.json() == {"accepted": False}
    assert stop.is_set()


async def test_default_http_port_accepts_browser_normalized_host_and_origin(tmp_path):
    controller, _ = await _make_controller(tmp_path)
    app = create_local_app(controller, port=80, csrf_token="csrf")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        assert (await client.get("/")).status_code == 200
        response = await client.post(
            "/api/capture/pause",
            headers={
                "Origin": "http://127.0.0.1",
                "X-TimeTrace-CSRF": "csrf",
                "Content-Type": "application/json",
            },
            json={},
        )
        assert response.status_code == 200


async def test_real_loopback_socket_starts_and_stops(tmp_path):
    controller, stop = await _make_controller(tmp_path)
    task = asyncio.create_task(serve_local_control(controller, stop, port=0))
    for _ in range(100):
        url = controller.cached_snapshot().control_url
        if url:
            break
        await asyncio.sleep(0.01)
    assert url.startswith("http://127.0.0.1:")
    async with httpx.AsyncClient(base_url=url) as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        assert (await client.get("/")).status_code == 200
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert controller.cached_snapshot().control_url is None


async def test_port_conflict_degrades_only_local_ui(tmp_path):
    controller, stop = await _make_controller(tmp_path)
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    occupied.bind(("127.0.0.1", 0))
    occupied.listen(1)
    port = occupied.getsockname()[1]
    sibling_alive = asyncio.Event()

    async def sibling() -> None:
        sibling_alive.set()
        await stop.wait()

    sibling_task = asyncio.create_task(sibling())
    try:
        await asyncio.sleep(0)
        await serve_local_control(controller, stop, port=port)
        assert sibling_alive.is_set()
        assert not stop.is_set()
        assert controller.cached_snapshot().last_error == "local_ui_bind: OSError"
    finally:
        occupied.close()
        stop.set()
        await sibling_task


async def test_cancelling_local_server_leaves_no_orphan_tasks(tmp_path):
    controller, stop = await _make_controller(tmp_path)
    task = asyncio.create_task(serve_local_control(controller, stop, port=0))
    for _ in range(100):
        if controller.cached_snapshot().control_url:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    orphan_names = {
        running.get_name()
        for running in asyncio.all_tasks()
        if not running.done() and running is not asyncio.current_task()
    }
    assert "local_api_server" not in orphan_names
    assert "local_api_stop" not in orphan_names
