from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
import structlog

from timetrace.common.config import StorageConfig
from timetrace.common.logging import configure_logging
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth, TokenEntry
from timetrace.server.db import Database


def test_nested_crash_is_logged_and_secrets_are_removed(tmp_path):
    configure_logging("client", logs_dir=tmp_path)
    try:
        try:
            raise PermissionError("tt_live_private-secret https://private.invalid/?token=secret")
        except PermissionError as cause:
            raise ExceptionGroup("sender failed", [cause])
    except Exception:
        structlog.get_logger().exception(
            "client.crashed", auth_token="private-secret", title="private-window-title"
        )
    logging.getLogger("library").warning("Bearer private-secret")
    rows = [json.loads(line) for line in (tmp_path / "client.log").read_text().splitlines()]
    assert rows[0]["service"] == "client"
    assert rows[0]["event"] == "client.crashed"
    assert "PermissionError" in rows[0]["exception"]
    assert "ExceptionGroup" in rows[0]["exception"]
    assert "private-secret" not in json.dumps(rows)
    assert "private.invalid" not in json.dumps(rows)
    assert "private-window-title" not in json.dumps(rows)
    for handler in logging.getLogger().handlers[:]:
        logging.getLogger().removeHandler(handler)
        handler.close()
    structlog.reset_defaults()


@pytest.fixture
async def monitored_app(tmp_path):
    db = Database(StorageConfig(data_dir=tmp_path))
    await db.init()
    auth = ServerAuth([TokenEntry(value="fixture-token")])
    app = create_app(db, auth=auth)
    yield app, db
    await db.close()


async def test_metrics_auth_request_ids_and_bounded_route_labels(monitored_app):
    app, _ = monitored_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get("/metrics")).status_code == 401
        response = await api.get("/healthz", headers={"X-Request-ID": "fixture-request"})
        assert response.headers["x-request-id"] == "fixture-request"
        response = await api.get("/healthz", headers={"X-Request-ID": "invalid/id"})
        assert response.headers["x-request-id"] != "invalid/id"
        await api.get("/not-a-real-route/private-document-name")
        response = await api.get("/metrics", headers={"Authorization": "Bearer fixture-token"})
        assert response.status_code == 200
        assert 'route="/healthz"' in response.text
        assert 'route="unmatched"' in response.text
        assert "private-document-name" not in response.text
        assert "fixture-token" not in response.text
        assert "timetrace_analysis_oldest_pending_seconds 0.0" in response.text


async def test_readiness_detects_stopped_required_task_and_database(monitored_app):
    app, db = monitored_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        assert (await api.get("/readyz")).status_code == 200
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        app.state.background_tasks["worker"] = task
        assert (await api.get("/readyz")).status_code == 503
        app.state.background_tasks.clear()
        await db.close()
        assert (await api.get("/readyz")).status_code == 503
