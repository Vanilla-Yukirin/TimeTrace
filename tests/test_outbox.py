"""Tests for the client-side append-only outbox."""

from __future__ import annotations

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
