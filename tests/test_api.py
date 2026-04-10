"""Tests for the Local API server."""

import pytest
from fastapi.testclient import TestClient

from timetrace.api.app import create_app
from timetrace.config import StorageConfig
from timetrace.storage.database import Database


@pytest.fixture
async def client(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    db = Database(cfg)
    await db.init()
    app = create_app(db)
    with TestClient(app) as c:
        yield c
    await db.close()


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_records_empty(client):
    resp = client.get("/v1/records")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["next_cursor"] is None


def test_search_placeholder(client):
    resp = client.post("/v1/search", json={"query_text": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []


def test_feedback_accepted(client):
    resp = client.post(
        "/v1/feedback",
        json={"record_id": "abc", "action": "confirm", "category": "work"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
