"""BackendClient Protocol and concrete implementations.

Capture used to call `Database` and `PHashIndex` directly, which forced the
client tier to import from the server tier. BackendClient pushes the only
boundary capture cares about — "submit a record / submit a screenshot /
mark pending" — into the common surface, so capture stays tier-pure and
the actual transport can swap underneath.

Today there are two implementations:

- ``InProcessBackend`` — direct adapter over Database + PHashIndex used by
  the single-process default ``uv run timetrace``.
- ``HttpBackend`` — POSTs to ``/v1/ingest/record`` and friends. Used by the
  two-process mode that P3b will turn on (``timetrace-client`` +
  ``timetrace-server``). ``submit_record`` fires a record-only POST and
  returns the server-assigned record_id; ``submit_screenshot`` fires a
  second POST with image bytes against the same client_record_id which
  the server attaches to the existing record. ``close_record`` hits
  ``POST /v1/ingest/record/{id}/close``.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import TYPE_CHECKING, Protocol

import httpx

from timetrace.common.phash_hash import phash_to_blob

if TYPE_CHECKING:
    from timetrace.common.models import CaptureContext
    from timetrace.common.protocol import ScreenshotSubmission
    from timetrace.server.db import Database
    from timetrace.server.phash_index.index import PHashIndex


class BackendError(Exception):
    """Raised when a backend call fails (network, 4xx/5xx, malformed reply)."""


class BackendClient(Protocol):
    """The minimal capture-facing surface for a TimeTrace backend."""

    async def submit_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
    ) -> str:
        """Persist a new activity record. Returns the new record id."""
        ...

    async def close_record(self, record_id: str) -> None:
        """Set ts_end on a previously-submitted record."""
        ...

    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str:
        """Persist a screenshot + thumbnail and update any side indexes.

        Returns the new screenshot id. Implementations are responsible for
        feeding the pHash into whatever similarity index they keep.
        """
        ...

    async def mark_pending(self, record_id: str) -> None:
        """Enqueue a record for downstream analysis (VLM)."""
        ...


class InProcessBackend:
    """Direct adapter over Database + PHashIndex.

    Behaviour-preserving: each method forwards to the same call the capture
    loop made before P2. The only consolidation is `submit_screenshot`, which
    now also handles the phash-index update + ts_start lookup the capture loop
    used to do inline.
    """

    def __init__(self, db: Database, phash_index: PHashIndex | None = None) -> None:
        self._db = db
        self._phash_index = phash_index

    async def submit_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
    ) -> str:
        return await self._db.insert_record(ctx, reason=reason, event_type=event_type)

    async def close_record(self, record_id: str) -> None:
        await self._db.close_record(record_id)

    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str:
        phash_blob = phash_to_blob(payload.phash) if payload.phash is not None else None
        screenshot_id = await self._db.insert_screenshot(
            record_id=payload.record_id,
            path=payload.path,
            thumb_path=payload.thumb_path,
            width=payload.width,
            height=payload.height,
            hash_sha256=payload.hash_sha256,
            privacy_level=payload.privacy_level,
            phash=phash_blob,
        )
        if payload.phash is not None and self._phash_index is not None:
            ts_start = await self._db.get_record_ts_start(payload.record_id)
            if ts_start is not None:
                self._phash_index.insert(screenshot_id, payload.phash, ts_start)
        return screenshot_id

    async def mark_pending(self, record_id: str) -> None:
        await self._db.mark_pending(record_id)


# --------------------------------------------------------------------------- #
# HTTP transport                                                                #
# --------------------------------------------------------------------------- #


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class HttpBackend:
    """BackendClient implementation that talks to a remote (or local) timetrace
    server over `/v1/ingest/*`.

    Maps the capture-facing two-call pattern (`submit_record` then
    `submit_screenshot`) onto the ingest endpoint via a shared
    `client_record_id` that the backend generates locally. The HTTP layer
    sees both calls as POSTs against the same id, with the server returning
    `was_new=False` on the second.

    Construction takes either an explicit `httpx.AsyncClient` (tests pass a
    transport-bound client so they can drive against `TestClient`) or a
    `base_url` + optional `auth_token` for production use.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        auth_token: str | None = None,
        device_id: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        # Hold device_id so callers can inject it on a borrowed client too,
        # via a single "extra headers" dict on each request.
        self._device_id = device_id or None
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            if base_url is None:
                raise ValueError("HttpBackend needs either base_url or an AsyncClient")
            headers: dict[str, str] = {}
            if auth_token:
                headers["Authorization"] = f"Bearer {auth_token}"
            if self._device_id:
                headers["X-Device-Id"] = self._device_id
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers=headers or None,
                timeout=timeout_s,
            )
            self._owns_client = True
        # client_record_id -> server-assigned record_id, populated on submit_record
        self._record_id_for: dict[str, str] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- BackendClient implementation ---------------------------------------

    async def submit_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
    ) -> str:
        """Generate a client_record_id, POST a record-only ingest, return it.

        The id returned to capture is the *client*-side id — that's what
        capture later passes back via `submit_screenshot.payload.record_id`
        and `close_record(record_id)`. We bookkeep the server-side id
        internally for any future endpoints that need it.
        """
        client_record_id = str(uuid.uuid4())
        payload = {
            "client_record_id": client_record_id,
            "ts_start": int(time.time() * 1000),
            "app_name": ctx.app_name,
            "process_name": ctx.process_name,
            "window_title": ctx.window_title,
            "url": ctx.url,
            "capture_reason": reason,
            "event_type": event_type,
        }
        response = await self._post_ingest(payload, image_bytes=None, thumb_bytes=None)
        self._record_id_for[client_record_id] = response["record_id"]
        return client_record_id

    async def close_record(self, record_id: str) -> None:
        """Close the record on the server (sets ts_end to client clock)."""
        server_id = self._record_id_for.get(record_id, record_id)
        ts_end = int(time.time() * 1000)
        resp = await self._client.post(
            f"/v1/ingest/record/{server_id}/close",
            json={"ts_end": ts_end},
            headers=self._extra_headers(),
        )
        if resp.status_code >= 400:
            raise BackendError(f"close_record failed: HTTP {resp.status_code} {resp.text!r}")

    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str:
        """Attach a screenshot to a previously-submitted record.

        `payload.record_id` is the client_record_id returned from `submit_record`.
        The screenshot bytes are read from `payload.path` (which the in-process
        screenshot writer placed on disk); HttpBackend reads, MD5s, and POSTs
        them as the multipart `image` part. Same for `thumb_path`.
        """
        # Read bytes off disk — capture's screenshot module wrote them there.
        # Bridges between "in-process file write" and "HTTP upload" without
        # changing the screenshot writer; P4 will fold the upload into the
        # privacy pipeline directly.
        from pathlib import Path  # noqa: PLC0415

        image_path = Path(payload.path)
        if not image_path.is_absolute():
            raise BackendError(f"HttpBackend needs absolute screenshot path; got {payload.path!r}")
        image_bytes = image_path.read_bytes()

        thumb_bytes: bytes | None = None
        if payload.thumb_path:
            thumb_path = Path(payload.thumb_path)
            if not thumb_path.is_absolute():
                raise BackendError(
                    f"HttpBackend needs absolute thumb path; got {payload.thumb_path!r}"
                )
            thumb_bytes = thumb_path.read_bytes()

        client_record_id = payload.record_id
        ingest_payload = {
            "client_record_id": client_record_id,
            "ts_start": int(time.time() * 1000),
            "image_md5": _md5(image_bytes),
            "image_width": payload.width,
            "image_height": payload.height,
            "image_phash": payload.phash,
            "image_format": image_path.suffix.lstrip(".") or "png",
            "thumb_md5": _md5(thumb_bytes) if thumb_bytes else None,
            "thumb_format": (thumb_path.suffix.lstrip(".") if payload.thumb_path else "jpg"),
        }

        response = await self._post_ingest(
            ingest_payload, image_bytes=image_bytes, thumb_bytes=thumb_bytes
        )
        sid = response.get("screenshot_id")
        if not sid:
            raise BackendError(f"ingest did not return screenshot_id: {response!r}")
        return sid

    async def mark_pending(self, record_id: str) -> None:
        """No-op for HttpBackend — the ingest route already calls mark_pending
        for every successful POST, so capture's explicit mark_pending after
        screenshot is redundant on this transport."""
        return

    # ---- internal -----------------------------------------------------------

    def _extra_headers(self) -> dict[str, str] | None:
        """Per-request headers that need to be added even when the AsyncClient
        was supplied externally (test path with ASGITransport-bound clients).

        For self-owned clients these headers are also pre-set on the client,
        so passing them again per-request is harmless duplication, not a bug.
        """
        if self._device_id:
            return {"X-Device-Id": self._device_id}
        return None

    async def _post_ingest(
        self,
        payload: dict,
        *,
        image_bytes: bytes | None,
        thumb_bytes: bytes | None,
    ) -> dict:
        # httpx wants `data=` for the JSON form field and `files=` for binaries;
        # mixing both in one call is the canonical multipart shape.
        data = {"record": json.dumps(payload, ensure_ascii=False)}
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        if image_bytes is not None:
            files.append(("image", ("image.bin", image_bytes, "application/octet-stream")))
        if thumb_bytes is not None:
            files.append(("thumb", ("thumb.bin", thumb_bytes, "application/octet-stream")))

        try:
            resp = await self._client.post(
                "/v1/ingest/record",
                data=data,
                files=files or None,
                headers=self._extra_headers(),
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"ingest POST failed: {exc}") from exc

        if resp.status_code >= 400:
            raise BackendError(f"ingest POST returned HTTP {resp.status_code}: {resp.text!r}")
        return resp.json()
