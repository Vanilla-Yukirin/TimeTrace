"""Minimal BK-tree for Hamming-metric range search over 64-bit pHashes."""

from __future__ import annotations

from typing import Any

from timetrace.common.phash_hash import hamming


class _BKNode:
    __slots__ = ("phash", "value", "children")

    def __init__(self, phash: int, value: Any) -> None:
        self.phash = phash
        self.value = value
        self.children: dict[int, _BKNode] = {}


class BKTree:
    """BK-tree over 64-bit integers with Hamming distance.

    Insertions are O(depth) distance computations; range_search prunes children
    by the triangle inequality: only edges in [d(q, node) - r, d(q, node) + r]
    need to be explored.
    """

    def __init__(self) -> None:
        self._root: _BKNode | None = None
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def insert(self, phash: int, value: Any) -> None:
        node = _BKNode(phash, value)
        if self._root is None:
            self._root = node
            self._size = 1
            return

        cur = self._root
        while True:
            d = hamming(phash, cur.phash)
            child = cur.children.get(d)
            if child is None:
                cur.children[d] = node
                self._size += 1
                return
            cur = child

    def range_search(self, phash: int, radius: int) -> list[tuple[int, Any]]:
        """Return all (distance, value) pairs within Hamming `radius` of `phash`."""
        if self._root is None:
            return []

        results: list[tuple[int, Any]] = []
        stack: list[_BKNode] = [self._root]
        while stack:
            node = stack.pop()
            d = hamming(phash, node.phash)
            if d <= radius:
                results.append((d, node.value))
            lo = d - radius
            hi = d + radius
            for edge, child in node.children.items():
                if lo <= edge <= hi:
                    stack.append(child)
        return results
