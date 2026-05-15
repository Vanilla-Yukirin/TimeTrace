"""``timetrace-server tokens ...`` / ``info`` admin subcommands.

Surface for the small set of out-of-band operations that don't need an HTTP
endpoint: mint / list / revoke bearer tokens, print resolved paths.

Why a CLI instead of a `/v1/admin/tokens` API:

- Bootstrap chicken-and-egg: the very first token has to be created without
  one already existing
- The small machine admin (= me, ssh'd into the box) is exactly the person
  who needs these operations; not worth designing browser auth for it yet
- Token CRUD touches a single JSON file with no concurrency from the running
  server (server only reads at startup; writes happen here while server is
  up but a reload just picks them up next boot)

The commands here intentionally **do not** hot-reload the running server.
After `tokens add` / `tokens revoke`, restart the systemd unit so the
in-memory token set picks up the change. Keeps the file→memory sync logic
in one place (load_or_generate at startup), avoids file-watcher complexity.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from timetrace.common.config import AppConfig
from timetrace.server.auth import (
    _DEFAULT_TOKEN_DIR,  # noqa: PLC2701  intentional admin reach-in
    _TOKEN_FILE_NAME,
    _TOKEN_PREFIX,
    TokenEntry,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timetrace-server",
        description="Server admin commands (token mgmt, info dump).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="Print resolved paths and listen addr.")

    tok = sub.add_parser("tokens", help="Manage bearer tokens.")
    tok_sub = tok.add_subparsers(dest="tok_cmd", required=True)

    tok_sub.add_parser("list", help="List tokens (full values masked).")

    add_p = tok_sub.add_parser("add", help="Mint a new token. Prints the full value ONCE.")
    add_p.add_argument("label", help="Free-text label, e.g. device name.")

    rev_p = tok_sub.add_parser("revoke", help="Revoke by label OR by token-prefix match.")
    rev_p.add_argument("identifier", help="Label, or full token, or token's last 8 chars.")

    return parser


def run(
    argv: list[str] | None = None,
    *,
    out: Callable[[str], None] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    out = out or print

    if args.cmd == "info":
        return _cmd_info(out)
    if args.cmd == "tokens":
        if args.tok_cmd == "list":
            return _cmd_tokens_list(out)
        if args.tok_cmd == "add":
            return _cmd_tokens_add(args.label, out)
        if args.tok_cmd == "revoke":
            return _cmd_tokens_revoke(args.identifier, out)
    out(f"unhandled command: {args}")
    return 2


# --------------------------------------------------------------------- #
# Commands                                                               #
# --------------------------------------------------------------------- #


def _cmd_info(out: Callable[[str], None]) -> int:
    cfg = AppConfig()
    out(f"data_dir         = {cfg.storage.data_dir}")
    out(f"db_path          = {cfg.storage.db_path}")
    out(f"thumbs_dir       = {cfg.storage.thumbs_dir}")
    out(f"screenshots_dir  = {cfg.storage.screenshots_dir}")
    out(f"token_file       = {_DEFAULT_TOKEN_DIR / _TOKEN_FILE_NAME}")
    out(f"listen_addr      = {cfg.api_host}:{cfg.api_port}")
    if cfg.vlm is None:
        out("vlm              = <not configured (no TIMETRACE_VLM_API_KEY)>")
    else:
        out(f"vlm.base_url     = {cfg.vlm.base_url}")
        out(f"vlm.model        = {cfg.vlm.model}")
    return 0


def _cmd_tokens_list(out: Callable[[str], None]) -> int:
    tokens = _read_tokens()
    if not tokens:
        out("(no tokens)")
        return 0
    out(f"{'label':<24} {'created_at':<24} {'value (last 8)':<16}")
    out("-" * 64)
    for t in tokens:
        ts = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t.created_at / 1000))
            if t.created_at
            else "?"
        )
        out(f"{t.label:<24} {ts:<24} ...{t.value[-8:]:<16}")
    return 0


def _cmd_tokens_add(label: str, out: Callable[[str], None]) -> int:
    tokens = _read_tokens()
    if any(t.label == label for t in tokens):
        out(f"refused: a token with label {label!r} already exists. Revoke it first.")
        return 1
    new_entry = TokenEntry(
        value=_TOKEN_PREFIX + secrets.token_urlsafe(32),
        label=label,
        created_at=int(time.time() * 1000),
    )
    tokens.append(new_entry)
    _write_tokens(tokens)
    out("Minted new token. Copy this value NOW — it will not be shown again:")
    out("")
    out(f"  {new_entry.value}")
    out("")
    out(f"  label      = {new_entry.label}")
    out(f"  created_at = {new_entry.created_at}")
    out("Restart timetrace-server for the new token to be accepted.")
    return 0


def _cmd_tokens_revoke(identifier: str, out: Callable[[str], None]) -> int:
    tokens = _read_tokens()
    if not tokens:
        out("(no tokens to revoke)")
        return 1
    matches = [
        t
        for t in tokens
        if t.label == identifier or t.value == identifier or t.value.endswith(identifier)
    ]
    if not matches:
        out(f"no token matches {identifier!r}")
        return 1
    if len(matches) > 1:
        out(f"ambiguous: {identifier!r} matches {len(matches)} tokens; use full value or label.")
        return 1
    target = matches[0]
    remaining = [t for t in tokens if t is not target]
    _write_tokens(remaining)
    out(f"Revoked token labelled {target.label!r} (...{target.value[-8:]}).")
    out("Restart timetrace-server for the revocation to take effect.")
    return 0


# --------------------------------------------------------------------- #
# File I/O                                                               #
# --------------------------------------------------------------------- #


def _token_path() -> Path:
    return _DEFAULT_TOKEN_DIR / _TOKEN_FILE_NAME


def _read_tokens() -> list[TokenEntry]:
    path = _token_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text("utf-8"))
    return [TokenEntry(**e) for e in data.get("tokens", [])]


def _write_tokens(tokens: list[TokenEntry]) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"tokens": [asdict(t) for t in tokens]}, indent=2)
    path.write_text(payload, encoding="utf-8")
    _harden_perms(path)


def _harden_perms(path: Path) -> None:
    """chmod 600 on POSIX. No-op on Windows (NTFS family ACL good enough)."""
    try:
        if sys.platform != "win32":
            path.chmod(0o600)
    except OSError:
        pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
