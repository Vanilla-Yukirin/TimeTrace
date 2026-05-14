"""Queue tier: analysis-task queue Protocol + concrete implementations.

The default in-memory implementation is used by tests and as a reference for
HttpBackend wiring at P3a. The production worker today still goes through
SqliteDatabase's `analysis_results` table directly (P2.5 leaves that intact);
when RedisQueue lands at P5 the worker rewires to Queue and the SQLite queue
role recedes to "fallback for single-process mode".
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict, deque
from typing import Any, Protocol


class Queue(Protocol):
    """Minimal claim/ack queue surface used by the analysis worker."""

    async def enqueue(self, kind: str, payload: dict[str, Any]) -> str:
        """Insert a task and return its server-assigned id."""
        ...

    async def claim(self, kind: str) -> tuple[str, dict[str, Any]] | None:
        """Atomically pull the next ready task in `kind`. Returns (task_id, payload)."""
        ...

    async def ack(self, task_id: str) -> None:
        """Mark a claimed task as completed; removes it from the in-flight set."""
        ...

    async def fail(self, task_id: str, *, requeue: bool = False) -> None:
        """Drop a claimed task; if `requeue` push it back to the front of its kind."""
        ...


class InMemoryQueue:
    """Single-process FIFO per kind. Never persists.

    Concurrent claim/ack is serialized through a single asyncio.Lock — fine
    for the tests and for in-process driving from HttpBackend during E2E setup.
    """

    def __init__(self) -> None:
        self._queues: dict[str, deque[tuple[str, dict[str, Any]]]] = defaultdict(deque)
        self._inflight: dict[str, tuple[str, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, kind: str, payload: dict[str, Any]) -> str:
        task_id = str(uuid.uuid4())
        async with self._lock:
            self._queues[kind].append((task_id, payload))
        return task_id

    async def claim(self, kind: str) -> tuple[str, dict[str, Any]] | None:
        async with self._lock:
            q = self._queues.get(kind)
            if not q:
                return None
            task_id, payload = q.popleft()
            self._inflight[task_id] = (kind, payload)
            return task_id, payload

    async def ack(self, task_id: str) -> None:
        async with self._lock:
            self._inflight.pop(task_id, None)

    async def fail(self, task_id: str, *, requeue: bool = False) -> None:
        async with self._lock:
            entry = self._inflight.pop(task_id, None)
            if requeue and entry is not None:
                kind, payload = entry
                # Re-add at the FRONT so this task is the next claim, not the last.
                self._queues[kind].appendleft((task_id, payload))

    # Helpers for tests + introspection ---------------------------------------

    async def pending(self, kind: str) -> int:
        async with self._lock:
            return len(self._queues.get(kind, ()))

    async def inflight_count(self) -> int:
        async with self._lock:
            return len(self._inflight)


__all__ = ["InMemoryQueue", "Queue"]
