"""Tests for GET /skill — serves the Claude Code skill markdown for download."""

from __future__ import annotations

import httpx
import pytest

from timetrace.common.config import StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.api.routes.skill import _SKILL_PATH
from timetrace.server.db import Database


@pytest.fixture
async def client(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    db = Database(cfg)
    await db.init()
    app = create_app(db, storage_cfg=cfg)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


def test_skill_file_exists_in_repo():
    # The route serves this file; if it's gone the download 404s in prod.
    assert _SKILL_PATH.is_file(), f"skill markdown missing at {_SKILL_PATH}"


async def test_skill_route_serves_markdown(client):
    res = await client.get("/skill")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    body = res.text
    # Frontmatter + the label-only rule + all 6 tool names must be present so an
    # agent that installs it actually learns the real surface.
    assert "name: timetrace" in body
    for tool in (
        "search_activity",
        "get_recent_activity",
        "get_app_breakdown",
        "get_category_stats",
        "apply_label",
        "ask_agent",
    ):
        assert tool in body, f"skill missing tool {tool}"
    assert "tt_live_" in body
    assert "/mcp/" in body


async def test_skill_route_is_open_no_auth(client):
    # Open route (docs); must not require cookie/bearer even when other business
    # routes would. With users=None it's open anyway, but assert 200 (not 401/403).
    res = await client.get("/skill")
    assert res.status_code == 200


async def test_skill_route_uses_packaged_runtime_path(client, tmp_path, monkeypatch):
    runtime_skill = tmp_path / "SKILL.md"
    runtime_skill.write_text("---\nname: packaged-timetrace\n---\n", encoding="utf-8")
    missing_checkout_skill = tmp_path / "missing-SKILL.md"
    monkeypatch.setattr("timetrace.server.api.routes.skill._SKILL_PATH", missing_checkout_skill)
    monkeypatch.setattr("timetrace.server.api.routes.skill._PACKAGED_SKILL_PATH", runtime_skill)

    res = await client.get("/skill")

    assert res.status_code == 200
    assert "name: packaged-timetrace" in res.text
