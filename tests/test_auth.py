"""Tests for ServerAuth (token generation, persistence, validation) and the
bearer dependency wired into the ingest router."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import StorageConfig
from timetrace.server.api.app import create_app
from timetrace.server.auth import ServerAuth, TokenEntry
from timetrace.server.db import Database
from timetrace.server.storage.blob import LocalBlobStorage


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


@pytest.fixture
def blob_storage(tmp_path) -> LocalBlobStorage:
    return LocalBlobStorage(tmp_path / "blobs")


# --------------------------------------------------------------------------- #
# Token persistence                                                             #
# --------------------------------------------------------------------------- #


def test_load_or_generate_creates_token_file_on_first_start(tmp_path):
    auth, was_generated, generated = ServerAuth.load_or_generate(token_dir=tmp_path)
    assert was_generated is True
    assert generated is not None
    assert generated.value.startswith("tt_live_")
    assert (tmp_path / "tokens.json").exists()


def test_generated_token_has_enough_entropy(tmp_path):
    """Two consecutive first-starts in different dirs should yield distinct tokens."""
    a, *_ = ServerAuth.load_or_generate(token_dir=tmp_path / "a")
    b, *_ = ServerAuth.load_or_generate(token_dir=tmp_path / "b")
    assert a.tokens[0].value != b.tokens[0].value


def test_load_or_generate_reads_existing_file_without_minting(tmp_path):
    payload = {"tokens": [{"value": "tt_live_existing", "label": "stored", "created_at": 0}]}
    (tmp_path / "tokens.json").write_text(json.dumps(payload), encoding="utf-8")
    auth, was_generated, generated = ServerAuth.load_or_generate(token_dir=tmp_path)
    assert was_generated is False
    assert generated is None
    assert auth.is_valid("tt_live_existing")


def test_is_valid_rejects_unknown_token(tmp_path):
    auth, *_ = ServerAuth.load_or_generate(token_dir=tmp_path)
    assert auth.is_valid("totally-not-a-token") is False


# --------------------------------------------------------------------------- #
# Ingest endpoint enforces bearer when auth is wired                            #
# --------------------------------------------------------------------------- #


def _record_payload() -> str:
    return json.dumps({"client_record_id": "rid-auth", "ts_start": 1, "app_name": "x"})


async def test_ingest_returns_401_without_bearer(db, blob_storage, tmp_path):
    auth, *_ = ServerAuth.load_or_generate(token_dir=tmp_path / "tokens")
    app = create_app(db, blob_storage=blob_storage, auth=auth)
    with TestClient(app) as client:
        resp = client.post("/v1/ingest/record", data={"record": _record_payload()})
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_ingest_returns_401_with_wrong_bearer(db, blob_storage, tmp_path):
    auth, *_ = ServerAuth.load_or_generate(token_dir=tmp_path / "tokens")
    app = create_app(db, blob_storage=blob_storage, auth=auth)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest/record",
            data={"record": _record_payload()},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 401


async def test_ingest_succeeds_with_valid_bearer(db, blob_storage, tmp_path):
    auth, _, generated = ServerAuth.load_or_generate(token_dir=tmp_path / "tokens")
    app = create_app(db, blob_storage=blob_storage, auth=auth)
    assert generated is not None
    with TestClient(app) as client:
        resp = client.post(
            "/v1/ingest/record",
            data={"record": _record_payload()},
            headers={"Authorization": f"Bearer {generated.value}"},
        )
    assert resp.status_code == 200


async def test_close_endpoint_also_enforces_bearer(db, blob_storage, tmp_path):
    auth, *_ = ServerAuth.load_or_generate(token_dir=tmp_path / "tokens")
    app = create_app(db, blob_storage=blob_storage, auth=auth)
    with TestClient(app) as client:
        resp = client.post("/v1/ingest/record/some-id/close")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Without auth (default) ingest is open — backwards compatibility               #
# --------------------------------------------------------------------------- #


async def test_ingest_open_when_auth_is_none(db, blob_storage):
    app = create_app(db, blob_storage=blob_storage)
    with TestClient(app) as client:
        resp = client.post("/v1/ingest/record", data={"record": _record_payload()})
    assert resp.status_code == 200


async def test_healthz_never_requires_auth(db, blob_storage, tmp_path):
    auth, *_ = ServerAuth.load_or_generate(token_dir=tmp_path / "tokens")
    app = create_app(db, blob_storage=blob_storage, auth=auth)
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200


async def test_records_routes_unaffected_by_auth(db, blob_storage, tmp_path):
    """Frontend-facing read routes are loopback-only; auth gate is on ingest only."""
    auth, *_ = ServerAuth.load_or_generate(token_dir=tmp_path / "tokens")
    app = create_app(db, blob_storage=blob_storage, auth=auth)
    with TestClient(app) as client:
        resp = client.get("/v1/records")
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Multi-token scenarios                                                         #
# --------------------------------------------------------------------------- #


def test_multiple_tokens_all_validated():
    auth = ServerAuth(
        [
            TokenEntry(value="t-one", label="alpha"),
            TokenEntry(value="t-two", label="beta"),
        ]
    )
    assert auth.is_valid("t-one")
    assert auth.is_valid("t-two")
    assert not auth.is_valid("t-three")
