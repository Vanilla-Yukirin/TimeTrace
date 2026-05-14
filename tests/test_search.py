"""Tests for /v1/search/by-image and /v1/apps endpoints."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.common.phash_hash import compute_phash, phash_to_blob
from timetrace.server.api.app import create_app
from timetrace.server.phash_index.index import PHashIndex
from timetrace.server.storage.database import Database


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


def _solid(color: tuple[int, int, int], size: int = 64) -> Image.Image:
    return Image.new("RGB", (size, size), color=color)


def _checker(cell: int = 8, size: int = 64) -> Image.Image:
    img = Image.new("RGB", (size, size), color=(0, 0, 0))
    pixels = img.load()
    for y in range(size):
        for x in range(size):
            if (x // cell + y // cell) % 2 == 0:
                pixels[x, y] = (255, 255, 255)
    return img


def _img_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _seed_record_with_screenshot(
    db: Database, app_name: str, title: str, phash_val: int | None
) -> tuple[str, str]:
    ctx = CaptureContext(app_name=app_name, process_name=app_name.lower(), window_title=title)
    record_id = await db.insert_record(ctx, reason="heartbeat")
    sid = await db.insert_screenshot(
        record_id=record_id,
        path=f"screenshots/{record_id}.png",
        thumb_path=f"thumbs/2026/04/22/{record_id}.jpg",
        width=64,
        height=64,
        hash_sha256="sha",
        phash=phash_to_blob(phash_val) if phash_val is not None else None,
    )
    return record_id, sid


# --------------------------------------------------------------------------- #
# /v1/apps                                                                      #
# --------------------------------------------------------------------------- #


async def test_apps_endpoint_returns_counts(db):
    await _seed_record_with_screenshot(db, "VSCode", "a.py", None)
    await _seed_record_with_screenshot(db, "VSCode", "b.py", None)
    await _seed_record_with_screenshot(db, "Chrome", "news", None)

    app = create_app(db)
    with TestClient(app) as client:
        resp = client.get("/v1/apps")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [(i["name"], i["count"]) for i in items] == [("VSCode", 2), ("Chrome", 1)]


# --------------------------------------------------------------------------- #
# /v1/records extended params                                                   #
# --------------------------------------------------------------------------- #


async def test_records_apps_multi_and_categories_filter(db):
    rid_a, _ = await _seed_record_with_screenshot(db, "VSCode", "a.py", None)
    rid_b, _ = await _seed_record_with_screenshot(db, "Chrome", "b", None)
    rid_c, _ = await _seed_record_with_screenshot(db, "Firefox", "c", None)

    # Mark categories
    await db.mark_pending(rid_a)
    await db.conn.execute(
        "UPDATE analysis_results SET category_final=? WHERE record_id=?",
        ("work/coding", rid_a),
    )
    await db.mark_pending(rid_b)
    await db.conn.execute(
        "UPDATE analysis_results SET category_final=? WHERE record_id=?",
        ("entertainment/other", rid_b),
    )
    await db.conn.commit()

    app = create_app(db)
    with TestClient(app) as client:
        resp = client.get("/v1/records?apps=VSCode,Chrome")
        ids = {r["id"] for r in resp.json()["items"]}
        assert ids == {rid_a, rid_b}

        resp2 = client.get("/v1/records?categories=work/coding")
        ids2 = {r["id"] for r in resp2.json()["items"]}
        assert ids2 == {rid_a}


# --------------------------------------------------------------------------- #
# /v1/search/by-image                                                           #
# --------------------------------------------------------------------------- #


async def test_search_by_image_requires_upload(db):
    app = create_app(db, phash_index=PHashIndex())
    with TestClient(app) as client:
        resp = client.post("/v1/search/by-image")
    assert resp.status_code == 422  # FastAPI: missing required File


async def test_search_by_image_visual_only(db):
    # Seed 3 screenshots with known phashes
    ph_target = compute_phash(_checker())
    ph_red = compute_phash(_solid((255, 0, 0)))
    ph_blue = compute_phash(_solid((0, 0, 255)))

    _, sid_target = await _seed_record_with_screenshot(db, "App", "target", ph_target)
    _, sid_red = await _seed_record_with_screenshot(db, "App", "red", ph_red)
    _, _ = await _seed_record_with_screenshot(db, "App", "blue", ph_blue)
    assert ph_red != ph_blue

    index = await PHashIndex.from_db(db)
    app = create_app(db, phash_index=index)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={"visual": "true", "semantic": "false", "radius": "64"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["visual_channel"] == "ok"
    assert body["semantic_channel"] == "disabled"
    # Top hit must be exact match (distance 0)
    assert body["items"][0]["screenshot_id"] == sid_target
    assert body["items"][0]["match"]["visual_distance"] == 0


async def test_search_by_image_semantic_unavailable(db):
    _, _ = await _seed_record_with_screenshot(db, "App", "x", compute_phash(_checker()))
    index = await PHashIndex.from_db(db)
    app = create_app(db, phash_index=index)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={"visual": "false", "semantic": "true"},
        )
    body = resp.json()
    assert body["visual_channel"] == "disabled"
    assert body["semantic_channel"] == "unavailable"
    assert body["items"] == []


async def test_search_by_image_both_channels_visual_fallback(db):
    """With VLM stubbed, selecting both should degrade to visual-only results."""
    ph = compute_phash(_checker())
    _, sid = await _seed_record_with_screenshot(db, "App", "x", ph)

    index = await PHashIndex.from_db(db)
    app = create_app(db, phash_index=index)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={"visual": "true", "semantic": "true"},
        )
    body = resp.json()
    assert body["visual_channel"] == "ok"
    assert body["semantic_channel"] == "unavailable"
    assert body["items"][0]["screenshot_id"] == sid


async def test_search_by_image_post_filter_apps(db):
    ph = compute_phash(_checker())
    _, sid_vscode = await _seed_record_with_screenshot(db, "VSCode", "a", ph)
    _, _ = await _seed_record_with_screenshot(db, "Chrome", "b", ph)

    index = await PHashIndex.from_db(db)
    app = create_app(db, phash_index=index)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={"visual": "true", "semantic": "false", "radius": "64", "apps": "VSCode"},
        )
    body = resp.json()
    sids = [it["screenshot_id"] for it in body["items"]]
    assert sids == [sid_vscode]


async def test_search_by_image_rejects_too_many_images(db):
    """Uploading more than 5 reference images must be rejected."""
    app = create_app(db, phash_index=PHashIndex())
    files = [
        ("images", (f"q{i}.png", _img_bytes(_solid((i * 10, 0, 0))), "image/png")) for i in range(6)
    ]
    with TestClient(app) as client:
        resp = client.post(
            "/v1/search/by-image",
            files=files,
            data={"visual": "true", "semantic": "false"},
        )
    assert resp.status_code == 400
    assert "max" in resp.json()["detail"].lower()


async def test_search_by_image_half_open_ts_range(db):
    """Giving only `start` (or only `end`) must still filter, not be silently ignored.

    pHash index filters at day-bucket granularity, so exclusion needs a bound
    at least one full day away from the inserted record's day-key.
    """
    ph = compute_phash(_checker())
    _, sid = await _seed_record_with_screenshot(db, "App", "x", ph)

    async with db.conn.execute(
        "SELECT ts_start FROM records WHERE id=(SELECT record_id FROM screenshots WHERE id=?)",
        (sid,),
    ) as cur:
        row = await cur.fetchone()
    ts = row["ts_start"]
    day_ms = 86_400_000

    index = await PHashIndex.from_db(db)
    app = create_app(db, phash_index=index)

    with TestClient(app) as client:
        # start one day in the future → excludes the row (different day bucket)
        resp_excl = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={
                "visual": "true",
                "semantic": "false",
                "radius": "64",
                "start": str(ts + 2 * day_ms),
            },
        )
        assert resp_excl.json()["items"] == []

        # start one day in the past, no `end` → must still include it (half-open window honoured)
        resp_incl = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={
                "visual": "true",
                "semantic": "false",
                "radius": "64",
                "start": str(ts - 2 * day_ms),
            },
        )
        assert len(resp_incl.json()["items"]) == 1


async def test_records_keyword_escapes_like_metachars(db):
    """Literal %/_ in keyword must not act as SQL wildcards."""
    _, _ = await _seed_record_with_screenshot(db, "App", "readme", None)
    _, _ = await _seed_record_with_screenshot(db, "App", "real_ab", None)  # would match %a%b% etc.

    app = create_app(db)
    with TestClient(app) as client:
        # Underscore should match literal '_' only → only "real_ab" hits
        resp = client.get("/v1/records?q=%5F")  # %5F = '_' in URL
        titles = {r["window_title"] for r in resp.json()["items"]}
        assert titles == {"real_ab"}


async def test_search_placeholder_endpoint_removed(db):
    """The old JSON /v1/search endpoint is gone (replaced by /v1/search/by-image)."""
    app = create_app(db)
    with TestClient(app) as client:
        resp = client.post("/v1/search", json={"query_text": "hello"})
    assert resp.status_code in (404, 405)


class _StubVLMClient:
    """Returns a fixed dict from describe(); mimics the surface used by the route."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def describe(self, image, window_title=None):  # noqa: ANN001
        return self.payload


