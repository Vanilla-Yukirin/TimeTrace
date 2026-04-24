"""Unit tests for pHash computation, BK-tree, and day-bucketed index."""

from __future__ import annotations

import random

import pytest
from PIL import Image

from timetrace.config import StorageConfig
from timetrace.phash_index.bk_tree import BKTree
from timetrace.phash_index.hash import (
    compute_phash,
    hamming,
    phash_from_blob,
    phash_to_blob,
)
from timetrace.phash_index.index import PHashIndex
from timetrace.storage.database import Database
from timetrace.storage.models import CaptureContext

# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def db(tmp_path):
    cfg = StorageConfig(data_dir=tmp_path)
    database = Database(cfg)
    await database.init()
    yield database
    await database.close()


def _checkerboard(size: int = 64, cell: int = 8, shift: int = 0) -> Image.Image:
    """A deterministic 'real-looking' image with structure (not a flat colour)."""
    img = Image.new("RGB", (size, size), color=(0, 0, 0))
    pixels = img.load()
    for y in range(size):
        for x in range(size):
            if ((x + shift) // cell + y // cell) % 2 == 0:
                pixels[x, y] = (255, 255, 255)
    return img


# --------------------------------------------------------------------------- #
# pHash function                                                                #
# --------------------------------------------------------------------------- #


def test_compute_phash_deterministic():
    img = _checkerboard()
    assert compute_phash(img) == compute_phash(img)


def test_compute_phash_distinguishes_different_images():
    red = Image.new("RGB", (64, 64), color=(255, 0, 0))
    # Use a structured image to avoid degenerate all-equal DCT low-band
    striped = _checkerboard()
    h1 = compute_phash(red)
    h2 = compute_phash(striped)
    assert hamming(h1, h2) > 20


def test_phash_blob_roundtrip():
    for value in (0, 1, 0xDEADBEEFCAFEBABE, (1 << 63), (1 << 64) - 1):
        assert phash_from_blob(phash_to_blob(value)) == value


def test_phash_fits_64_bits():
    h = compute_phash(_checkerboard())
    assert 0 <= h < (1 << 64)


# --------------------------------------------------------------------------- #
# BK-tree                                                                       #
# --------------------------------------------------------------------------- #


def test_bk_tree_empty_returns_empty():
    tree = BKTree()
    assert tree.range_search(0, 64) == []
    assert len(tree) == 0


def test_bk_tree_insert_and_exact_match():
    tree = BKTree()
    tree.insert(0xABCDEF0123456789, "a")
    results = tree.range_search(0xABCDEF0123456789, 0)
    assert results == [(0, "a")]
    assert len(tree) == 1


def test_bk_tree_range_search_matches_brute_force():
    rng = random.Random(42)
    values = [(rng.getrandbits(64), f"v{i}") for i in range(50)]
    tree = BKTree()
    for phash, val in values:
        tree.insert(phash, val)
    assert len(tree) == 50

    for _ in range(10):
        q = rng.getrandbits(64)
        r = rng.randint(0, 64)
        brute = {val for phash, val in values if hamming(phash, q) <= r}
        got = {val for _, val in tree.range_search(q, r)}
        assert got == brute, f"mismatch at q={q:016x} r={r}"


# --------------------------------------------------------------------------- #
# PHashIndex                                                                    #
# --------------------------------------------------------------------------- #


_DAY_MS = 86_400_000


def test_phash_index_no_time_filter():
    idx = PHashIndex()
    # Three different days, two frames each
    for day in (10, 11, 12):
        base_ts = day * _DAY_MS
        idx.insert(f"{day}-a", 0xF0F0F0F0F0F0F0F0, base_ts)
        idx.insert(f"{day}-b", 0x0F0F0F0F0F0F0F0F, base_ts + 1000)

    hits = idx.search(0xF0F0F0F0F0F0F0F0, radius=0)
    assert {v for _, v in hits} == {"10-a", "11-a", "12-a"}
    assert len(idx) == 6


def test_phash_index_time_range_filters_buckets():
    idx = PHashIndex()
    for day in (10, 11, 12):
        idx.insert(f"{day}", 0xAAAAAAAAAAAAAAAA, day * _DAY_MS + 5000)

    hits = idx.search(
        0xAAAAAAAAAAAAAAAA,
        radius=0,
        ts_range=(11 * _DAY_MS, 11 * _DAY_MS + _DAY_MS - 1),
    )
    assert [v for _, v in hits] == ["11"]


def test_phash_index_k_limit_and_sorted_by_distance():
    idx = PHashIndex()
    base_ts = 10 * _DAY_MS
    target = 0
    # Insert hashes with known Hamming distances: 0, 1, 3, 7
    idx.insert("d0", 0b0, base_ts)
    idx.insert("d1", 0b1, base_ts)
    idx.insert("d3", 0b111, base_ts)
    idx.insert("d7", 0b1111111, base_ts)

    hits = idx.search(target, radius=64, k=2)
    assert [v for _, v in hits] == ["d0", "d1"]
    # Distances must be non-decreasing
    dists = [d for d, _ in hits]
    assert dists == sorted(dists)


def test_phash_index_empty_returns_empty():
    idx = PHashIndex()
    assert idx.search(0, radius=8) == []
    assert idx.search(0, radius=8, ts_range=(0, _DAY_MS)) == []


async def test_phash_index_from_db_roundtrip(db):
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Win")
    record_id = await db.insert_record(ctx, reason="heartbeat")

    phash_a = 0xDEADBEEFCAFEBABE
    phash_b = 0x1111222233334444
    await db.insert_screenshot(
        record_id=record_id,
        path="s/a.png",
        thumb_path="t/a.jpg",
        width=1,
        height=1,
        hash_sha256="sa",
        phash=phash_to_blob(phash_a),
    )
    await db.insert_screenshot(
        record_id=record_id,
        path="s/b.png",
        thumb_path="t/b.jpg",
        width=1,
        height=1,
        hash_sha256="sb",
        phash=phash_to_blob(phash_b),
    )

    idx = await PHashIndex.from_db(db)
    assert len(idx) == 2

    hits = idx.search(phash_a, radius=0)
    assert len(hits) == 1 and hits[0][0] == 0


async def test_phash_index_from_db_skips_deleted(db):
    """Deleted screenshots should not be loaded into the index."""
    ctx = CaptureContext(app_name="App", process_name="app", window_title="Win")
    record_id = await db.insert_record(ctx, reason="heartbeat")

    sid = await db.insert_screenshot(
        record_id=record_id,
        path="s/a.png",
        thumb_path=None,
        width=1,
        height=1,
        hash_sha256="sa",
        phash=phash_to_blob(0xF00D),
    )
    await db.conn.execute("UPDATE screenshots SET deleted_at=? WHERE id=?", (1, sid))
    await db.conn.commit()

    idx = await PHashIndex.from_db(db)
    assert len(idx) == 0


# --------------------------------------------------------------------------- #
# Schema migration                                                              #
# --------------------------------------------------------------------------- #


async def test_database_phash_column_migration(tmp_path):
    """Simulate an old DB with a screenshots table missing the phash column."""
    import aiosqlite

    cfg = StorageConfig(data_dir=tmp_path)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)

    # Manually create a pre-migration schema with no phash column.
    async with aiosqlite.connect(cfg.db_path) as conn:
        await conn.execute(
            """CREATE TABLE screenshots (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL,
                path TEXT NOT NULL,
                thumb_path TEXT,
                width INTEGER,
                height INTEGER,
                hash_sha256 TEXT,
                deleted_at INTEGER,
                privacy_level TEXT NOT NULL DEFAULT 'normal',
                created_at INTEGER NOT NULL
            )"""
        )
        await conn.commit()

    # Open through Database — migration must add the phash column.
    database = Database(cfg)
    await database.init()
    try:
        async with database.conn.execute("PRAGMA table_info(screenshots)") as cur:
            cols = {row["name"] for row in await cur.fetchall()}
        assert "phash" in cols
    finally:
        await database.close()
