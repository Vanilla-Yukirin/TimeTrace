"""Startup-boundary tests for the standalone client daemon."""

from __future__ import annotations

import sys

import pytest

from timetrace.client import cli
from timetrace.client.core import config as config_module


def test_daemon_rejects_invalid_effective_identity_before_runtime_or_network(tmp_path, monkeypatch):
    config_path = tmp_path / "client.toml"
    config_path.write_text('[device]\nid = "file-invalid"\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "_DEFAULT_PATH", config_path)
    monkeypatch.delenv("TIMETRACE_DEVICE_ID", raising=False)
    monkeypatch.delenv("TIMETRACE_DEVICE_NAME", raising=False)
    monkeypatch.delenv("TIMETRACE_DEVICE_DESC", raising=False)
    monkeypatch.setattr(sys, "argv", ["timetrace-client"])

    def must_not_start_runtime():
        pytest.fail("invalid effective configuration must fail before daemon/network startup")

    monkeypatch.setattr(cli.asyncio, "new_event_loop", must_not_start_runtime)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2
