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
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

from timetrace.common.phash_hash import phash_to_blob
from timetrace.common.protocol import DeviceMetadata

if TYPE_CHECKING:
    from timetrace.common.models import CaptureContext
    from timetrace.common.protocol import ScreenshotSubmission
    from timetrace.server.db import Database
    from timetrace.server.phash_index.index import PHashIndex


class BackendError(Exception):
    """Raised when a backend call fails (network, 4xx/5xx, malformed reply)."""


def resolve_path_under_data_dir(raw: str, data_dir: Path | None, *, kind: str) -> Path:
    """Normalise a path against an optional ``data_dir`` base.

    Shared by every BackendClient that needs to read screenshot bytes off
    disk (HttpBackend, OutboxBackend). Behaviour:

    - Absolute paths are returned as-is.
    - Relative paths are joined to ``data_dir`` and ``resolve()``'d.
    - ``data_dir is None`` makes relative paths an error — refusing to
      silently read from CWD avoids accidental log/config exfiltration.
    - Relative paths whose ``resolve()`` escapes ``data_dir``
      (``../../../etc/passwd``) raise — defence against a malicious
      ScreenshotSubmission feeding host secrets into the upload pipeline.
    """
    path = Path(raw)
    if path.is_absolute():
        return path
    if data_dir is None:
        raise BackendError(f"need absolute {kind} path or a data_dir; got {raw!r}")
    resolved = (data_dir / path).resolve()
    try:
        resolved.relative_to(data_dir)
    except ValueError as exc:
        raise BackendError(
            f"{kind} path {raw!r} resolves outside data_dir {str(data_dir)!r}"
        ) from exc
    return resolved


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

    async def submit_screenshot(self, payload: ScreenshotSubmission) -> str | None:
        """Persist a screenshot + thumbnail and update any side indexes.

        Returns the new screenshot id when the implementation can produce
        one synchronously (InProcessBackend, HttpBackend) or ``None`` when
        the call is queued for later upload (OutboxBackend, where the real
        id is server-side and unknown until drain time). Capture treats both
        the same — neither value is persisted client-side.

        Implementations are responsible for feeding the pHash into whatever
        similarity index they keep.
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
        screenshot_id, was_new = await self._db.insert_screenshot(
            record_id=payload.record_id,
            path=payload.path,
            thumb_path=payload.thumb_path,
            width=payload.width,
            height=payload.height,
            hash_sha256=payload.hash_sha256,
            privacy_level=payload.privacy_level,
            phash=phash_blob,
        )
        if was_new and payload.phash is not None and self._phash_index is not None:
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
        device_name: str = "",
        device_description: str = "",
        client_version: str = "",
        capabilities: tuple[str, ...] = (),
        data_dir: Path | str | None = None,
        timeout_s: float = 30.0,
        base_url_provider: Callable[[], str | None] | None = None,
    ) -> None:
        # Hold auth_token + device_id on the instance so they're injected on
        # every request regardless of whether the AsyncClient is owned (headers
        # baked in at construction) or borrowed (per-request _extra_headers).
        self._auth_token = auth_token or None
        self._device_id = device_id or None
        try:
            self._device_metadata = DeviceMetadata(
                name=device_name,
                description=device_description,
                client_version=client_version,
                capabilities=list(capabilities),
            ).model_dump()
        except ValueError as exc:
            raise ValueError(
                "invalid HttpBackend device metadata; correct client configuration before "
                f"upload: {exc}"
            ) from exc
        # data_dir lets submit_screenshot resolve paths relative to the client's
        # storage root (capture_active_window emits relative paths). Without it,
        # only absolute paths are accepted.
        self._data_dir: Path | None = Path(data_dir).resolve() if data_dir is not None else None
        # When set, the active base URL is resolved per-request (multi-endpoint
        # failover). The owned client then carries no base_url and every request
        # passes an absolute URL. When None, the fixed base_url is used as before.
        self._base_url_provider = base_url_provider
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            if base_url is None and base_url_provider is None:
                raise ValueError("HttpBackend needs base_url, base_url_provider, or an AsyncClient")
            headers: dict[str, str] = {}
            if self._auth_token:
                headers["Authorization"] = f"Bearer {self._auth_token}"
            if self._device_id:
                headers["X-Device-Id"] = self._device_id
            self._client = httpx.AsyncClient(
                base_url=base_url or "",
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
        """Close the record on the server (sets ts_end to current client clock)."""
        await self.post_close(record_id, ts_end=int(time.time() * 1000))

    async def post_close(self, record_id: str, ts_end: int) -> None:
        """Wire-level close with a caller-supplied ts_end.

        Exposed for OutboxSender so it can replay close entries with the
        ts_end that was stamped when capture originally produced the close,
        not the time the sender happens to drain the outbox entry.
        """
        server_id = self._record_id_for.get(record_id, record_id)
        resp = await self._client.post(
            self._url(f"/v1/ingest/record/{server_id}/close"),
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

        Paths may be either absolute or relative. Relative paths are resolved
        against ``self._data_dir`` (the storage root the capture loop writes
        into); without a configured ``data_dir`` only absolute paths are
        accepted.
        """
        # Bridges between "in-process file write" and "HTTP upload" without
        # changing the screenshot writer; P4 will fold the upload into the
        # privacy pipeline directly.
        image_path = self._resolve_path(payload.path, kind="screenshot")
        image_bytes = image_path.read_bytes()

        thumb_bytes: bytes | None = None
        thumb_path: Path | None = None
        if payload.thumb_path:
            thumb_path = self._resolve_path(payload.thumb_path, kind="thumb")
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

    def _resolve_path(self, raw: str, *, kind: str) -> Path:
        return resolve_path_under_data_dir(raw, self._data_dir, kind=kind)

    def _url(self, path: str) -> str:
        """Resolve a request path to send.

        With a ``base_url_provider`` (multi-endpoint mode) build an absolute URL
        against the currently-selected endpoint, raising if none is healthy so
        the outbox keeps the entry pending. Without one, return the relative
        path (the owned client carries the fixed ``base_url``)."""
        if self._base_url_provider is not None:
            base = self._base_url_provider()
            if not base:
                raise BackendError("no healthy endpoint available")
            return base.rstrip("/") + path
        return path

    def _extra_headers(self) -> dict[str, str] | None:
        """Per-request headers that need to be added even when the AsyncClient
        was supplied externally (test path with ASGITransport-bound clients).

        For self-owned clients these headers are also pre-set on the client,
        so passing them again per-request is harmless duplication, not a bug.
        """
        headers: dict[str, str] = {}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._device_id:
            headers["X-Device-Id"] = self._device_id
        return headers or None

    async def post_ingest(
        self,
        payload: dict,
        *,
        image_bytes: bytes | None = None,
        thumb_bytes: bytes | None = None,
    ) -> dict:
        """Wire-level POST to /v1/ingest/record with a fully-formed payload.

        Exposed for OutboxSender so it can replay queued entries verbatim
        (the payload was assembled at OutboxBackend.append time).
        """
        return await self._post_ingest(payload, image_bytes=image_bytes, thumb_bytes=thumb_bytes)

    async def _post_ingest(
        self,
        payload: dict,
        *,
        image_bytes: bytes | None,
        thumb_bytes: bytes | None,
    ) -> dict:
        # Metadata is injected at drain time rather than persisted in each
        # outbox entry. This upgrades old queued entries after a client update
        # and keeps client.toml as the single source of device presentation.
        if self._device_id:
            payload = {**payload, "device": self._device_metadata}

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
                self._url("/v1/ingest/record"),
                data=data,
                files=files or None,
                headers=self._extra_headers(),
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"ingest POST failed: {exc}") from exc

        if resp.status_code >= 400:
            raise BackendError(f"ingest POST returned HTTP {resp.status_code}: {resp.text!r}")
        return resp.json()
