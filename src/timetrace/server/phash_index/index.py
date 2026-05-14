"""Day-bucketed pHash index with time-range filtered Hamming nearest-neighbour search."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from timetrace.common.phash_hash import phash_from_blob
from timetrace.server.phash_index.bk_tree import BKTree

if TYPE_CHECKING:
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

_MS_PER_DAY = 86_400_000


def _day_key(ts_ms: int) -> int:
    return ts_ms // _MS_PER_DAY


class PHashIndex:
    """One BK-tree per UTC-day bucket. Queries with `ts_range` visit only the
    overlapping buckets; queries without `ts_range` visit all buckets.
    """

    def __init__(self) -> None:
        self._buckets: dict[int, BKTree] = {}
        self._size = 0

    def __len__(self) -> int:
        return self._size

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)

    def insert(self, value: str, phash: int, ts_ms: int) -> None:
        key = _day_key(ts_ms)
        tree = self._buckets.get(key)
        if tree is None:
            tree = BKTree()
            self._buckets[key] = tree
        tree.insert(phash, value)
        self._size += 1

    def search(
        self,
        phash: int,
        radius: int,
        ts_range: tuple[int, int] | None = None,
        k: int | None = None,
    ) -> list[tuple[int, str]]:
        """Return (distance, value) pairs sorted by distance ascending.

        - `radius`: maximum Hamming distance (inclusive)
        - `ts_range`: optional (start_ms, end_ms) inclusive; restricts search to
          day-buckets overlapping this range
        - `k`: optional limit on number of results
        """
        if ts_range is not None:
            # Iterate existing buckets and filter by day-key; O(bucket_count).
            # Avoids iterating a giant integer range when the caller passes a
            # wide open-ended upper bound (e.g. 2**62 for "no upper limit").
            start_ms, end_ms = ts_range
            start_key = _day_key(start_ms)
            end_key = _day_key(end_ms)
            trees = (self._buckets[k] for k in self._buckets if start_key <= k <= end_key)
        else:
            trees = iter(self._buckets.values())

        results: list[tuple[int, str]] = []
        for tree in trees:
            results.extend(tree.range_search(phash, radius))

        results.sort(key=lambda pair: pair[0])
        if k is not None:
            results = results[:k]
        return results

    @classmethod
    async def from_db(cls, db: Database) -> PHashIndex:
        """Rebuild the index from SQLite (the source of truth)."""
        index = cls()
        async with db.lock:
            async with db.conn.execute(
                """SELECT s.id AS sid, s.phash AS phash, r.ts_start AS ts
                   FROM screenshots s
                   JOIN records r ON r.id = s.record_id
                   WHERE s.phash IS NOT NULL AND s.deleted_at IS NULL
                   ORDER BY r.ts_start ASC, s.id ASC"""
            ) as cur:
                rows = await cur.fetchall()

        for row in rows:
            index.insert(row["sid"], phash_from_blob(row["phash"]), row["ts"])

        logger.info("phash_index.loaded", count=len(index), buckets=index.bucket_count)
        return index
