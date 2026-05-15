"""Background task that drains an Outbox by handing each entry to a sender
callable, with exponential backoff on failure.

The sender callable is supplied at construction so the OutboxSender doesn't
need to know about HTTP, BackendClient, or any specific transport. In
production it's bound to ``HttpBackend._post_ingest``-shaped logic; in tests
it's a stub that tracks invocations.

Loop semantics:

- Strict FIFO: a failing entry is retried (with backoff) until it either
  succeeds and gets acked, or the stop event fires. The loop never skips
  a failing entry to try the next — that's the whole point of the outbox.

  **Load-bearing for OutboxBackend**: capture's submit_record →
  submit_screenshot → close_record sequence reaches the outbox in that
  order, and the server expects the same order on the wire (close hits
  ``/v1/ingest/record/{id}/close``, which only succeeds once the matching
  record exists). Strict FIFO gives us that ordering for free. If a future
  refactor introduces parallel senders sharing one outbox, that invariant
  breaks and either close needs to re-route through the by-client-id
  fallback every time (extra SELECT per close) or sender needs explicit
  per-record_id sequencing.

- Backoff: doubles after each consecutive failure, capped at ``backoff_max_s``.
  Resets on success.
- Idle wait: when the queue is empty the loop sleeps ``idle_poll_interval_s``
  between checks. (No filesystem watcher today; a future refinement could
  wake the loop synchronously when ``Outbox.append`` is called from the same
  process.)
- Stop: either pass an asyncio.Event and set it, or cancel the task. Both
  cause `run` to return cleanly.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import structlog

from timetrace.client.core.outbox import Outbox, OutboxEntry

logger = structlog.get_logger(__name__)

# Sender contract: receives one OutboxEntry, returns nothing on success,
# raises any exception on failure.
SendCallable = Callable[[OutboxEntry], Awaitable[None]]


class _TokenBucket:
    """Pre-emptive rate limiter: ``await consume(n)`` blocks until n bytes of
    budget are available. ``rate_kbps == 0`` means unlimited (the consume call
    is a no-op so the limiter has zero overhead when disabled)."""

    def __init__(self, rate_kbps: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate_bytes_per_sec = max(0, rate_kbps) * 1024
        self._tokens: float = float(self._rate_bytes_per_sec)  # initial 1-second burst budget
        self._clock = clock
        self._last_refill = clock()

    @property
    def enabled(self) -> bool:
        return self._rate_bytes_per_sec > 0

    async def consume(self, byte_count: int) -> None:
        if not self.enabled or byte_count <= 0:
            return
        now = self._clock()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._tokens + elapsed * self._rate_bytes_per_sec,
            float(self._rate_bytes_per_sec),
        )
        self._last_refill = now
        if self._tokens >= byte_count:
            self._tokens -= byte_count
            return
        deficit = byte_count - self._tokens
        wait_s = deficit / self._rate_bytes_per_sec
        await asyncio.sleep(wait_s)
        self._tokens = 0.0
        self._last_refill = self._clock()


def _entry_size_bytes(entry: OutboxEntry) -> int:
    """Best-effort bytes-on-the-wire estimate for the rate limiter.

    Uses raw image + thumb byte lengths and a small fixed overhead for the
    JSON record envelope. Doesn't account for HTTP framing, but the limiter's
    job is approximate fairness, not precise wire accounting.
    """
    size = 256  # heuristic for JSON envelope + multipart boundaries
    if entry.image_bytes is not None:
        size += len(entry.image_bytes)
    if entry.thumb_bytes is not None:
        size += len(entry.thumb_bytes)
    return size


class OutboxSender:
    """Drain an Outbox in submission order, retrying with backoff on failure.

    With ``max_kbps`` set above 0, each successful send first waits on a
    token bucket so the per-second upload doesn't exceed the cap. ``0`` keeps
    the limiter dormant.
    """

    def __init__(
        self,
        outbox: Outbox,
        send: SendCallable,
        *,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 60.0,
        idle_poll_interval_s: float = 5.0,
        max_kbps: int = 0,
    ) -> None:
        self._outbox = outbox
        self._send = send
        self._backoff_initial = backoff_initial_s
        self._backoff_max = backoff_max_s
        self._idle_interval = idle_poll_interval_s
        self._bucket = _TokenBucket(max_kbps)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Main drain loop. Returns when `stop_event` is set or task is cancelled."""
        stop_event = stop_event or asyncio.Event()
        try:
            while not stop_event.is_set():
                drained = await self._drain_pending(stop_event)
                if drained == 0 and not stop_event.is_set():
                    # Nothing to do — wait either for the idle interval or for
                    # someone to flip the stop event, whichever comes first.
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=self._idle_interval)
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            logger.info("outbox_sender.cancelled")
            raise

    async def _drain_pending(self, stop_event: asyncio.Event) -> int:
        """Drain everything currently in the outbox; return count successfully sent."""
        sent = 0
        async for entry in self._outbox.iter_pending():
            if stop_event.is_set():
                return sent
            # Pre-emptive bandwidth gate: wait BEFORE the network attempt so the
            # bytes we're about to send fit inside the cap. No-op when the
            # bucket is disabled (max_kbps=0).
            await self._bucket.consume(_entry_size_bytes(entry))
            if stop_event.is_set():
                return sent
            await self._send_with_backoff(entry, stop_event)
            if stop_event.is_set():
                return sent
            await self._outbox.ack_next(entry.entry_id)
            sent += 1
        return sent

    async def _send_with_backoff(self, entry: OutboxEntry, stop_event: asyncio.Event) -> None:
        """Try to send `entry`, retrying with exponential backoff on failure.

        Returns once the send succeeded OR the stop event fired (in which case
        the entry remains pending for next start). Strict FIFO means we never
        give up on an entry — at-least-once + idempotent server is the
        durability story.
        """
        attempt = 0
        backoff = self._backoff_initial
        while True:
            try:
                await self._send(entry)
                if attempt > 0:
                    logger.info(
                        "outbox_sender.recovered",
                        entry_id=entry.entry_id,
                        attempts=attempt + 1,
                    )
                return
            except Exception as exc:  # noqa: BLE001
                attempt += 1
                logger.warning(
                    "outbox_sender.send_failed",
                    entry_id=entry.entry_id,
                    attempt=attempt,
                    backoff_s=backoff,
                    error=str(exc),
                )
                # Wait with cancellable sleep that wakes on stop too.
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    # stop fired during backoff
                    return
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, self._backoff_max)
