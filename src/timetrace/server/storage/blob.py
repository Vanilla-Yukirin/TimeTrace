"""BlobStorage Protocol + LocalBlobStorage implementation.

A `key` is the relative path under the storage root, e.g.
"screenshots/2026/05/15/uuid.png". LocalBlobStorage maps that key directly
to a file on disk; S3BlobStorage will map it to an object key.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Protocol


class BlobNotFound(Exception):
    """Raised by `BlobStorage.get` when the requested key does not exist."""


class BlobIntegrityError(Exception):
    """Raised by `BlobStorage.put` when the content fails the supplied checksum."""


class BlobStorage(Protocol):
    """Opaque-bytes store keyed by relative path."""

    async def put(self, key: str, data: bytes, *, expected_md5: str | None = None) -> str:
        """Store `data` at `key`. If `expected_md5` is provided, fail if it doesn't match.

        Returns the canonical key actually used (implementations may normalize).
        """
        ...

    async def get(self, key: str) -> bytes:
        """Read the bytes at `key`. Raises `BlobNotFound` if missing."""
        ...

    async def delete(self, key: str) -> None:
        """Remove `key`. Idempotent — missing keys do not raise."""
        ...

    async def exists(self, key: str) -> bool: ...


class LocalBlobStorage:
    """Filesystem-backed BlobStorage rooted at `root_dir`.

    All keys are joined relative to `root_dir`; absolute keys or `..` segments
    are rejected with ValueError so a malicious key can't escape the root.
    """

    def __init__(self, root_dir: Path) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")):
            raise ValueError(f"blob key must be relative: {key!r}")
        candidate = (self._root / key).resolve()
        try:
            candidate.relative_to(self._root.resolve())
        except ValueError as exc:
            raise ValueError(f"blob key escapes storage root: {key!r}") from exc
        return candidate

    async def put(self, key: str, data: bytes, *, expected_md5: str | None = None) -> str:
        if expected_md5 is not None:
            actual = hashlib.md5(data, usedforsecurity=False).hexdigest()
            if actual != expected_md5:
                raise BlobIntegrityError(
                    f"md5 mismatch for {key!r}: expected {expected_md5}, got {actual}"
                )
        path = self._resolve(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)
        return key

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise BlobNotFound(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()
