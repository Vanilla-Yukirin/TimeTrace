"""EndpointSelector — pick the active backend path from an ordered list.

The client may have several network paths to the *same* backend (LAN direct, an
SSH relay, the public domain). This selects the first ``enabled`` + healthy one
in priority order, and re-probes periodically so it can **upgrade** back to a
higher-priority path when it returns (e.g. LAN when you get home).

Single active endpoint — no load balancing (that would need a different outbox
ack model; see infra/PLAN-MULTIPATH-CLIENT.md §6). Health is a simple
``GET /healthz == 200`` probe; the same probe works for an SSH-tunnel endpoint
once its local forward is up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from timetrace.client.core.config import EndpointSection

logger = structlog.get_logger(__name__)

HealthProbe = Callable[[str], Awaitable[bool]]
_PROBE_TIMEOUT_S = 3.0
_DEFAULT_PROBE_INTERVAL_S = 30.0


async def http_healthz_probe(url: str, *, timeout_s: float = _PROBE_TIMEOUT_S) -> bool:
    """Default probe: ``GET {url}/healthz``; healthy iff HTTP 200.

    Any error (connection refused, timeout, DNS, TLS) → not healthy. The probe
    is deliberately cheap + side-effect-free so it can run every cycle.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(f"{url.rstrip('/')}/healthz")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


class EndpointSelector:
    """Hold an ordered endpoint list + the currently-selected active one.

    Thread-confined to the asyncio loop. ``select()`` (re)probes and updates
    ``current``; ``run()`` calls it on a timer. The sender reads ``current_url``
    per request and calls ``select()`` on a send failure for fast failover.
    """

    def __init__(
        self,
        endpoints: list[EndpointSection],
        *,
        probe: HealthProbe = http_healthz_probe,
        probe_interval_s: float = _DEFAULT_PROBE_INTERVAL_S,
    ) -> None:
        self._endpoints = list(endpoints)  # priority order, first = most preferred
        self._probe = probe
        self._probe_interval = probe_interval_s
        self._current: EndpointSection | None = None
        self._health: dict[str, bool] = {}  # endpoint name -> last probe result
        self._lock = asyncio.Lock()

    @property
    def health(self) -> dict[str, bool]:
        """Snapshot of last-known health per probed endpoint name (for the tray)."""
        return dict(self._health)

    def current(self) -> EndpointSection | None:
        """The active endpoint, or None when nothing healthy is available."""
        return self._current

    def current_url(self) -> str | None:
        return self._current.url if self._current is not None else None

    def _enabled(self) -> list[EndpointSection]:
        return [e for e in self._endpoints if e.enabled]

    async def select(self) -> EndpointSection | None:
        """Probe enabled endpoints in priority order; set ``current`` to the
        first healthy one. Probes all enabled (cheap) so the tray gets full
        health, but the first healthy in priority order always wins."""
        async with self._lock:
            chosen: EndpointSection | None = None
            for ep in self._enabled():
                ok = await self._probe(ep.url)
                self._health[ep.name] = ok
                if ok and chosen is None:
                    chosen = ep
            prev = self._current.name if self._current is not None else None
            self._current = chosen
            if chosen is None:
                logger.warning("endpoint.none_healthy", tried=[e.name for e in self._enabled()])
            elif chosen.name != prev:
                logger.info("endpoint.selected", name=chosen.name, url=chosen.url, previous=prev)
            return chosen

    async def run(self, stop_event: asyncio.Event) -> None:
        """Background loop: re-select every ``probe_interval_s`` so a recovered
        higher-priority endpoint is picked back up. Returns when stop is set."""
        while not stop_event.is_set():
            await self.select()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._probe_interval)
            except asyncio.TimeoutError:
                pass
