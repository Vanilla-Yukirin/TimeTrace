"""``timetrace-client init`` — interactive (or env-driven) first-time setup.

Two modes:

- **Interactive** (default): walks through each `client.toml` field with the
  current value (or env-var override) as the default. Press Enter to accept
  the shown default, or type a new value. Suitable for human first run.
- **Non-interactive** (``--non-interactive``): skips all prompts; writes
  whatever env vars + existing file already provide. Suitable for CI / Ansible
  / Dockerfile / systemd-driven first launch where there is no tty.

Both modes converge on the same write path: load file → overlay env →
optionally fill via prompts → save.

Connectivity test (``--probe``): after save, ``GET <server>/healthz`` to
catch obvious URL typos / wrong port / firewall up front. Off by default
because it adds an httpx import to the cold path.

Design constraint: this module **must not** import the heavy capture stack —
init may be called from environments where pywin32 / mss are unavailable
(Linux dev container, CI). Only ClientConfig + stdlib + httpx (lazy).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from timetrace.client.core.config import ClientConfig

_PRIVACY_MODES = ("off", "text_only", "full")


# --------------------------------------------------------------------- #
# Pure-logic core (testable: callers inject the ask/confirm/print fns)   #
# --------------------------------------------------------------------- #

PromptFn = Callable[[str, str], str]
"""(prompt_text, default_value) → user_input or default."""

ConfirmFn = Callable[[str, bool], bool]
"""(prompt_text, default_yes) → True/False."""


def fill_interactive(
    cfg: ClientConfig,
    ask: PromptFn,
    confirm: ConfirmFn,
) -> ClientConfig:
    """Walk through fields and fill `cfg` from prompts. Returns the same `cfg`.

    Defaults shown are the values already on `cfg` (i.e., file + env layered).
    Empty input keeps the default. Validation is minimal — typos surface as
    runtime errors during real upload, not here.
    """
    cfg.server.url = ask("Server URL", cfg.server.url) or cfg.server.url
    cfg.server.auth_token = (
        ask("Auth token (paste from server admin UI)", cfg.server.auth_token)
        or cfg.server.auth_token
    )

    cfg.device.name = ask("Device name (free-text label)", cfg.device.name) or cfg.device.name
    cfg.device.description = (
        ask("Device description", cfg.device.description) or cfg.device.description
    )

    new_outbox = ask("Outbox directory", str(cfg.outbox.root_dir))
    if new_outbox:
        cfg.outbox.root_dir = Path(new_outbox)

    new_data = ask("Data directory (screenshots / cache)", str(cfg.storage.data_dir))
    if new_data:
        cfg.storage.data_dir = Path(new_data)

    new_kbps = ask(
        "Upload cap KB/s (0 = unlimited)",
        str(cfg.upload.max_kbps),
    )
    if new_kbps:
        try:
            cfg.upload.max_kbps = int(new_kbps)
        except ValueError as exc:
            raise SystemExit(f"upload.max_kbps must be an int, got {new_kbps!r}") from exc

    new_mode = ask(
        f"Privacy mode {_PRIVACY_MODES}",
        cfg.privacy.mode,
    )
    if new_mode:
        if new_mode not in _PRIVACY_MODES:
            raise SystemExit(f"privacy.mode must be one of {_PRIVACY_MODES}, got {new_mode!r}")
        cfg.privacy.mode = new_mode

    cfg.ensure_device_id()
    return cfg


# --------------------------------------------------------------------- #
# CLI surface                                                            #
# --------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Build the ``timetrace-client init`` arg parser.

    Exposed for testability — :func:`run` calls it internally too.
    """
    parser = argparse.ArgumentParser(
        prog="timetrace-client init",
        description="First-time setup for timetrace-client (interactive or env-driven).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to client.toml (default: %USERPROFILE%/TimeTraceData/client.toml)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip prompts; use env vars + existing file values as-is.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing client.toml without confirmation.",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="After save, GET <server>/healthz to verify connectivity.",
    )
    return parser


def run(
    argv: list[str] | None = None,
    *,
    ask: PromptFn | None = None,
    confirm: ConfirmFn | None = None,
    out: Callable[[str], None] | None = None,
) -> int:
    """Entry: parse args, run init flow, write config, return exit code.

    `ask` / `confirm` / `out` injectable for tests; defaults are stdin/stdout.
    Returns 0 on success, non-zero on user-cancel or validation failure.
    """
    args = build_parser().parse_args(argv)

    ask = ask or _default_ask
    confirm = confirm or _default_confirm
    out = out or _default_out

    cfg = ClientConfig.load_or_default(args.config).apply_env_overrides()
    target_path = args.config or _default_path()

    if target_path.exists() and not args.force:
        if not confirm(f"{target_path} exists. Overwrite?", False):
            out("Aborted.")
            return 1

    if not args.non_interactive:
        try:
            fill_interactive(cfg, ask, confirm)
        except (KeyboardInterrupt, EOFError):
            out("\nCancelled.")
            return 130
    else:
        cfg.ensure_device_id()

    saved = cfg.save(args.config)
    out(f"Saved {saved}")
    out(f"  device_id = {cfg.device.id}")
    out(f"  server    = {cfg.server.url}")

    if args.probe:
        rc = _probe_server(cfg.server.url, cfg.server.auth_token, out)
        if rc != 0:
            return rc

    return 0


# --------------------------------------------------------------------- #
# Default I/O implementations                                            #
# --------------------------------------------------------------------- #


def _default_ask(prompt: str, default: str) -> str:
    """Show ``prompt [default]: `` and return the user's line (stripped).

    Empty input returns "" so callers can treat it as "keep default".
    """
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ")
    return raw.strip()


def _default_confirm(prompt: str, default_yes: bool) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    raw = input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def _default_out(line: str) -> None:
    print(line)


def _default_path() -> Path:
    return Path.home() / "TimeTraceData" / "client.toml"


def _probe_server(
    url: str,
    auth_token: str,
    out: Callable[[str], None],
) -> int:
    """Hit /healthz once. Best-effort — don't fail init if probe fails."""
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        out("Probe skipped: httpx not installed.")
        return 0

    try:
        response = httpx.get(url.rstrip("/") + "/healthz", timeout=5.0)
        if response.status_code == 200:
            out(f"Probe OK: {url}/healthz returned 200.")
            return 0
        out(f"Probe WARN: {url}/healthz returned {response.status_code}.")
        return 0
    except Exception as exc:  # noqa: BLE001
        out(f"Probe FAILED: {exc!r}")
        out("(Init still wrote client.toml; fix server URL / firewall and rerun.)")
        return 0


# --------------------------------------------------------------------- #
# Module entry (so `python -m timetrace.client.init_cmd` works)          #
# --------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
