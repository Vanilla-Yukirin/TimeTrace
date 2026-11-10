"""Disk-failure recovery with real files, including native Windows replace denial."""

from __future__ import annotations

import asyncio
import ctypes
import errno
import json
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from timetrace.client.core.outbox import Outbox, OutboxError
from timetrace.client.core.outbox_sender import OutboxSender


async def wait_empty(outbox, task):
    async with asyncio.timeout(5):
        while await outbox.pending_count():
            if task.done():
                await task
                pytest.fail("sender stopped before draining")
            await asyncio.sleep(0.01)


async def stop_sender(task, stop):
    stop.set()
    try:
        await asyncio.wait_for(task, 1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def denied():
    error = PermissionError(errno.EACCES, "fixture: atomic replace denied")
    error.winerror = 5
    return error


@pytest.mark.parametrize("concurrency", [1, 3])
async def test_ack_retry_keeps_capture_alive_without_resending(tmp_path, monkeypatch, concurrency):
    outbox = Outbox(tmp_path)
    first = await outbox.append({"client_record_id": "first"}, image_bytes=b"image")
    outbox._write_acked(0)
    blocked = threading.Event()
    blocked.set()
    failed = asyncio.Event()
    attempts = []
    delivered = []
    failovers = []
    replace = Path.replace

    def guarded_replace(path, target):
        if Path(target) == tmp_path / "state.json" and blocked.is_set():
            attempts.append(path)
            raise denied()
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", guarded_replace)

    async def send(entry):
        delivered.append(entry.entry_id)

    async def failover():
        failovers.append(True)

    sender = OutboxSender(
        outbox,
        send,
        concurrency=concurrency,
        backoff_initial_s=0.01,
        backoff_max_s=0.02,
        idle_poll_interval_s=0.01,
        on_send_failure=failover,
        on_storage_error=lambda where, error: failed.set(),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))
    try:
        await asyncio.wait_for(failed.wait(), 2)
        async with asyncio.timeout(2):
            while len(attempts) < 3:
                await asyncio.sleep(0.01)
        assert not task.done()
        assert delivered == [first]
        assert json.loads((tmp_path / "state.json").read_text()) == {"acked": 0}
        assert not list(tmp_path.glob(".*.tmp"))
        assert len(set(attempts)) == len(attempts)
        second = await asyncio.wait_for(outbox.append({"client_record_id": "second"}), 1)
        assert await outbox.pending_count() == 2
        assert failovers == []
        blocked.clear()
        await wait_empty(outbox, task)
        assert delivered == [first, second]
        assert await Outbox(tmp_path).pending_count() == 0
    finally:
        blocked.clear()
        await stop_sender(task, stop)


@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("shutdown", ["stop", "cancel"])
async def test_persistent_ack_failure_can_stop_and_replay(
    tmp_path, monkeypatch, concurrency, shutdown
):
    outbox = Outbox(tmp_path)
    entry_id = await outbox.append({"client_record_id": "recover"}, image_bytes=b"image")
    failed = asyncio.Event()
    delivered = []
    write_acked = outbox._write_acked

    def fail_ack(value):
        raise denied()

    monkeypatch.setattr(outbox, "_write_acked", fail_ack)

    async def send(entry):
        delivered.append(entry.entry_id)

    sender = OutboxSender(
        outbox,
        send,
        concurrency=concurrency,
        backoff_initial_s=60,
        on_storage_error=lambda where, error: failed.set(),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))
    try:
        await asyncio.wait_for(failed.wait(), 2)
        if shutdown == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            await stop_sender(task, stop)
        pending = [entry async for entry in Outbox(tmp_path).iter_pending()]
        assert [entry.entry_id for entry in pending] == [entry_id]
        assert pending[0].image_bytes == b"image"
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    monkeypatch.setattr(outbox, "_write_acked", write_acked)
    task = asyncio.create_task(OutboxSender(outbox, send).run(stop := asyncio.Event()))
    try:
        await wait_empty(outbox, task)
        assert delivered == [entry_id, entry_id]  # replay only after restart
    finally:
        await stop_sender(task, stop)


