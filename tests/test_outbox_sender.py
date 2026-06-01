"""Tests for OutboxSender — the drain-with-backoff background loop."""

from __future__ import annotations

import asyncio

import pytest

from timetrace.client.core.outbox import Outbox, OutboxEntry
from timetrace.client.core.outbox_sender import OutboxSender


@pytest.fixture
def outbox(tmp_path) -> Outbox:
    return Outbox(tmp_path / "outbox")


class _StubSender:
    """Records every send + can be programmed to fail N times before succeeding."""

    def __init__(self, fail_first_n: int = 0) -> None:
        self._fail_remaining = fail_first_n
        self.calls: list[OutboxEntry] = []

    async def __call__(self, entry: OutboxEntry) -> None:
        self.calls.append(entry)
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise RuntimeError("transient")


# --------------------------------------------------------------------------- #
# Happy path                                                                    #
# --------------------------------------------------------------------------- #


async def test_sender_drains_in_submission_order(outbox):
    eid_a = await outbox.append({"i": 0})
    eid_b = await outbox.append({"i": 1})
    eid_c = await outbox.append({"i": 2})

    stub = _StubSender()
    sender = OutboxSender(outbox, stub, idle_poll_interval_s=0.05)
    stop = asyncio.Event()

    task = asyncio.create_task(sender.run(stop))
    # Wait for queue to empty
    while await outbox.pending_count() > 0:
        await asyncio.sleep(0.01)
    stop.set()
    await task

    assert [e.entry_id for e in stub.calls] == [eid_a, eid_b, eid_c]
    assert await outbox.pending_count() == 0


async def test_sender_drops_oversize_image_entry(outbox):
    """An entry whose image blob exceeds the cap is dropped (acked without
    sending) so it can't wedge the strict-FIFO queue; the entry behind it
    still sends."""
    from timetrace.client.core.outbox_sender import _MAX_IMAGE_BYTES

    await outbox.append({"i": 0}, image_bytes=b"X" * (_MAX_IMAGE_BYTES + 1))  # oversize → drop
    eid_ok = await outbox.append({"i": 1}, image_bytes=b"X" * 1024)  # normal → send

    stub = _StubSender()
    sender = OutboxSender(outbox, stub, idle_poll_interval_s=0.05)
    stop = asyncio.Event()

    task = asyncio.create_task(sender.run(stop))
    while await outbox.pending_count() > 0:
        await asyncio.sleep(0.01)
    stop.set()
    await task

    # The oversize entry never reached the sender; only the small one did.
    assert [e.entry_id for e in stub.calls] == [eid_ok]
    assert await outbox.pending_count() == 0


async def test_sender_picks_up_entries_appended_after_start(outbox):
    """Sender's idle wait must let new appends drain the next iteration."""
    stub = _StubSender()
    sender = OutboxSender(outbox, stub, idle_poll_interval_s=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))

    await asyncio.sleep(0.1)  # let it spin in idle once
    eid = await outbox.append({"late": True})

    # Wait for the new entry to be drained
    for _ in range(50):
        if await outbox.pending_count() == 0:
            break
        await asyncio.sleep(0.02)

    stop.set()
    await task
    assert any(e.entry_id == eid for e in stub.calls)


# --------------------------------------------------------------------------- #
# Backoff / retry                                                               #
# --------------------------------------------------------------------------- #


async def test_sender_retries_failing_entry_until_success(outbox):
    eid = await outbox.append({"x": 1})

    stub = _StubSender(fail_first_n=3)
    sender = OutboxSender(
        outbox,
        stub,
        backoff_initial_s=0.01,
        backoff_max_s=0.05,
        idle_poll_interval_s=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))

    while await outbox.pending_count() > 0:
        await asyncio.sleep(0.02)
    stop.set()
    await task

    # 3 failures + 1 success = 4 calls, all on the same entry
    assert len(stub.calls) == 4
    assert all(c.entry_id == eid for c in stub.calls)


async def test_sender_strict_fifo_under_failure(outbox):
    """A failing head entry must NOT cause the sender to skip ahead to entry 2."""
    eid_head = await outbox.append({"i": 0})
    eid_next = await outbox.append({"i": 1})

    stub = _StubSender(fail_first_n=2)
    sender = OutboxSender(
        outbox,
        stub,
        backoff_initial_s=0.01,
        backoff_max_s=0.02,
        idle_poll_interval_s=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))

    while await outbox.pending_count() > 0:
        await asyncio.sleep(0.02)
    stop.set()
    await task

    # First three calls all on eid_head (2 failures + 1 success); only then eid_next
    head_calls = [c for c in stub.calls if c.entry_id == eid_head]
    next_calls = [c for c in stub.calls if c.entry_id == eid_next]
    assert len(head_calls) == 3
    assert len(next_calls) == 1
    # Order check: head's last call must come before next's only call
    assert stub.calls.index(head_calls[-1]) < stub.calls.index(next_calls[0])


# --------------------------------------------------------------------------- #
# Stop semantics                                                                #
# --------------------------------------------------------------------------- #


