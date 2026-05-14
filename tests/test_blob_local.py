"""Tests for LocalBlobStorage (the default BlobStorage Protocol implementation)."""

from __future__ import annotations

import hashlib

import pytest

from timetrace.server.storage.blob import (
    BlobIntegrityError,
    BlobNotFound,
    LocalBlobStorage,
)


@pytest.fixture
def storage(tmp_path) -> LocalBlobStorage:
    return LocalBlobStorage(tmp_path / "blobs")


async def test_put_then_get_roundtrip(storage):
    data = b"hello, world"
    await storage.put("a/b/c.bin", data)
    assert await storage.get("a/b/c.bin") == data


async def test_get_missing_raises_blob_not_found(storage):
    with pytest.raises(BlobNotFound):
        await storage.get("does/not/exist.bin")


async def test_put_creates_intermediate_directories(storage):
    await storage.put("deep/nested/path/file.txt", b"x")
    assert await storage.exists("deep/nested/path/file.txt")


async def test_put_with_correct_md5_succeeds(storage):
    data = b"content"
    md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()
    await storage.put("c.bin", data, expected_md5=md5)
    assert await storage.get("c.bin") == data


async def test_put_with_wrong_md5_raises(storage):
    with pytest.raises(BlobIntegrityError):
        await storage.put("c.bin", b"content", expected_md5="0" * 32)


async def test_delete_is_idempotent(storage):
    await storage.put("k.bin", b"x")
    await storage.delete("k.bin")
    assert not await storage.exists("k.bin")
    # Calling delete again on the missing key must not raise
    await storage.delete("k.bin")


async def test_absolute_key_rejected(storage):
    with pytest.raises(ValueError, match="relative"):
        await storage.put("/etc/passwd", b"x")


async def test_traversal_key_rejected(storage):
    with pytest.raises(ValueError, match="escapes"):
        await storage.put("../../etc/passwd", b"x")


async def test_root_dir_is_created_on_init(tmp_path):
    storage = LocalBlobStorage(tmp_path / "fresh")
    assert (tmp_path / "fresh").is_dir()
    await storage.put("x.bin", b"y")
    assert await storage.get("x.bin") == b"y"
