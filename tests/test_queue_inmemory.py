"""Tests for InMemoryQueue (the default Queue Protocol implementation)."""

from __future__ import annotations

import asyncio

import pytest

from timetrace.server.queue import InMemoryQueue


@pytest.fixture
def queue() -> InMemoryQueue:
    return InMemoryQueue()


async def test_enqueue_then_claim_returns_payload(queue):
    task_id = await queue.enqueue("vlm", {"record_id": "r1"})
    claimed = await queue.claim("vlm")
    assert claimed is not None
    cid, payload = claimed
    assert cid == task_id
    assert payload == {"record_id": "r1"}


async def test_claim_returns_none_when_kind_empty(queue):
    assert await queue.claim("vlm") is None


async def test_claim_isolates_kinds(queue):
    await queue.enqueue("vlm", {"x": 1})
    assert await queue.claim("ocr") is None  # different kind, queue empty
    claimed = await queue.claim("vlm")
    assert claimed is not None and claimed[1] == {"x": 1}


async def test_claims_are_fifo_within_kind(queue):
    ids = [await queue.enqueue("vlm", {"i": i}) for i in range(3)]
    claimed = [await queue.claim("vlm") for _ in range(3)]
    assert [c[0] for c in claimed] == ids


async def test_ack_removes_from_inflight(queue):
    tid = await queue.enqueue("vlm", {})
    await queue.claim("vlm")
    assert await queue.inflight_count() == 1
    await queue.ack(tid)
    assert await queue.inflight_count() == 0


async def test_fail_without_requeue_drops_task(queue):
    tid = await queue.enqueue("vlm", {"x": 1})
    await queue.claim("vlm")
    await queue.fail(tid)
    assert await queue.claim("vlm") is None
    assert await queue.inflight_count() == 0


async def test_fail_with_requeue_puts_task_at_front(queue):
    tid_a = await queue.enqueue("vlm", {"x": "a"})
    tid_b = await queue.enqueue("vlm", {"x": "b"})

    await queue.claim("vlm")  # claims A
    await queue.fail(tid_a, requeue=True)

    # A must be claimed again before B
    next_claim = await queue.claim("vlm")
    assert next_claim is not None and next_claim[0] == tid_a
    after = await queue.claim("vlm")
    assert after is not None and after[0] == tid_b


async def test_concurrent_claims_each_get_one_task(queue):
    """Five concurrent claim() on a single task → one wins, four get None."""
    await queue.enqueue("vlm", {"only": True})
    results = await asyncio.gather(*(queue.claim("vlm") for _ in range(5)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


async def test_pending_reflects_queue_depth(queue):
    await queue.enqueue("vlm", {})
    await queue.enqueue("vlm", {})
    assert await queue.pending("vlm") == 2
    await queue.claim("vlm")
    assert await queue.pending("vlm") == 1
