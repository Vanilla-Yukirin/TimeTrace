"""Circuit breaker for VLM calls: HEALTHY ↔ SLEEPING with heartbeat probe."""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from timetrace.server.vlm.client import VLMClient

logger = structlog.get_logger(__name__)


class GateState(str, Enum):
    HEALTHY = "healthy"
    SLEEPING = "sleeping"


class VLMHealthGate:
    """Cross-worker shared gate. Workers call `acquire()` before each VLM call.

    - HEALTHY: acquire() returns True immediately.
    - On `fail_threshold` consecutive failures, transition to SLEEPING.
    - SLEEPING: acquire() returns False until a heartbeat probe (one at a time)
      succeeds `recover_threshold` consecutive times. Probes are paced by
      `probe_interval_s`.
    """

    def __init__(
        self,
        client: VLMClient,
        *,
        fail_threshold: int = 3,
        recover_threshold: int = 3,
        probe_interval_s: float = 30.0,
        clock: callable = time.monotonic,
    ) -> None:
        self._client = client
        self._fail_threshold = fail_threshold
        self._recover_threshold = recover_threshold
        self._probe_interval_s = probe_interval_s
        self._clock = clock

        self._state = GateState.HEALTHY
        self._consecutive_failures = 0
        self._consecutive_probe_successes = 0
        self._next_probe_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> GateState:
        return self._state

    @property
    def probe_interval_s(self) -> float:
        return self._probe_interval_s

    async def acquire(self) -> bool:
        """Return True if VLM is currently usable; otherwise False (gate is sleeping)."""
        async with self._lock:
            if self._state == GateState.HEALTHY:
                return True
            now = self._clock()
            if now < self._next_probe_at:
                return False
            self._next_probe_at = now + self._probe_interval_s

        # Probe outside the lock so concurrent acquires while we're awaiting
        # the network just see "sleeping + cooldown not elapsed" and bail.
        ok = await self._client.heartbeat()
        async with self._lock:
            if ok:
                self._consecutive_probe_successes += 1
                logger.info(
                    "vlm.heartbeat_ok",
                    consecutive=self._consecutive_probe_successes,
                    threshold=self._recover_threshold,
                )
                if self._consecutive_probe_successes >= self._recover_threshold:
                    self._transition(GateState.HEALTHY)
                    return True
            else:
                self._consecutive_probe_successes = 0
                logger.info("vlm.heartbeat_fail")
            return False

    async def report_success(self) -> None:
        async with self._lock:
            self._consecutive_failures = 0

    async def report_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            if (
                self._state == GateState.HEALTHY
                and self._consecutive_failures >= self._fail_threshold
            ):
                # Schedule the first probe one full interval out so we don't
                # immediately hammer the failing endpoint.
                self._next_probe_at = self._clock() + self._probe_interval_s
                self._transition(GateState.SLEEPING)

    def _transition(self, new_state: GateState) -> None:
        if new_state == self._state:
            return
        logger.warning(
            "vlm_gate.transition",
            from_state=self._state.value,
            to_state=new_state.value,
            consecutive_failures=self._consecutive_failures,
            consecutive_probe_successes=self._consecutive_probe_successes,
        )
        self._state = new_state
        if new_state == GateState.HEALTHY:
            self._consecutive_failures = 0
            self._consecutive_probe_successes = 0
        elif new_state == GateState.SLEEPING:
            self._consecutive_probe_successes = 0
