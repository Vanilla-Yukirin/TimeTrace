from __future__ import annotations

import signal

from timetrace.server import cli


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
