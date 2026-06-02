"""Per-app overrides: the overrides module, the HTTP route, and the worker
rule path (a user-pinned category deterministically beats the VLM)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.rules.engine import VlmPrediction, decide_category
from timetrace.server.settings.overrides import (
    SETTINGS_KEY,
    build_ruleset,
    find_note,
    load_overrides,
    save_overrides,
    validate_overrides,
)


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def client(db):
    app = create_app(db)
    with TestClient(app) as c:
        yield c


# --- module --------------------------------------------------------------- #


async def test_load_overrides_default_empty(db):
    assert await load_overrides(db) == {"version": 1, "apps": {}}


async def test_load_overrides_tolerates_corrupt_json(db):
    await db.set_setting(SETTINGS_KEY, "{not valid json")
    assert await load_overrides(db) == {"version": 1, "apps": {}}


def test_validate_rejects_unknown_category():
    with pytest.raises(ValueError):
        validate_overrides({"apps": {"vanish": {"category": "nonsense"}}})


def test_validate_drops_blank_keys_and_caps_note():
    clean = validate_overrides(
        {"apps": {"  ": {"category": "work"}, "claude": {"note": "x" * 999}}}
    )
    assert "  " not in clean["apps"] and "" not in clean["apps"]
    assert len(clean["apps"]["claude"]["note"]) == 300
    assert clean["apps"]["claude"]["category"] is None


def test_build_ruleset_only_category_entries():
    overrides = {"apps": {"vanish": {"category": "work"}, "obsidian": {"note": "笔记"}}}
    rs = build_ruleset(overrides)
    assert rs.rules == [{"app": "vanish", "category": "work"}]
    # note-only entry does not create a rule
    assert all(r["app"] != "obsidian" for r in rs.rules)


def test_find_note_substring_case_insensitive():
    overrides = {"apps": {"vanish": {"note": "公司 IM"}}}
    assert find_note(overrides, "Vanish.exe") == "公司 IM"
    assert find_note(overrides, "Notepad") is None
    assert find_note(overrides, None) is None


# --- worker rule path ----------------------------------------------------- #


def test_override_category_beats_vlm():
    """A user rule (weight 2.0) outvotes the VLM's pick (1.5) in decide_category."""
    rs = build_ruleset({"apps": {"vanish": {"category": "work"}}})
    final, _conf, _trace = decide_category(
        app="Vanish",
        url=None,
        title="LiteLLM 开发小群",
        vlm_pred=VlmPrediction(category="social", confidence=1.0),
        rules=rs,
    )
    assert final == "work"  # rule wins over the VLM's "social"


# --- HTTP route ----------------------------------------------------------- #


def test_get_app_overrides_default_empty(client):
    resp = client.get("/v1/settings/app-overrides")
    assert resp.status_code == 200
    assert resp.json() == {"version": 1, "apps": {}}


def test_put_then_get_roundtrip(client):
    body = {"apps": {"vanish": {"category": "work", "note": "公司 IM"}}}
    put = client.put("/v1/settings/app-overrides", json=body)
    assert put.status_code == 200
    assert put.json()["apps"]["vanish"]["category"] == "work"
    got = client.get("/v1/settings/app-overrides").json()
    assert got["apps"]["vanish"]["note"] == "公司 IM"


def test_put_rejects_unknown_category(client):
    resp = client.put(
        "/v1/settings/app-overrides",
        json={"apps": {"vanish": {"category": "definitely-not-a-cat"}}},
    )
    assert resp.status_code == 400


async def test_save_overrides_persists_json(db):
    await save_overrides(db, {"apps": {"claude": {"category": "work"}}})
    raw = await db.get_setting(SETTINGS_KEY)
    assert json.loads(raw)["apps"]["claude"]["category"] == "work"
