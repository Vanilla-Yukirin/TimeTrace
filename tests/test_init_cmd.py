"""Tests for ``timetrace-client init`` (init_cmd module).

Covers the pure-logic core (`fill_interactive`) by injecting scripted
ask/confirm functions, plus the high-level `run()` entry by isolating
filesystem to tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from timetrace.client.core.config import ClientConfig
from timetrace.client.init_cmd import fill_interactive, run

# --------------------------------------------------------------------- #
# Helpers — scripted ask/confirm/out                                     #
# --------------------------------------------------------------------- #


class _Scripted:
    """Replays a list of pre-canned answers in order; raises if asked too much."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.prompts: list[tuple[str, str]] = []

    def ask(self, prompt: str, default: str) -> str:
        self.prompts.append((prompt, default))
        if not self._answers:
            raise AssertionError(f"unexpected extra prompt: {prompt!r}")
        return self._answers.pop(0)

    def confirm(self, prompt: str, default_yes: bool) -> bool:
        if not self._answers:
            raise AssertionError(f"unexpected extra confirm: {prompt!r}")
        ans = self._answers.pop(0)
        if ans == "":
            return default_yes
        return ans.lower() in ("y", "yes")


class _Capture:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)


# --------------------------------------------------------------------- #
# fill_interactive — pure logic                                          #
# --------------------------------------------------------------------- #


def test_fill_interactive_keeps_defaults_on_empty_input():
    cfg = ClientConfig()
    cfg.server.url = "http://existing"
    cfg.server.auth_token = "tt_existing"
    cfg.device.name = "old-device"
    cfg.device.description = "old-desc"
    cfg.upload.max_kbps = 100

    scripted = _Scripted(["", "", "", "", "", "", "", ""])
    result = fill_interactive(cfg, scripted.ask, scripted.confirm)

    assert result.server.url == "http://existing"
    assert result.server.auth_token == "tt_existing"
    assert result.device.name == "old-device"
    assert result.device.description == "old-desc"
    assert result.upload.max_kbps == 100
    assert result.device.id  # ensure_device_id minted one


def test_fill_interactive_overrides_with_typed_input():
    cfg = ClientConfig()
    scripted = _Scripted(
        [
            "https://new",  # server.url
            "tt_live_new",  # auth_token
            "Yuki-Box",  # device.name
            "headless small box",  # device.description
            str(Path("/tmp/ob")),  # outbox.root_dir
            str(Path("/tmp/data")),  # storage.data_dir
            "512",  # upload.max_kbps
            "full",  # privacy.mode
        ]
    )
    result = fill_interactive(cfg, scripted.ask, scripted.confirm)
    assert result.server.url == "https://new"
    assert result.server.auth_token == "tt_live_new"
    assert result.device.name == "Yuki-Box"
    assert result.device.description == "headless small box"
    assert result.outbox.root_dir == Path("/tmp/ob")
    assert result.storage.data_dir == Path("/tmp/data")
    assert result.upload.max_kbps == 512
    assert result.privacy.mode == "full"


def test_fill_interactive_rejects_invalid_privacy_mode():
    cfg = ClientConfig()
    scripted = _Scripted(["", "", "", "", "", "", "", "bogus_mode"])
    with pytest.raises(SystemExit, match="privacy.mode"):
        fill_interactive(cfg, scripted.ask, scripted.confirm)


def test_fill_interactive_rejects_non_int_max_kbps():
    cfg = ClientConfig()
    scripted = _Scripted(["", "", "", "", "", "", "abc"])
    with pytest.raises(SystemExit, match="max_kbps"):
        fill_interactive(cfg, scripted.ask, scripted.confirm)


def test_fill_interactive_rejects_oversize_device_name_before_other_prompts():
    cfg = ClientConfig()
    scripted = _Scripted(["", "", "n" * 129, ""])
    with pytest.raises(SystemExit, match=r"device\.name.*128"):
        fill_interactive(cfg, scripted.ask, scripted.confirm)
    assert len(scripted.prompts) == 4


