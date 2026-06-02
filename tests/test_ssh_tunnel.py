"""Native SSH tunnel (P2): command construction + spawn/watch/restart/stop
lifecycle, all with a fake process (no real ssh)."""

from __future__ import annotations

import asyncio

import pytest

from timetrace.client.core import ssh_tunnel
from timetrace.client.core.config import EndpointSection
from timetrace.client.core.ssh_tunnel import SshTunnel, SshTunnelManager, build_ssh_command


def _ssh_ep(**kw) -> EndpointSection:
    base = dict(
        name="jp",
        url="http://127.0.0.1:19765",
        type="ssh",
        ssh_host="jp",
        remote_host="127.0.0.1",
        remote_port=18765,
    )
    base.update(kw)
    return EndpointSection(**base)


# --------------------------------------------------------------------------- #
# Command construction                                                          #
# --------------------------------------------------------------------------- #


def test_build_ssh_command_basic():
    cmd = build_ssh_command(_ssh_ep())
    assert cmd[0] == "ssh"
    assert "-N" in cmd
    assert cmd[-1] == "jp"  # host is last
    i = cmd.index("-L")
    assert cmd[i + 1] == "19765:127.0.0.1:18765"
    assert "BatchMode=yes" in cmd  # daemon never prompts
    assert "ExitOnForwardFailure=yes" in cmd
    # default port 22 → no -p; no identity → no -i
    assert "-p" not in cmd
    assert "-i" not in cmd


def test_build_ssh_command_with_port_and_identity():
    cmd = build_ssh_command(_ssh_ep(ssh_host="u@h", ssh_port=2222, identity_file="/k.pem"))
    assert cmd[cmd.index("-p") + 1] == "2222"
    assert cmd[cmd.index("-i") + 1] == "/k.pem"
    assert cmd[-1] == "u@h"


def test_build_ssh_command_requires_local_port():
    with pytest.raises(ValueError, match="explicit port"):
        build_ssh_command(_ssh_ep(url="http://127.0.0.1"))


# --------------------------------------------------------------------------- #
# Lifecycle (fake process)                                                      #
# --------------------------------------------------------------------------- #


class _FakeProc:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._exited = asyncio.Event()

    async def wait(self) -> int | None:
        await self._exited.wait()
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self._exited.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._exited.set()

    def exit(self, rc: int = 0) -> None:
        self.returncode = rc
        self._exited.set()


async def _until(cond, timeout: float = 2.0) -> None:
    waited = 0.0
    while waited < timeout:
        if cond():
            return
        await asyncio.sleep(0.01)
        waited += 0.01
    raise AssertionError("condition not met within timeout")


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(ssh_tunnel, "_RESTART_BACKOFF_INITIAL_S", 0.01)
    monkeypatch.setattr(ssh_tunnel, "_RESTART_BACKOFF_MAX_S", 0.01)


async def test_tunnel_restarts_on_unexpected_exit():
    procs: list[_FakeProc] = []

    async def spawn(*_args):
        p = _FakeProc()
        procs.append(p)
        return p

    tunnel = SshTunnel(_ssh_ep(), spawn=spawn)
    stop = asyncio.Event()
    task = asyncio.create_task(tunnel.run(stop))

    await _until(lambda: len(procs) >= 1)
    procs[0].exit(1)  # tunnel dies unexpectedly → should respawn
    await _until(lambda: len(procs) >= 2)

    stop.set()  # shutdown terminates the live one
    await asyncio.wait_for(task, timeout=2)
    assert procs[-1].terminated


async def test_tunnel_stop_terminates_process():
    procs: list[_FakeProc] = []

    async def spawn(*_args):
        p = _FakeProc()
        procs.append(p)
        return p

    tunnel = SshTunnel(_ssh_ep(), spawn=spawn)
    stop = asyncio.Event()
    task = asyncio.create_task(tunnel.run(stop))
    await _until(lambda: len(procs) >= 1)

    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert procs[0].terminated
    assert len(procs) == 1  # not restarted after a clean stop


async def test_manager_filters_to_enabled_ssh_only():
    async def spawn(*_args):
        return _FakeProc()

    eps = [
        EndpointSection(name="http", url="http://h", type="http"),
        EndpointSection(
            name="ssh-off", url="http://127.0.0.1:1", type="ssh", enabled=False, remote_port=2
        ),
        EndpointSection(name="ssh-on", url="http://127.0.0.1:3", type="ssh", remote_port=4),
    ]
    assert SshTunnelManager(eps, spawn=spawn).count == 1


async def test_manager_empty_returns_immediately():
    mgr = SshTunnelManager([])
    assert mgr.count == 0
    await asyncio.wait_for(mgr.run(asyncio.Event()), timeout=1)
