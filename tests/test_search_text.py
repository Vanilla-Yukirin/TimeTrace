"""Tests for GET /v1/search/text — keyword (FTS5/LIKE) + semantic (vector) RRF.

The semantic channel is exercised with a fake embedder injected onto
``app.state.embedding_client``: app.state is a plain mutable namespace, so the
endpoint reads whatever we set there regardless of ``create_app``'s signature.
That keeps these tests independent of the (separate) wiring that exposes the
real embedding client in production.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.api.app import create_app
from timetrace.server.db import Database


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


def _vec(*xs: float) -> bytes:
    return np.asarray(xs, dtype=np.float32).tobytes()


class _FakeEmbedder:
    """Returns a fixed vector for any query so the test controls which seeded
    record the query is 'semantically' closest to."""

    model = "fake-embed"
    dim = 4

    def __init__(self, query_vec: bytes) -> None:
        self._vec = query_vec

    async def embed(self, text: str) -> bytes:
        if not text:
            from timetrace.server.embedding.client import EmbeddingError

            raise EmbeddingError("empty")
        return self._vec

    async def aclose(self) -> None:
        pass


async def _seed(
    db: Database,
    app_name: str,
    title: str,
    desc: str,
    embedding: bytes | None,
    category: str | None = None,
) -> str:
    ctx = CaptureContext(app_name=app_name, process_name=app_name.lower(), window_title=title)
    rid = await db.insert_record(ctx, reason="heartbeat")
    await db.insert_screenshot(
        record_id=rid,
        path=f"screenshots/{rid}.png",
        thumb_path=f"thumbs/2026/06/05/{rid}.jpg",
        width=64,
        height=64,
        hash_sha256=f"sha-{rid}",
        phash=None,
    )
    await db.mark_pending(rid)
    await db.save_description(rid, desc)
    if embedding is not None:
        await db.save_text_embedding(rid, embedding, "fake-embed")
    if category is not None:
        await db.set_category_final(rid, category)
    return rid


async def test_text_search_fuses_keyword_and_semantic(db):
    # rid_a: keyword match on "哥莱姆", embedding orthogonal to the query.
    # rid_b: NO keyword overlap, embedding == query vector (semantic hit).
    # rid_c: neither keyword nor an embedding at all → must not surface.
    rid_a = await _seed(db, "Wuthering", "游戏", "鸣潮游戏哥莱姆战斗场景", _vec(0, 1, 0, 0))
    rid_b = await _seed(db, "Edge", "攻略", "二次元角色养成抽卡", _vec(1, 0, 0, 0))
    rid_c = await _seed(db, "Terminal", "shell", "命令行编译报错", None)

    app = create_app(db)
    app.state.embedding_client = _FakeEmbedder(_vec(1, 0, 0, 0))  # closest to rid_b
    with TestClient(app) as client:
        resp = client.get("/v1/search/text", params={"q": "哥莱姆"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["semantic_channel"] == "ok"
    ids = [it["record_id"] for it in body["items"]]
    assert rid_a in ids  # keyword channel
    assert rid_b in ids  # semantic channel (no keyword overlap)
    assert rid_c not in ids  # in neither channel
    # rid_a wins on RRF: it scores in BOTH channels.
    assert ids[0] == rid_a
    # match info is surfaced per item
    a_item = next(it for it in body["items"] if it["record_id"] == rid_a)
    assert a_item["match"]["keyword_rank"] == 1
    assert a_item["match"]["semantic_rank"] is not None


async def test_text_search_degrades_to_keyword_without_embedder(db):
    rid_a = await _seed(db, "Wuthering", "游戏", "鸣潮游戏哥莱姆", _vec(0, 1, 0, 0))
    await _seed(db, "Edge", "攻略", "二次元抽卡", _vec(1, 0, 0, 0))  # only semantic could match

    app = create_app(db)  # no embedding_client on app.state
    with TestClient(app) as client:
        resp = client.get("/v1/search/text", params={"q": "哥莱姆"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["semantic_channel"] == "unavailable"
    ids = [it["record_id"] for it in body["items"]]
    assert ids == [rid_a]  # keyword-only → just the literal match


async def test_text_search_embedder_failure_falls_back_to_keyword(db):
    rid_a = await _seed(db, "Wuthering", "游戏", "鸣潮游戏哥莱姆", _vec(0, 1, 0, 0))

    class _BoomEmbedder:
        model = "boom"
        dim = 4

        async def embed(self, text: str) -> bytes:
            from timetrace.server.embedding.client import EmbeddingError

            raise EmbeddingError("endpoint down")

    app = create_app(db)
    app.state.embedding_client = _BoomEmbedder()
    with TestClient(app) as client:
        resp = client.get("/v1/search/text", params={"q": "哥莱姆"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["semantic_channel"] == "unavailable"  # embed failed → degraded
    assert [it["record_id"] for it in body["items"]] == [rid_a]


async def test_text_search_category_filter(db):
    rid_a = await _seed(
        db, "Wuthering", "游戏", "鸣潮哥莱姆", _vec(0, 1, 0, 0), category="entertainment"
    )
    await _seed(db, "EdgeWiki", "wiki", "哥莱姆攻略资料", _vec(1, 0, 0, 0), category="work")

    app = create_app(db)
    with TestClient(app) as client:
        resp = client.get("/v1/search/text", params={"q": "哥莱姆", "categories": "entertainment"})

    assert resp.status_code == 200
    ids = [it["record_id"] for it in resp.json()["items"]]
    assert ids == [rid_a]  # both match keyword; only entertainment kept


async def test_text_search_empty_q_returns_400(db):
    app = create_app(db)
    with TestClient(app) as client:
        resp = client.get("/v1/search/text", params={"q": "   "})
    assert resp.status_code == 400
