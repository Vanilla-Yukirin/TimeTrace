"""Tests for the client-side append-only outbox."""

from __future__ import annotations

import os

import pytest

from timetrace.client.core.outbox import Outbox, OutboxError


@pytest.fixture
def outbox(tmp_path) -> Outbox:
    return Outbox(tmp_path / "outbox")


_PAYLOAD_A = {"client_record_id": "ra", "ts_start": 1, "app_name": "VSCode"}
_PAYLOAD_B = {"client_record_id": "rb", "ts_start": 2, "app_name": "Chrome"}


# --------------------------------------------------------------------------- #
# Append + pending_count                                                        #
# --------------------------------------------------------------------------- #


async def test_append_increments_pending_count(outbox):
    assert await outbox.pending_count() == 0
    await outbox.append(_PAYLOAD_A)
    assert await outbox.pending_count() == 1
    await outbox.append(_PAYLOAD_B)
    assert await outbox.pending_count() == 2


async def test_append_returns_unique_entry_ids(outbox):
    a = await outbox.append(_PAYLOAD_A)
    b = await outbox.append(_PAYLOAD_B)
    assert a != b


async def test_append_writes_blobs_when_provided(outbox, tmp_path):
    eid = await outbox.append(_PAYLOAD_A, image_bytes=b"IMG", thumb_bytes=b"TH")
    blobs = tmp_path / "outbox" / "blobs"
    assert (blobs / f"{eid}-image").read_bytes() == b"IMG"
    assert (blobs / f"{eid}-thumb").read_bytes() == b"TH"


# --------------------------------------------------------------------------- #
# iter_pending                                                                  #
# --------------------------------------------------------------------------- #


async def test_iter_pending_yields_in_submission_order(outbox):
    eid_a = await outbox.append(_PAYLOAD_A)
    eid_b = await outbox.append(_PAYLOAD_B)
    seen = [e async for e in outbox.iter_pending()]
    assert [e.entry_id for e in seen] == [eid_a, eid_b]
    assert seen[0].payload == _PAYLOAD_A
    assert seen[1].payload == _PAYLOAD_B


async def test_iter_pending_returns_blob_bytes(outbox):
    await outbox.append(_PAYLOAD_A, image_bytes=b"IM", thumb_bytes=b"TB")
    entries = [e async for e in outbox.iter_pending()]
    assert entries[0].image_bytes == b"IM"
    assert entries[0].thumb_bytes == b"TB"


async def test_iter_pending_skips_acked_entries(outbox):
    eid_a = await outbox.append(_PAYLOAD_A)
    await outbox.append(_PAYLOAD_B)
    await outbox.ack_next(eid_a)
    entries = [e async for e in outbox.iter_pending()]
    assert len(entries) == 1
    assert entries[0].payload == _PAYLOAD_B


# --------------------------------------------------------------------------- #
# ack_next semantics                                                            #
# --------------------------------------------------------------------------- #


async def test_ack_next_advances_cursor(outbox):
    eid_a = await outbox.append(_PAYLOAD_A)
    await outbox.append(_PAYLOAD_B)
    assert await outbox.pending_count() == 2
    await outbox.ack_next(eid_a)
    assert await outbox.pending_count() == 1


async def test_ack_next_rejects_out_of_order_id(outbox):
    await outbox.append(_PAYLOAD_A)
    eid_b = await outbox.append(_PAYLOAD_B)
    with pytest.raises(OutboxError, match="out of order"):
        await outbox.ack_next(eid_b)  # B is not the head


async def test_ack_next_on_empty_raises(outbox):
    with pytest.raises(OutboxError, match="no pending"):
        await outbox.ack_next("anything")


# --------------------------------------------------------------------------- #
# Crash recovery — re-init Outbox over an existing root keeps the state        #
# --------------------------------------------------------------------------- #


async def test_crash_recovery_preserves_pending(tmp_path):
    root = tmp_path / "outbox"
    first = Outbox(root)
    eid_a = await first.append(_PAYLOAD_A, image_bytes=b"IMG")
    await first.append(_PAYLOAD_B)

    # New process: open the same root again. Pending entries + blobs survive.
    second = Outbox(root)
    assert await second.pending_count() == 2
    entries = [e async for e in second.iter_pending()]
    assert entries[0].entry_id == eid_a
    assert entries[0].image_bytes == b"IMG"


async def test_crash_recovery_preserves_acked_offset(tmp_path):
    root = tmp_path / "outbox"
    first = Outbox(root)
    eid_a = await first.append(_PAYLOAD_A)
    await first.append(_PAYLOAD_B)
    await first.ack_next(eid_a)

    second = Outbox(root)
    assert await second.pending_count() == 1
    entries = [e async for e in second.iter_pending()]
    assert entries[0].payload == _PAYLOAD_B


# --------------------------------------------------------------------------- #
# fsync — kickoff 宪法要求 append 与 ack 都要 fsync 后才视为持久化            #
# --------------------------------------------------------------------------- #