@pytest.mark.parametrize("concurrency", [1, 3])
async def test_compaction_failure_rereads_cursor_before_next_ack(
    tmp_path, monkeypatch, concurrency
):
    outbox = Outbox(tmp_path)
    ids = [
        await outbox.append({"client_record_id": str(i)}, image_bytes=b"image") for i in range(4)
    ]
    rewrite = outbox._rewrite_log
    failures = []
    delivered = []

    def fail_once(entries):
        if not failures:
            failures.append(True)
            assert outbox._read_acked() == 0
            raise denied()
        rewrite(entries)

    monkeypatch.setattr(outbox, "_rewrite_log", fail_once)

    async def send(entry):
        assert entry.image_bytes == b"image"
        delivered.append(entry.entry_id)

    sender = OutboxSender(
        outbox,
        send,
        concurrency=concurrency,
        compact_every_n_acks=1,
        backoff_initial_s=0.01,
        idle_poll_interval_s=0.01,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(sender.run(stop))
    try:
        await wait_empty(outbox, task)
        assert set(delivered) == set(ids)
        assert delivered.count(ids[0]) == 2
        assert failures == [True]
        assert await Outbox(tmp_path).pending_count() == 0
    finally:
        await stop_sender(task, stop)


async def test_protocol_error_is_not_retried_as_disk_failure(tmp_path, monkeypatch):
    outbox = Outbox(tmp_path)
    await outbox.append({"client_record_id": "bad-ack"})

    async def bad_ack(entry_id):
        raise OutboxError("out of order")

    async def send(entry):
        pass

    monkeypatch.setattr(outbox, "ack_next", bad_ack)
    with pytest.raises(OutboxError, match="out of order"):
        await asyncio.wait_for(OutboxSender(outbox, send).run(), 1)


@contextmanager
def windows_replace_blocker(path, mode):
    if mode == "read_only":
        original = path.stat().st_mode
        path.chmod(stat.S_IREAD)
        try:
            yield
        finally:
            path.chmod(original)
        return
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # Allow reads and writes, but deny DELETE sharing (also controls rename).
    handle = create(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file attributes and sharing modes")
@pytest.mark.parametrize("mode", ["read_only", "open_handle"])
@pytest.mark.parametrize("concurrency", [1, 3])
async def test_native_windows_replace_denial_recovers(tmp_path, mode, concurrency):
    outbox = Outbox(tmp_path)
    first = await outbox.append({"client_record_id": "native"}, image_bytes=b"image")
    outbox._write_acked(0)
    failed = asyncio.Event()
    errors = []
    delivered = []

    def observe(where, error):
        errors.append((where, error))
        failed.set()

    async def send(entry):
        delivered.append(entry.entry_id)

    sender = OutboxSender(
        outbox,
        send,
        concurrency=concurrency,
        backoff_initial_s=0.01,
        backoff_max_s=0.02,
        idle_poll_interval_s=0.01,
        on_storage_error=observe,
    )
    stop = asyncio.Event()
    task = None
    try:
        with windows_replace_blocker(tmp_path / "state.json", mode):
            task = asyncio.create_task(sender.run(stop))
            await asyncio.wait_for(failed.wait(), 2)
            assert errors[0][0] == "outbox_ack"
            assert isinstance(errors[0][1], PermissionError)
            assert (
                errors[0][1].winerror == 5
                if mode == "read_only"
                else errors[0][1].winerror in (5, 32, 33)
            )
            assert not task.done()
            assert await outbox.pending_count() == 1
            assert await Outbox(tmp_path).pending_count() == 1
            second = await asyncio.wait_for(outbox.append({"client_record_id": "while-blocked"}), 1)
            assert outbox._read_acked() == 0
        await wait_empty(outbox, task)
        assert delivered == [first, second]
        assert await Outbox(tmp_path).pending_count() == 0
    finally:
        if task is not None:
            await stop_sender(task, stop)