async def test_stop_event_during_backoff_returns_promptly(outbox):
    """Setting stop_event while sender is sleeping in backoff must wake it
    within roughly the backoff window, not multiple seconds later."""
    await outbox.append({"x": 1})

    class _AlwaysFail:
        async def __call__(self, entry):  # noqa: ANN001
            raise RuntimeError("never")

    sender = OutboxSender(
        outbox,
        _AlwaysFail(),
        backoff_initial_s=10.0,  # long enough that without wake we'd hang
        backoff_max_s=10.0,
        idle_poll_interval_s=10.0,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))

    await asyncio.sleep(0.05)  # let it enter backoff
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)  # must finish well under backoff


async def test_max_kbps_zero_does_not_throttle(outbox):
    """Default `max_kbps=0` must impose zero overhead — pure pass-through."""
    for _ in range(5):
        await outbox.append({"x": 1}, image_bytes=b"X" * 10240)

    stub = _StubSender()
    sender = OutboxSender(outbox, stub, idle_poll_interval_s=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))
    while await outbox.pending_count() > 0:
        await asyncio.sleep(0.01)
    stop.set()
    await task
    assert len(stub.calls) == 5


async def test_max_kbps_caps_throughput(outbox, monkeypatch):
    """With max_kbps=10 and 30KB of pending data, sender should sleep ~3s.

    We swap asyncio.sleep with a fake that records waits without actually
    sleeping, then assert the recorded total exceeds the cap-derived floor.
    """
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)  # yield once so the loop can progress

    monkeypatch.setattr("timetrace.client.core.outbox_sender.asyncio.sleep", _fake_sleep)

    # 3 entries of 10KB each = 30KB total
    await outbox.append({"i": 0}, image_bytes=b"X" * 10240)
    await outbox.append({"i": 1}, image_bytes=b"X" * 10240)
    await outbox.append({"i": 2}, image_bytes=b"X" * 10240)

    stub = _StubSender()
    sender = OutboxSender(outbox, stub, max_kbps=10, idle_poll_interval_s=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))
    while await outbox.pending_count() > 0:
        await real_sleep(0.01)
    stop.set()
    await task

    # 30KB at 10KB/s = 3s of cap-imposed wait; we burn the initial 1-second
    # bucket "for free" so the recorded wait must exceed (30 - 10) / 10 = 2s.
    assert sum(sleeps) >= 2.0


async def test_pending_entry_stays_pending_when_stop_fires_before_success(outbox):
    eid = await outbox.append({"x": 1})

    class _AlwaysFail:
        async def __call__(self, entry):  # noqa: ANN001
            raise RuntimeError("never")

    sender = OutboxSender(
        outbox,
        _AlwaysFail(),
        backoff_initial_s=0.05,
        backoff_max_s=0.05,
        idle_poll_interval_s=0.05,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))

    await asyncio.sleep(0.1)
    stop.set()
    await task

    assert await outbox.pending_count() == 1
    # And the entry id is still the one we put in
    pending = [e async for e in outbox.iter_pending()]
    assert pending[0].entry_id == eid


# --------------------------------------------------------------------------- #
# Periodic compaction                                                           #
# --------------------------------------------------------------------------- #


async def test_compact_every_n_acks_triggers_after_threshold(outbox):
    for i in range(5):
        await outbox.append({"i": i})

    sender = OutboxSender(
        outbox,
        _StubSender(),
        idle_poll_interval_s=0.01,
        compact_every_n_acks=3,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))

    # Let it drain all 5
    for _ in range(50):
        if await outbox.pending_count() == 0:
            break
        await asyncio.sleep(0.01)

    stop.set()
    await task

    # After 5 acks with threshold 3: compaction triggered at the 3rd ack,
    # reclaiming entries 0..2. Subsequent acks (4, 5) accumulate in counter
    # but don't fire again until counter reaches 3 → so log should now hold
    # 0 entries (everything sent + at least the first 3 reclaimed).
    # The strong invariant we can assert: state.acked is < 3 after compaction.
    log_path = outbox._log_path  # noqa: SLF001
    if log_path.exists():
        line_count = sum(1 for _ in log_path.read_text().splitlines() if _.strip())
        # At most 4 entries left in log (compaction triggered at 3rd ack).
        assert line_count <= 4


async def test_compact_disabled_when_zero(outbox, tmp_path):
    for i in range(3):
        await outbox.append({"i": i}, image_bytes=b"x" * 100)

    sender = OutboxSender(
        outbox,
        _StubSender(),
        idle_poll_interval_s=0.01,
        compact_every_n_acks=0,  # disabled
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))

    for _ in range(50):
        if await outbox.pending_count() == 0:
            break
        await asyncio.sleep(0.01)

    stop.set()
    await task

    # All blobs still present (no compaction means no GC).
    blobs_dir = tmp_path / "outbox" / "blobs"
    blob_count = len(list(blobs_dir.iterdir())) if blobs_dir.exists() else 0
    assert blob_count == 3