async def test_append_fsyncs_log_and_blobs(outbox, monkeypatch):
    """Each append must fsync the jsonl line and any blob it writes — otherwise
    a power loss between flush and writeback can corrupt the log."""
    fsynced: list[int] = []
    real_fsync = os.fsync

    def _spy(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr("timetrace.client.core.outbox.os.fsync", _spy)
    await outbox.append(_PAYLOAD_A, image_bytes=b"IMG", thumb_bytes=b"TH")
    # 1 jsonl + 2 blobs = 3 fds
    assert len(fsynced) >= 3


async def test_ack_fsyncs_state(outbox, monkeypatch):
    """state.json rename must be durable — fsync after replace."""
    eid = await outbox.append(_PAYLOAD_A)

    fsynced: list[int] = []
    real_fsync = os.fsync

    def _spy(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr("timetrace.client.core.outbox.os.fsync", _spy)
    await outbox.ack_next(eid)
    assert len(fsynced) >= 1


async def test_partial_last_line_is_tolerated(tmp_path):
    """If the process dies mid-append, log.jsonl can end with a half-written line.
    The outbox must skip that line and continue, not refuse to start."""
    root = tmp_path / "outbox"
    first = Outbox(root)
    eid_good = await first.append(_PAYLOAD_A)

    # Simulate crash: append a partial JSON line (no closing brace, no newline).
    log_path = root / "log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write('{"entry_id": "broken", "timestamp_ms": 0, "payload"')

    second = Outbox(root)
    # The complete entry survives; the half-written line is silently dropped.
    assert await second.pending_count() == 1
    entries = [e async for e in second.iter_pending()]
    assert entries[0].entry_id == eid_good


# --------------------------------------------------------------------------- #
# Compaction                                                                    #
# --------------------------------------------------------------------------- #


async def test_compact_noop_when_nothing_acked(outbox):
    await outbox.append(_PAYLOAD_A)
    reclaimed = await outbox.compact()
    assert reclaimed == 0
    assert await outbox.pending_count() == 1


async def test_compact_below_min_acked_is_noop(outbox):
    eid_a = await outbox.append(_PAYLOAD_A)
    await outbox.ack_next(eid_a)
    # 1 acked but min_acked=10 → skip
    reclaimed = await outbox.compact(min_acked=10)
    assert reclaimed == 0


async def test_compact_drops_acked_entries_and_keeps_unacked(outbox, tmp_path):
    eid_a = await outbox.append(_PAYLOAD_A)
    eid_b = await outbox.append(_PAYLOAD_B)
    eid_c = await outbox.append({"client_record_id": "rc"})

    await outbox.ack_next(eid_a)
    await outbox.ack_next(eid_b)
    assert await outbox.pending_count() == 1

    reclaimed = await outbox.compact(min_acked=1)
    assert reclaimed == 2

    # State reset to 0; log holds only the unacked entry
    assert await outbox.pending_count() == 1
    entries = [e async for e in outbox.iter_pending()]
    assert [e.entry_id for e in entries] == [eid_c]


async def test_compact_unlinks_acked_blobs(outbox, tmp_path):
    eid_a = await outbox.append(_PAYLOAD_A, image_bytes=b"img-A", thumb_bytes=b"thumb-A")
    eid_b = await outbox.append(_PAYLOAD_B, image_bytes=b"img-B")

    blobs_dir = tmp_path / "outbox" / "blobs"
    assert (blobs_dir / f"{eid_a}-image").exists()
    assert (blobs_dir / f"{eid_a}-thumb").exists()
    assert (blobs_dir / f"{eid_b}-image").exists()

    await outbox.ack_next(eid_a)
    await outbox.compact(min_acked=1)

    # eid_a's blobs gone, eid_b's still present
    assert not (blobs_dir / f"{eid_a}-image").exists()
    assert not (blobs_dir / f"{eid_a}-thumb").exists()
    assert (blobs_dir / f"{eid_b}-image").exists()


async def test_compact_can_continue_acking_after(outbox):
    eid_a = await outbox.append(_PAYLOAD_A)
    eid_b = await outbox.append(_PAYLOAD_B)
    eid_c = await outbox.append({"client_record_id": "rc"})

    await outbox.ack_next(eid_a)
    await outbox.ack_next(eid_b)
    await outbox.compact(min_acked=1)

    # After compaction, eid_c is now the head (acked=0); ack_next must work.
    await outbox.ack_next(eid_c)
    assert await outbox.pending_count() == 0


async def test_compact_survives_crash_between_state_and_log(tmp_path, monkeypatch):
    """Crash AFTER state reset but BEFORE log rewrite → at-least-once replay
    of acked entries on next start. Server's idempotency absorbs the dups."""
    root = tmp_path / "outbox"
    first = Outbox(root)
    eid_a = await first.append(_PAYLOAD_A)
    eid_b = await first.append(_PAYLOAD_B)
    await first.ack_next(eid_a)

    # Patch _rewrite_log to raise BEFORE the rename, simulating mid-compact crash.
    def _crash(self, entries):
        raise RuntimeError("simulated crash mid-compaction")

    monkeypatch.setattr(Outbox, "_rewrite_log", _crash, raising=True)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await first.compact(min_acked=1)

    # Re-open: state.acked is 0, log still has [a, b] → both will replay.
    second = Outbox(root)
    assert await second.pending_count() == 2  # 2 entries, 0 acked
    entries = [e async for e in second.iter_pending()]
    assert [e.entry_id for e in entries] == [eid_a, eid_b]


async def test_compact_preserves_strict_fifo_ordering(outbox):
    """After compaction, iter_pending order matches original submission order."""
    ids = []
    for i in range(5):
        ids.append(await outbox.append({"client_record_id": f"r{i}"}))

    # Ack first 3
    for i in range(3):
        await outbox.ack_next(ids[i])

    await outbox.compact(min_acked=1)

    entries = [e async for e in outbox.iter_pending()]
    assert [e.entry_id for e in entries] == ids[3:]