def test_fill_interactive_mints_device_id_once():
    cfg = ClientConfig()
    scripted = _Scripted(["", "", "", "", "", "", "", ""])
    result = fill_interactive(cfg, scripted.ask, scripted.confirm)
    first_id = result.device.id

    # Run again with new scripted answers — id must persist.
    scripted = _Scripted(["", "", "", "", "", "", "", ""])
    result = fill_interactive(result, scripted.ask, scripted.confirm)
    assert result.device.id == first_id


# --------------------------------------------------------------------- #
# run() — high-level entry                                               #
# --------------------------------------------------------------------- #


def test_run_non_interactive_writes_file_with_env_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMETRACE_SERVER_URL", "http://env-server:9000")
    monkeypatch.setenv("TIMETRACE_AUTH_TOKEN", "tt_live_envtoken")
    out = _Capture()
    target = tmp_path / "client.toml"

    rc = run(
        ["--config", str(target), "--non-interactive"],
        ask=lambda p, d: pytest.fail("must not prompt in non-interactive mode"),
        confirm=lambda p, d: pytest.fail("must not confirm in non-interactive mode"),
        out=out,
    )
    assert rc == 0
    assert target.exists()
    loaded = ClientConfig.load_or_default(target)
    assert loaded.server.url == "http://env-server:9000"
    assert loaded.server.auth_token == "tt_live_envtoken"
    assert loaded.device.id  # minted


def test_run_aborts_when_existing_file_and_user_declines_overwrite(tmp_path):
    target = tmp_path / "client.toml"
    target.write_text('[server]\nurl = "http://untouched"\n')

    out = _Capture()
    scripted = _Scripted(["n"])  # decline overwrite
    rc = run(
        ["--config", str(target)],
        ask=scripted.ask,
        confirm=scripted.confirm,
        out=out,
    )
    assert rc == 1
    # File unchanged
    assert "http://untouched" in target.read_text()


def test_run_force_overwrites_without_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMETRACE_SERVER_URL", "http://newer")
    target = tmp_path / "client.toml"
    target.write_text('[server]\nurl = "http://older"\n')

    out = _Capture()
    rc = run(
        ["--config", str(target), "--force", "--non-interactive"],
        ask=lambda p, d: pytest.fail("--force should bypass confirm"),
        confirm=lambda p, d: pytest.fail("--force should bypass confirm"),
        out=out,
    )
    assert rc == 0
    loaded = ClientConfig.load_or_default(target)
    assert loaded.server.url == "http://newer"


def test_run_interactive_keeps_existing_file_values_as_defaults(tmp_path, monkeypatch):
    """Existing file → load → env override → prompts seeded with that."""
    monkeypatch.delenv("TIMETRACE_SERVER_URL", raising=False)

    target = tmp_path / "client.toml"
    pre = ClientConfig()
    pre.server.url = "http://from-file"
    pre.server.auth_token = "tt_from_file"
    pre.save(target)

    captured_defaults: list[tuple[str, str]] = []

    def ask(prompt: str, default: str) -> str:
        captured_defaults.append((prompt, default))
        return ""  # keep all defaults

    out = _Capture()
    scripted = _Scripted(["y"])  # confirm overwrite
    rc = run(
        ["--config", str(target)],
        ask=ask,
        confirm=scripted.confirm,
        out=out,
    )
    assert rc == 0
    # The first prompt's default was the existing file value
    assert captured_defaults[0] == ("Server URL", "http://from-file")
    loaded = ClientConfig.load_or_default(target)
    assert loaded.server.url == "http://from-file"
    assert loaded.server.auth_token == "tt_from_file"


def test_run_writes_device_id_in_non_interactive(tmp_path):
    target = tmp_path / "client.toml"
    out = _Capture()
    rc = run(
        ["--config", str(target), "--non-interactive"],
        out=out,
    )
    assert rc == 0
    loaded = ClientConfig.load_or_default(target)
    assert loaded.device.id  # minted on save
    # Output reports it
    joined = "\n".join(out.lines)
    assert "device_id" in joined
    assert loaded.device.id in joined


def test_run_non_interactive_reports_invalid_device_env_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMETRACE_DEVICE_NAME", "n" * 129)
    target = tmp_path / "client.toml"
    out = _Capture()
    rc = run(["--config", str(target), "--non-interactive"], out=out)
    assert rc == 2
    assert not target.exists()
    assert "device.name" in "\n".join(out.lines)
