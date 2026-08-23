"""Single owner for mutable standalone-client state and desktop controls."""

from __future__ import annotations

import asyncio
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from timetrace.client.core.config import ClientConfig
from timetrace.client.core.endpoints import EndpointSelector, redact_endpoint_url
from timetrace.client.core.outbox import Outbox, OutboxEntry

logger = structlog.get_logger(__name__)
_UNSET = object()


@dataclass(frozen=True)
class EndpointSnapshot:
    index: int
    name: str
    url: str
    enabled: bool
    healthy: bool | None
    active: bool


@dataclass(frozen=True)
class ClientSnapshot:
    paused: bool
    pause_persisted: bool
    shutdown_requested: bool
    active_endpoint: str | None
    endpoints: tuple[EndpointSnapshot, ...]
    last_capture_at: float | None
    last_upload_at: float | None
    last_error_at: float | None
    last_error: str | None
    outbox_pending: int
    outbox_bytes: int
    outbox_oldest_at: float | None
    control_url: str | None


@dataclass(frozen=True)
class PauseResult:
    paused: bool
    persisted: bool
    warning: str | None = None


class ControllerMutationError(RuntimeError):
    """A requested persistent mutation could not be committed."""


ControlSaver = Callable[[bool, tuple[bool, ...] | None], Any]
BrowserOpener = Callable[[str], Any]


