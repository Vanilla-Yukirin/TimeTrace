"""Tests for the Local API server."""

import pytest
from fastapi.testclient import TestClient

from timetrace.api.app import create_app
from timetrace.config import StorageConfig
from timetrace.storage.database import Database
from timetrace.storage.models import CaptureContext


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
async def client(db):
    app = create_app(db)
    with TestClient(app) as c:
        yield c


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


def test_feedback_accepted(client):
    resp = client.post(
        "/v1/feedback",
        json={"record_id": "abc", "action": "confirm", "category": "work/coding"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert "feedback_id" in body


def test_feedback_invalid_action(client):
    resp = client.post(
        "/v1/feedback",
        json={"record_id": "abc", "action": "delete"},
    )
    assert resp.status_code == 422


def test_list_categories(client):
    resp = client.get("/v1/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["categories"], list)
    names = [c["name"] for c in data["categories"]]
    assert "工作/编程" in names


async def test_list_records_with_data(db, tmp_path):
    """Insert records and verify the API returns them with filters."""
    ctx = CaptureContext(app_name="VSCode", process_name="code", window_title="app.py")
    record_id = await db.insert_record(ctx, reason="heartbeat")

    app = create_app(db)
    with TestClient(app) as client:
        resp = client.get("/v1/records?app=VSCode")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == record_id
        assert items[0]["app_name"] == "VSCode"

        resp2 = client.get("/v1/records?q=app.py")
        assert resp2.status_code == 200
        assert len(resp2.json()["items"]) == 1

        resp3 = client.get("/v1/records?app=Chrome")
        assert resp3.status_code == 200
        assert resp3.json()["items"] == []
