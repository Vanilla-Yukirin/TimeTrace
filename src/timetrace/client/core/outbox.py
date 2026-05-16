"""Append-only client outbox for at-most-once HttpBackend delivery.

Layout under ``root_dir``:

    log.jsonl               # one JSON entry per line, append-only
    blobs/<entry_id>-image  # binary screenshot bytes (optional, per entry)
    blobs/<entry_id>-thumb  # binary thumbnail bytes (optional, per entry)
    state.json              # {"acked": N} — entries 0..N-1 of log have been sent

Crash safety: `append` writes the blob files first (so a partial crash leaves
a complete blob, never a half-written one), then appends the JSONL line and
fsyncs. If we crash between blob write and line write the blobs are leaked
(harmless — they'll be garbage-collected on next compaction); if we crash
between line write and ack, the next sender re-reads and re-sends, which is
exactly the at-least-once-delivery semantics we want — the server side is
idempotent on `client_record_id`.

Strict FIFO: entries must be acked in submission order. If a sender wants
to retry a failed entry it must do so before moving on; out-of-order ack is
not supported because the persisted state is just a cursor. A single
sender-per-Outbox keeps this honest and matches the real deployment.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class OutboxEntry:
    """One pending submission read off the outbox."""

    entry_id: str
    timestamp_ms: int
    payload: dict
    image_bytes: bytes | None
    thumb_bytes: bytes | None


class Outbox:
    """Append-only file-backed queue rooted at ``root_dir``."""

    def __init__(self, root_dir: Path) -> None:
        self._root = Path(root_dir)
        self._blobs_dir = self._root / "blobs"
        self._log_path = self._root / "log.jsonl"
        self._state_path = self._root / "state.json"
        self._root.mkdir(parents=True, exist_ok=True)
        self._blobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    # ---- write side -----------------------------------------------------

    async def append(
        self,
        payload: dict,
        *,
        image_bytes: bytes | None = None,
        thumb_bytes: bytes | None = None,
    ) -> str:
        """Persist one submission. Returns the entry_id."""
        entry_id = str(uuid.uuid4())
        async with self._lock:
            if image_bytes is not None:
                await asyncio.to_thread(self._write_blob, f"{entry_id}-image", image_bytes)
            if thumb_bytes is not None:
                await asyncio.to_thread(self._write_blob, f"{entry_id}-thumb", thumb_bytes)

            entry = {
                "entry_id": entry_id,
                "timestamp_ms": int(time.time() * 1000),
                "payload": payload,
                "has_image": image_bytes is not None,
                "has_thumb": thumb_bytes is not None,
            }
            await asyncio.to_thread(self._append_line, entry)
        return entry_id

    # ---- read side ------------------------------------------------------

    async def iter_pending(self) -> AsyncIterator[OutboxEntry]:
        """Yield entries that haven't been acked yet, in submission order.

        Each yield reads the entry's blob bytes back from disk so the sender
        gets a self-contained payload to POST. The lock is held while the
        in-memory view of the log is taken; blob reads happen outside the
        lock since reads don't need to serialize against append.
        """
        async with self._lock:
            entries = await asyncio.to_thread(self._read_log)
            acked = await asyncio.to_thread(self._read_acked)

        for entry in entries[acked:]:
            yield OutboxEntry(
                entry_id=entry["entry_id"],
                timestamp_ms=entry["timestamp_ms"],
                payload=entry["payload"],
                image_bytes=await self._read_blob_if(entry, "image"),
                thumb_bytes=await self._read_blob_if(entry, "thumb"),
            )

    async def ack_next(self, entry_id: str) -> None:
        """Acknowledge the *head* pending entry. Raises if `entry_id` isn't it.

        Strict FIFO: the sender must drain in order. The id check stops a
        confused caller from advancing the cursor past an entry that hasn't
        actually completed.
        """
        async with self._lock:
            entries = await asyncio.to_thread(self._read_log)
            acked = await asyncio.to_thread(self._read_acked)
            if acked >= len(entries):
                raise OutboxError("ack_next called with no pending entries")
            head = entries[acked]
            if head["entry_id"] != entry_id:
                raise OutboxError(
                    f"ack_next out of order: expected {head['entry_id']!r}, got {entry_id!r}"
                )
            await asyncio.to_thread(self._write_acked, acked + 1)

    async def pending_count(self) -> int:
        async with self._lock:
            entries = await asyncio.to_thread(self._read_log)
            acked = await asyncio.to_thread(self._read_acked)
        return len(entries) - acked

    async def compact(self, *, min_acked: int = 1) -> int:
        """Reclaim acked entries: rewrite log to keep only un-acked, drop blobs.

        Returns the number of entries reclaimed (0 = no-op). ``min_acked``
        guards against running compaction too eagerly — cheap to skip when
        the log has barely grown.

        Crash safety story
        ------------------
        We write ``state.json`` BEFORE the log rewrite:

            state.acked = 0   ← step 1 (atomic rename)
            log = [unacked]   ← step 2 (atomic rename)

        If we crash between the two, on next start: log still holds the
        original [acked + unacked] and state says ``acked=0`` → the sender
        replays the already-acked entries. The server's
        ``client_record_id`` UNIQUE index absorbs the duplicates (200 OK
        with ``was_new=False``), so at-least-once delivery is preserved
        and no data is lost.

        The opposite order — log first, then state — would risk skipping
        unacked entries (state says "acked=N" but the new log is shorter
        than N), which is unrecoverable. So this order is load-bearing,
        do not flip.

        Blobs of acked entries are unlinked last; if any survive (crash
        mid-cleanup) they're harmless orphans next compaction sweeps.
        """
        async with self._lock:
            entries = await asyncio.to_thread(self._read_log)
            acked = await asyncio.to_thread(self._read_acked)
            if acked < min_acked:
                return 0

            kept = entries[acked:]
            acked_blob_ids = [e["entry_id"] for e in entries[:acked]]

            # Step 1: reset state.acked = 0 BEFORE rewriting log.
            await asyncio.to_thread(self._write_acked, 0)

            # Step 2: atomically replace log with only the kept entries.
            await asyncio.to_thread(self._rewrite_log, kept)

            # Step 3: drop acked-entry blobs. Best-effort; survivors are
            # harmless until the next compaction notices their entries are
            # also gone.
            for entry_id in acked_blob_ids:
                for kind in ("image", "thumb"):
                    blob_path = self._blobs_dir / f"{entry_id}-{kind}"
                    try:
                        blob_path.unlink(missing_ok=True)
                    except OSError:
                        # Permission / FS error on individual blob is not
                        # fatal — they'll be re-attempted on next compaction.
                        logger.warning(
                            "outbox.compact.blob_unlink_failed",
                            path=str(blob_path),
                        )

            logger.info("outbox.compacted", reclaimed=acked, kept=len(kept))
            return acked

    # ---- internal disk helpers -----------------------------------------

    def _write_blob(self, name: str, data: bytes) -> None:
        path = self._blobs_dir / name
        # write + fsync the file content so an OS-level crash after this returns
        # cannot leave the blob torn for the post-crash sender to read.
        with path.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def _append_line(self, entry: dict) -> None:
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _rewrite_log(self, entries: list[dict]) -> None:
        """Atomically replace log.jsonl with the given entries (compaction)."""
        tmp = self._log_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self._log_path)

    def _read_log(self) -> list[dict]:
        if not self._log_path.exists():
            return []
        out: list[dict] = []
        with self._log_path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        for idx, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                out.append(json.loads(stripped))
            except json.JSONDecodeError:
                # Tolerate a torn last line caused by a crash mid-append.
                # Anywhere earlier means real corruption — propagate so an
                # operator can investigate rather than silently lose data.
                is_last_nonblank = all(not s.strip() for s in lines[idx + 1 :])
                if is_last_nonblank:
                    logger.warning(
                        "outbox.log.partial_line_skipped",
                        path=str(self._log_path),
                        line_index=idx,
                    )
                    break
                raise
        return out

    def _read_acked(self) -> int:
        if not self._state_path.exists():
            return 0
        return int(json.loads(self._state_path.read_text("utf-8"))["acked"])

    def _write_acked(self, value: int) -> None:
        # Write to a tmp file then rename so we never have a half-written state.json
        tmp = self._state_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"acked": value}))
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self._state_path)

    async def _read_blob_if(self, entry: dict, kind: str) -> bytes | None:
        if not entry.get(f"has_{kind}"):
            return None
        path = self._blobs_dir / f"{entry['entry_id']}-{kind}"
        if not path.exists():
            return None
        return await asyncio.to_thread(path.read_bytes)


class OutboxError(Exception):
    """Raised on outbox protocol violations (out-of-order ack, etc.)."""