class ClientController:
    """Own mutable config/state on the asyncio thread.

    Tray callbacks never touch config objects directly; they enqueue one of
    the ``*_from_thread`` methods. The HTTP API runs on the same loop and calls
    the async methods directly. A small immutable cache is the only state the
    tray thread reads.
    """

    def __init__(
        self,
        config: ClientConfig,
        outbox: Outbox,
        selector: EndpointSelector,
        quit_event: asyncio.Event,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        save_controls: ControlSaver | None = None,
        clock: Callable[[], float] = time.time,
        browser_opener: BrowserOpener = webbrowser.open,
        stats_ttl_s: float = 5.0,
    ) -> None:
        self.config = config
        self._outbox = outbox
        self._selector = selector
        self._quit_event = quit_event
        self._loop = loop or asyncio.get_running_loop()
        self._save_controls = save_controls or self._default_save_controls
        self._clock = clock
        self._browser_opener = browser_opener
        self._mutation_lock = asyncio.Lock()
        self._stats_lock = asyncio.Lock()
        self._stats_ttl_s = max(0.0, stats_ttl_s)
        self._stats_updated_at = float("-inf")
        self._pause_persisted = True
        self._startup_enabled_ssh = {
            index
            for index, endpoint in enumerate(config.server.endpoints)
            if endpoint.enabled and endpoint.type == "ssh"
        }
        self._last_capture_at: float | None = None
        self._last_upload_at: float | None = None
        self._last_error_at: float | None = None
        self._last_error: str | None = None
        self._shutdown_requested = False
        self._control_url: str | None = None
        self._cache_lock = threading.Lock()
        self._cached = self._snapshot_without_outbox()

    def _default_save_controls(self, paused: bool, enabled: tuple[bool, ...] | None) -> Any:
        return self.config.save_control_state(paused=paused, endpoint_enabled=enabled)

    def _enabled_flags(self) -> tuple[bool, ...]:
        return tuple(endpoint.enabled for endpoint in self.config.server.endpoints)

    async def snapshot(self, *, force_stats: bool = False) -> ClientSnapshot:
        now = time.monotonic()
        if force_stats or now - self._stats_updated_at >= self._stats_ttl_s:
            async with self._stats_lock:
                now = time.monotonic()
                if force_stats or now - self._stats_updated_at >= self._stats_ttl_s:
                    stats = await self._outbox.stats()
                    self._stats_updated_at = now
                else:
                    stats = None
        else:
            stats = None
        snapshot = self._snapshot_without_outbox(
            pending=stats.pending_count if stats else None,
            pending_bytes=stats.pending_bytes if stats else None,
            oldest=(
                stats.oldest_timestamp_ms / 1000 if stats and stats.oldest_timestamp_ms else None
            )
            if stats is not None
            else _UNSET,
        )
        self._set_cache(snapshot)
        return snapshot

    def cached_snapshot(self) -> ClientSnapshot:
        with self._cache_lock:
            return self._cached

    def _snapshot_without_outbox(
        self,
        *,
        pending: int | None = None,
        pending_bytes: int | None = None,
        oldest: float | None | object = _UNSET,
    ) -> ClientSnapshot:
        previous = getattr(self, "_cached", None)
        current = self._selector.current()
        health = self._selector.health
        endpoints = tuple(
            EndpointSnapshot(
                index=index,
                name=endpoint.name,
                url=redact_endpoint_url(endpoint.url),
                enabled=endpoint.enabled,
                healthy=health.get(endpoint.name),
                active=current is endpoint,
            )
            for index, endpoint in enumerate(self.config.server.endpoints)
        )
        return ClientSnapshot(
            paused=self.config.privacy.paused,
            pause_persisted=self._pause_persisted,
            shutdown_requested=self._shutdown_requested,
            active_endpoint=current.name if current is not None else None,
            endpoints=endpoints,
            last_capture_at=self._last_capture_at,
            last_upload_at=self._last_upload_at,
            last_error_at=self._last_error_at,
            last_error=self._last_error,
            outbox_pending=(
                pending if pending is not None else (previous.outbox_pending if previous else 0)
            ),
            outbox_bytes=(
                pending_bytes
                if pending_bytes is not None
                else (previous.outbox_bytes if previous else 0)
            ),
            outbox_oldest_at=(previous.outbox_oldest_at if previous else None)
            if oldest is _UNSET
            else oldest,  # type: ignore[arg-type]
            control_url=self._control_url,
        )

    def _set_cache(self, snapshot: ClientSnapshot | None = None) -> None:
        snapshot = snapshot or self._snapshot_without_outbox()
        with self._cache_lock:
            self._cached = snapshot

    def record_capture(self, _reason: str) -> None:
        self._last_capture_at = self._clock()
        self._set_cache()

    def record_upload(self, _entry: OutboxEntry) -> None:
        self._last_upload_at = self._clock()
        self._set_cache()

    def record_send_error(self, _entry: OutboxEntry, error: Exception) -> None:
        self.record_error("upload", error)

    def record_error(self, source: str, error: BaseException) -> None:
        self._last_error_at = self._clock()
        # Do not expose exception strings: HTTP errors can contain URLs or
        # payload fragments. The detailed exception remains in structured logs.
        self._last_error = f"{source}: {type(error).__name__}"
        self._set_cache()

    async def set_paused(self, paused: bool) -> PauseResult:
        """Persist a privacy-safe pause transition.

        Pausing applies immediately and stays paused even if persistence fails.
        Resuming is the opposite: persist first, then resume, so a disk error can
        never accidentally re-enable capture.
        """
        async with self._mutation_lock:
            if self.config.privacy.paused == paused and self._pause_persisted:
                return PauseResult(paused=paused, persisted=True)
            if paused:
                self.config.privacy.paused = True
                self._set_cache()
                try:
                    await asyncio.to_thread(self._save_controls, True, None)
                except Exception as exc:  # noqa: BLE001
                    self._pause_persisted = False
                    self.record_error("config_save", exc)
                    logger.warning("client.pause_save_failed", exc_info=True)
                    return PauseResult(
                        paused=True,
                        persisted=False,
                        warning="Paused for this session, but client.toml could not be updated.",
                    )
                self._pause_persisted = True
                self._set_cache()
                return PauseResult(paused=True, persisted=True)

            try:
                await asyncio.to_thread(self._save_controls, False, None)
            except Exception as exc:  # noqa: BLE001
                self._pause_persisted = False
                self.record_error("config_save", exc)
                logger.warning("client.resume_save_failed", exc_info=True)
                return PauseResult(
                    paused=True,
                    persisted=False,
                    warning="Still paused because client.toml could not be updated.",
                )
            self.config.privacy.paused = False
            self._pause_persisted = True
            self._set_cache()
            return PauseResult(paused=False, persisted=True)

    async def set_endpoint_enabled(self, index: int, enabled: bool) -> ClientSnapshot:
        async with self._mutation_lock:
            endpoints = self.config.server.endpoints
            if index < 0 or index >= len(endpoints):
                raise ControllerMutationError("endpoint not found")
            if (
                enabled
                and not endpoints[index].enabled
                and endpoints[index].type == "ssh"
                and index not in self._startup_enabled_ssh
            ):
                raise ControllerMutationError(
                    "restart the client after enabling an SSH endpoint in client.toml"
                )
            desired = list(self._enabled_flags())
            desired[index] = enabled
            if not any(desired):
                raise ControllerMutationError("at least one endpoint must remain enabled")
            if tuple(desired) == self._enabled_flags():
                return await self.snapshot()
            try:
                await asyncio.to_thread(
                    self._save_controls,
                    self.config.privacy.paused,
                    tuple(desired),
                )
            except Exception as exc:  # noqa: BLE001
                self.record_error("config_save", exc)
                raise ControllerMutationError("client.toml could not be updated") from exc
            # The same atomic control-state write also persisted the current
            # pause value, including recovery from a prior pause-save failure.
            self._pause_persisted = True
            endpoints[index].enabled = enabled
            await self._selector.select()
            return await self.snapshot()

    def set_control_url(self, url: str | None) -> None:
        self._control_url = url
        self._set_cache()

    async def open_control_panel(self) -> bool:
        url = self._control_url
        if not url:
            return False
        try:
            return bool(await asyncio.to_thread(self._browser_opener, url))
        except Exception as exc:  # noqa: BLE001
            self.record_error("browser", exc)
            logger.warning("client.control_browser_failed", exc_info=True)
            return False

    def request_shutdown(self) -> bool:
        if self._shutdown_requested:
            return False
        self._shutdown_requested = True
        self._set_cache()
        self._quit_event.set()
        return True

    async def refresh_loop(self, stop_event: asyncio.Event, *, interval_s: float = 2.0) -> None:
        while not stop_event.is_set():
            try:
                await self.snapshot()
            except Exception as exc:  # noqa: BLE001
                self.record_error("status", exc)
                logger.warning("client.status_refresh_failed", exc_info=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass

    def _submit_from_thread(self, coroutine: Any) -> None:
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:
            coroutine.close()
            return

        def _log_failure(done: Any) -> None:
            try:
                done.result()
            except Exception:  # noqa: BLE001
                logger.warning("client.tray_action_failed", exc_info=True)

        future.add_done_callback(_log_failure)

    def set_paused_from_thread(self, paused: bool) -> None:
        self._submit_from_thread(self.set_paused(paused))

    def set_endpoint_enabled_from_thread(self, index: int, enabled: bool) -> None:
        self._submit_from_thread(self.set_endpoint_enabled(index, enabled))

    def open_control_panel_from_thread(self) -> None:
        self._submit_from_thread(self.open_control_panel())

    def request_shutdown_from_thread(self) -> None:
        try:
            self._loop.call_soon_threadsafe(self.request_shutdown)
        except RuntimeError:
            pass
