"""Native SSH tunnel management for ``type="ssh"`` endpoints.

For an endpoint with ``type="ssh"`` the client maintains an ``ssh -N -L`` local
forward so an otherwise non-public, plain-HTTP backend port becomes reachable —
**and encrypted** — at the endpoint's local ``url``. We shell out to the system
``ssh`` binary (Win10+ ships OpenSSH) so it reuses ``~/.ssh/config``, the agent,
and ``known_hosts`` — zero key handling in our code.

The tunnel is started **up front** and kept alive (restart-on-exit with
backoff), NOT lazily on selection: the EndpointSelector can only pick an
endpoint whose ``/healthz`` probe succeeds, and that needs the tunnel already
up (chicken-and-egg). See infra/PLAN-MULTIPATH-CLIENT.md §5.

Limitation (P2): the set of tunnels is fixed at startup from the
enabled+ssh endpoints. Toggling an ssh endpoint in the tray changes *selection*
(via ``enabled``) but does not start/stop its tunnel process mid-run — a fixed
idle tunnel is harmless; full dynamic lifecycle is a later refinement.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

if TYPE_CHECKING:
    from timetrace.client.core.config import EndpointSection

logger = structlog.get_logger(__name__)

_RESTART_BACKOFF_INITIAL_S = 1.0
_RESTART_BACKOFF_MAX_S = 30.0

# Spawn returns a process-like object exposing wait()/terminate()/kill()/
# returncode/pid. Default uses asyncio subprocess; tests inject a fake.
Spawn = Callable[..., Awaitable[Any]]


def _local_port(url: str) -> int:
    port = urlparse(url).port
    if not port:
        raise ValueError(f"ssh endpoint url must include an explicit port: {url!r}")
    return port


def build_ssh_command(ep: EndpointSection) -> list[str]:
    """``ssh -N -L <local>:<remote_host>:<remote_port> [-p][-i] <host>`` + keepalives.

    ``-o BatchMode=yes`` so a daemon never hangs on a password/passphrase prompt
    (use ssh-agent); ``ExitOnForwardFailure`` so a bind/forward failure exits
    fast and triggers the restart loop instead of a half-open tunnel.
    """
    local = _local_port(ep.url)
    args = [
        "ssh",
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "BatchMode=yes",
        "-L",
        f"{local}:{ep.remote_host}:{ep.remote_port}",
    ]
    if ep.ssh_port and ep.ssh_port != 22:
        args += ["-p", str(ep.ssh_port)]
    if ep.identity_file:
        args += ["-i", ep.identity_file]
    args.append(ep.ssh_host)
    return args


async def _default_spawn(*args: str) -> Any:
    return await asyncio.create_subprocess_exec(
        *args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


async def _sleep_or_stop(stop_event: asyncio.Event, timeout: float) -> bool:
    """Sleep up to ``timeout``; return True if stop fired (so caller returns)."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _wait_proc_or_stop(proc: Any, stop_event: asyncio.Event) -> None:
    stop_task = asyncio.ensure_future(stop_event.wait())
    wait_task = asyncio.ensure_future(proc.wait())
    _, pending = await asyncio.wait({stop_task, wait_task}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()


async def _terminate(proc: Any) -> None:
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001  (already dead / platform quirk)
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:  # noqa: BLE001  (timeout or wait error → escalate to kill)
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


class SshTunnel:
    """Keep one ``ssh -L`` forward alive: spawn, watch, restart-on-exit, stop."""

    def __init__(self, ep: EndpointSection, *, spawn: Spawn = _default_spawn) -> None:
        self._ep = ep
        self._spawn = spawn

    async def run(self, stop_event: asyncio.Event) -> None:
        cmd = build_ssh_command(self._ep)
        backoff = _RESTART_BACKOFF_INITIAL_S
        while not stop_event.is_set():
            try:
                proc = await self._spawn(*cmd)
            except Exception:  # noqa: BLE001
                logger.warning("ssh_tunnel.spawn_failed", name=self._ep.name, exc_info=True)
                if await _sleep_or_stop(stop_event, backoff):
                    return
                backoff = min(backoff * 2, _RESTART_BACKOFF_MAX_S)
                continue

            logger.info(
                "ssh_tunnel.started",
                name=self._ep.name,
                cmd=" ".join(cmd),
                pid=getattr(proc, "pid", None),
            )
            backoff = _RESTART_BACKOFF_INITIAL_S  # reset after a clean spawn
            await _wait_proc_or_stop(proc, stop_event)
            if stop_event.is_set():
                await _terminate(proc)
                return

            logger.warning(
                "ssh_tunnel.exited",
                name=self._ep.name,
                returncode=getattr(proc, "returncode", None),
            )
            if await _sleep_or_stop(stop_event, backoff):
                return
            backoff = min(backoff * 2, _RESTART_BACKOFF_MAX_S)


class SshTunnelManager:
    """Maintain a tunnel for every enabled ``type="ssh"`` endpoint."""

    def __init__(self, endpoints: list[EndpointSection], *, spawn: Spawn = _default_spawn) -> None:
        self._tunnels = [
            SshTunnel(e, spawn=spawn) for e in endpoints if e.type == "ssh" and e.enabled
        ]

    @property
    def count(self) -> int:
        return len(self._tunnels)

    async def run(self, stop_event: asyncio.Event) -> None:
        if not self._tunnels:
            return
        await asyncio.gather(*(t.run(stop_event) for t in self._tunnels))