async def test_search_by_image_semantic_with_stub_vlm(db):
    """When VLM returns a real dict and DB has matching vlm_desc, semantic channel hits."""
    ph = compute_phash(_checker())
    rid, _ = await _seed_record_with_screenshot(db, "App", "vscode-window", ph)

    # Manually populate vlm_desc with text that overlaps the stub VLM output
    await db.mark_pending(rid)
    await db.conn.execute(
        "UPDATE analysis_results SET vlm_desc=? WHERE record_id=?",
        ("用户正在 VSCode 编辑代码\n\n摘要：调试\n关键词：VSCode、调试", rid),
    )
    await db.conn.commit()

    index = await PHashIndex.from_db(db)
    app = create_app(db, phash_index=index)
    app.state.vlm_client = _StubVLMClient(
        {"keywords": ["VSCode", "调试"], "summary": "调试代码", "description": "在 VSCode 调试代码"}
    )

    with TestClient(app) as client:
        resp = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={"visual": "false", "semantic": "true"},
        )
    body = resp.json()
    assert body["semantic_channel"] == "ok"
    assert len(body["items"]) >= 1


async def test_search_by_image_thumb_path_stripped(db):
    ph = compute_phash(_checker())
    _, _ = await _seed_record_with_screenshot(db, "App", "x", ph)

    index = await PHashIndex.from_db(db)
    app = create_app(db, phash_index=index)

    with TestClient(app) as client:
        resp = client.post(
            "/v1/search/by-image",
            files={"images": ("q.png", _img_bytes(_checker()), "image/png")},
            data={"visual": "true", "semantic": "false", "radius": "64"},
        )
    thumb = resp.json()["items"][0]["thumb_path"]
    # Must not carry the "thumbs/" prefix any more (stripped for /thumbs/ mount)
    assert not thumb.startswith("thumbs/")
    assert thumb.endswith(".jpg")
