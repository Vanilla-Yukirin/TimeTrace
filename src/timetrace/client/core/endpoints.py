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
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog

if TYPE_CHECKING:
    from timetrace.client.core.config import EndpointSection

logger = structlog.get_logger(__name__)

HealthProbe = Callable[[str], Awaitable[bool]]
# Generous so a slow-but-alive remote (e.g. a laggy cross-region path) isn't
# falsely marked down by a tight probe window.
_PROBE_TIMEOUT_S = 8.0
_DEFAULT_PROBE_INTERVAL_S = 30.0


def redact_endpoint_url(url: str) -> str:
    """Return only scheme + host + port for display/logging."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "<invalid endpoint URL>"
    if ":" in hostname:
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{port}" if port is not None else hostname
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


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

    async def select(self, *, upgrade_only: bool = False) -> EndpointSection | None:
        """Probe enabled endpoints in priority order; set ``current`` to the
        first healthy one. First healthy in priority order wins.

        Two rules that make this robust on a flaky/slow link:

        - **Don't probe the current endpoint** (``upgrade_only=True``, the
          periodic check): its liveness is already proven by whether sends
          succeed, so re-probing wastes the (scarce) uplink and a short healthz
          timeout can falsely fail mid-upload. We only probe *higher*-priority
          endpoints — to decide whether to upgrade back (e.g. LAN at home).
          ``upgrade_only=False`` (startup + on a send failure) probes all to find
          any working path.
        - **Sticky**: if nothing probes healthy, KEEP current (when still
          enabled) instead of blackholing sends to None — the send's own retry is
          the real liveness test. Only drop to None with no current to keep."""
        async with self._lock:
            enabled = self._enabled()
            to_probe = enabled
            if upgrade_only and self._current is not None:
                # Only endpoints strictly higher-priority than current.
                to_probe = []
                for ep in enabled:
                    if ep.name == self._current.name:
                        break
                    to_probe.append(ep)

            chosen: EndpointSection | None = None
            for ep in to_probe:
                ok = await self._probe(ep.url)
                self._health[ep.name] = ok
                if ok and chosen is None:
                    chosen = ep

            prev = self._current.name if self._current is not None else None
            if chosen is not None:
                self._current = chosen
                if chosen.name != prev:
                    logger.info("endpoint.selected", name=chosen.name, previous=prev)
            elif self._current is not None and self._current.name in {e.name for e in enabled}:
                # Keep current. Silent during the periodic upgrade check (steady
                # state); only warn on a full probe (a real send-failure probe).
                if not upgrade_only:
                    logger.warning(
                        "endpoint.probe_miss_keeping_current", current=self._current.name
                    )
            else:
                self._current = None
                logger.warning("endpoint.none_healthy", tried=[e.name for e in enabled])
            return self._current

    async def run(self, stop_event: asyncio.Event) -> None:
        """Background loop: every ``probe_interval_s`` check only whether a
        higher-priority endpoint came back (upgrade), without re-probing the
        current one. Returns when stop is set."""
        while not stop_event.is_set():
            await self.select(upgrade_only=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._probe_interval)
            except asyncio.TimeoutError:
                pass
