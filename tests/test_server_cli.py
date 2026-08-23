from __future__ import annotations

import asyncio
import signal

import uvicorn

from timetrace.server import cli
from timetrace.server.bootstrap import (
    _UVICORN_GRACEFUL_SHUTDOWN_S,
    _CoordinatedServer,
)


def test_daemon_registers_sigint_and_sigterm(monkeypatch):
    registered: list[signal.Signals] = []

    async def fake_run(config, quit_event):
        return None

    monkeypatch.setattr(cli.sys, "argv", ["timetrace-server"])
    monkeypatch.setattr(cli, "AppConfig", object)
    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(
        cli.signal,
        "signal",
        lambda shutdown_signal, handler: registered.append(shutdown_signal),
    )

    cli.main()

    assert registered == [signal.SIGINT, signal.SIGTERM]


def test_uvicorn_signal_wakes_coordinated_shutdown_immediately():
    quit_event = asyncio.Event()
    config = uvicorn.Config(
        object(),
        timeout_graceful_shutdown=_UVICORN_GRACEFUL_SHUTDOWN_S,
    )
    server = _CoordinatedServer(config, quit_event)

    # This is the handler Uvicorn installs inside Server.serve(), after the
    # CLI-level handlers exercised above have been replaced.
    server.handle_exit(signal.SIGTERM, None)

    assert quit_event.is_set()
    assert server.should_exit is True
    assert server._captured_signals == [signal.SIGTERM]
    assert server.config.timeout_graceful_shutdown == 30
