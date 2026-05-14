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
from collections.abc import Awaitable, Callable

import structlog

from timetrace.client.core.outbox import Outbox, OutboxEntry

logger = structlog.get_logger(__name__)

# Sender contract: receives one OutboxEntry, returns nothing on success,
# raises any exception on failure.
SendCallable = Callable[[OutboxEntry], Awaitable[None]]


class OutboxSender:
    """Drain an Outbox in submission order, retrying with backoff on failure."""

    def __init__(
        self,
        outbox: Outbox,
        send: SendCallable,
        *,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 60.0,
        idle_poll_interval_s: float = 5.0,
    ) -> None:
        self._outbox = outbox
        self._send = send
        self._backoff_initial = backoff_initial_s
        self._backoff_max = backoff_max_s
        self._idle_interval = idle_poll_interval_s

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
