"""OutboxBackend — BackendClient implementation that defers all server I/O.

Serializes every capture-side call (submit_record / submit_screenshot /
close_record) into an append-only entry on an :class:`Outbox`. A separate
:class:`OutboxSender` later drains the outbox and POSTs each entry to a
real server via :class:`HttpBackend`.

This is the backend the standalone ``timetrace-client`` uses. It buys two
things over driving HttpBackend directly from capture:

1. **Decoupling capture from network state.** Capture never blocks waiting
   for a server roundtrip; it always returns immediately after the entry
   hits disk. Network failure handling is the sender's problem.
2. **Crash safety + at-least-once delivery.** Outbox is fsync-on-append;
   the sender retries with backoff and acks only after a successful POST.
   The server's ``client_record_id``-based idempotency absorbs any duplicate
   replays that follow a crash.

Wire-format note: each outbox entry's ``payload`` dict is **already in the
shape the server expects** under ``/v1/ingest/record`` (or the close
endpoint), with one extra ``"kind"`` field that the sender pops to choose
the right endpoint. Keeping the payload wire-ready means the sender stays
trivial — no domain re-mapping at drain time.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from timetrace.client.core.outbox import Outbox, OutboxEntry

if TYPE_CHECKING:
    from timetrace.client.core.backend import HttpBackend
    from timetrace.common.models import CaptureContext
    from timetrace.common.protocol import ScreenshotSubmission


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


class OutboxBackend:
    """BackendClient implementation that writes to an Outbox instead of HTTP."""

    def __init__(self, outbox: Outbox, *, data_dir: Path | None = None) -> None:
        self._outbox = outbox
        # data_dir lets submit_screenshot resolve relative paths emitted by
        # capture_active_window. None means only absolute paths are accepted.
        self._data_dir = Path(data_dir).resolve() if data_dir is not None else None

    # ---- BackendClient surface --------------------------------------------- #

    async def submit_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
    ) -> str:
        """Generate a client_record_id, queue a record-only ingest entry."""
        client_record_id = str(uuid.uuid4())
        await self._outbox.append(
            {
                "kind": "ingest",
                "client_record_id": client_record_id,
                "ts_start": _now_ms(),
                "app_name": ctx.app_name,
                "process_name": ctx.process_name,
                "window_title": ctx.window_title,
                "url": ctx.url,
                "capture_reason": reason,
                "event_type": event_type,
            }
        )
        return client_record_id

    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str:
        """Read the screenshot bytes off disk and queue them as an ingest entry.

        Bytes are inlined into the outbox blob store (not the jsonl) so the
        outbox is self-contained — capture can delete its local screenshot
        copy after this returns and the sender will still have everything
        it needs to POST. (capture today doesn't delete; this is a future
        ergonomic affordance.)
        """
        image_bytes = self._read_path(payload.path)
        image_format = Path(payload.path).suffix.lstrip(".") or "png"

        thumb_bytes: bytes | None = None
        thumb_format = "jpg"
        if payload.thumb_path:
            thumb_bytes = self._read_path(payload.thumb_path)
            thumb_format = Path(payload.thumb_path).suffix.lstrip(".") or "jpg"

        await self._outbox.append(
            {
                "kind": "ingest",
                "client_record_id": payload.record_id,
                "ts_start": _now_ms(),
                "image_md5": _md5(image_bytes),
                "image_width": payload.width,
                "image_height": payload.height,
                "image_phash": payload.phash,
                "image_format": image_format,
                "thumb_md5": _md5(thumb_bytes) if thumb_bytes else None,
                "thumb_format": thumb_format,
            },
            image_bytes=image_bytes,
            thumb_bytes=thumb_bytes,
        )
        # Synthetic id — the real screenshot id is server-side and not knowable
        # at queue time. Capture only logs this return value, never persists.
        return f"deferred:{payload.record_id}"

    async def close_record(self, record_id: str) -> None:
        """Queue a close entry stamping the *current* client clock as ts_end.

        The sender replays this with the ts_end captured here, not its own
        drain-time clock — so a backed-up outbox doesn't smear close moments
        across an arbitrary recovery window.
        """
        await self._outbox.append(
            {
                "kind": "close",
                "client_record_id": record_id,
                "ts_end": _now_ms(),
            }
        )

    async def mark_pending(self, record_id: str) -> None:
        """No-op. The server's /v1/ingest/record route already calls
        ``mark_pending`` (INSERT OR IGNORE) on every successful POST."""
        return

    # ---- internals --------------------------------------------------------- #

    def _read_path(self, raw: str) -> bytes:
        path = Path(raw)
        if not path.is_absolute():
            if self._data_dir is None:
                raise FileNotFoundError(
                    f"OutboxBackend got relative path {raw!r} but no data_dir to resolve against"
                )
            path = self._data_dir / path
        return path.read_bytes()


# --------------------------------------------------------------------------- #
# Sender adapter                                                                #
# --------------------------------------------------------------------------- #


def make_http_sender(http: HttpBackend) -> Callable[[OutboxEntry], Awaitable[None]]:
    """Build the ``send`` callback OutboxSender expects, wired to an HttpBackend.

    Dispatch by ``payload["kind"]``:
    - ``"ingest"`` → ``http.post_ingest`` with payload + bytes
    - ``"close"`` → ``http.post_close`` with id + ts_end
    """

    async def _send(entry: OutboxEntry) -> None:
        kind = entry.payload.get("kind")
        if kind == "ingest":
            payload = {k: v for k, v in entry.payload.items() if k != "kind"}
            await http.post_ingest(
                payload,
                image_bytes=entry.image_bytes,
                thumb_bytes=entry.thumb_bytes,
            )
        elif kind == "close":
            await http.post_close(
                entry.payload["client_record_id"],
                ts_end=entry.payload["ts_end"],
            )
        else:
            raise ValueError(f"OutboxSender: unknown entry kind {kind!r}")

    return _send
