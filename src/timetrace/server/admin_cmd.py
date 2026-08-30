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
After ``tokens add`` / ``tokens revoke``, restart the systemd unit so the
in-memory token set picks up the change. Keeps the file→memory sync logic
in one place (load_or_generate at startup), avoids file-watcher complexity.

Schema ownership: token-file reads / writes go through
:class:`timetrace.server.auth.ServerAuth` (``read_tokens`` / ``write_tokens``).
This module does NOT re-implement the JSON shape — schema changes ripple
from auth.py only.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from collections.abc import Callable

from timetrace.common.config import AppConfig
from timetrace.server.auth import ServerAuth, TokenEntry


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

    bf = sub.add_parser("backfill", help="Build the metrics cascade for a historical date range.")
    bf.add_argument("start", help="Start (local 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS').")
    bf.add_argument("end", help="End; the cascade tiles whole logical days in [start, end).")
    bf.add_argument(
        "--pause",
        type=float,
        default=1.0,
        help="Seconds to pause between days, to spare the box (default 1.0).",
    )

    nr = sub.add_parser("narrate", help="Generate LLM narratives for finalized cascade windows.")
    nr.add_argument("start", help="Start (local 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS').")
    nr.add_argument("end", help="End; narrates finalized windows in [start, end), bottom-up.")
    nr.add_argument("--limit", type=int, default=500, help="Max windows per grain (default 500).")
    nr.add_argument(
        "--force", action="store_true", help="Re-narrate even already-narrated windows."
    )

    repair = sub.add_parser(
        "repair-data-quality",
        help="Audit deterministic metadata defects; use --apply to back up and repair.",
    )
    repair.add_argument(
        "--apply",
        action="store_true",
        help="Create an online SQLite backup, then apply one transactional repair.",
    )
    nr.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Force a uniform LLM output budget across grains (omit for the "
        "per-grain default: 4000 for 5min up to 7000 for day/week). An "
        "always-thinking model spends this on reasoning before content.",
    )
    nr.add_argument(
        "--grains",
        default=None,
        help="Comma-separated grains to (re)narrate, e.g. '1h,6h,day,week' to "
        "redo only parent layers. Default: all (5min,1h,6h,day,week).",
    )

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
    if args.cmd == "backfill":
        return _cmd_backfill(args.start, args.end, args.pause, out)
    if args.cmd == "narrate":
        return _cmd_narrate(
            args.start, args.end, args.limit, args.force, args.max_tokens, args.grains, out
        )
    if args.cmd == "repair-data-quality":
        return _cmd_repair_data_quality(args.apply, out)
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
    out(f"token_file       = {ServerAuth.token_path()}")
    out(f"listen_addr      = {cfg.api_host}:{cfg.api_port}")
    if cfg.vlm is None:
        out("vlm              = <not configured (no TIMETRACE_VLM_API_KEY)>")
    else:
        out(f"vlm.base_url     = {cfg.vlm.base_url}")
        out(f"vlm.model        = {cfg.vlm.model}")
    return 0


def _cmd_tokens_list(out: Callable[[str], None]) -> int:
    tokens = ServerAuth.read_tokens()
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
    tokens = ServerAuth.read_tokens()
    if any(t.label == label for t in tokens):
        out(f"refused: a token with label {label!r} already exists. Revoke it first.")
        return 1
    new_entry = TokenEntry(
        value=ServerAuth.mint_token_value(),
        label=label,
        created_at=int(time.time() * 1000),
    )
    tokens.append(new_entry)
    ServerAuth.write_tokens(tokens)
    out("Minted new token. Copy this value NOW — it will not be shown again:")
    out("")
    out(f"  {new_entry.value}")
    out("")
    out(f"  label      = {new_entry.label}")
    out(f"  created_at = {new_entry.created_at}")
    out("Restart timetrace-server for the new token to be accepted.")
    return 0


def _cmd_tokens_revoke(identifier: str, out: Callable[[str], None]) -> int:
    tokens = ServerAuth.read_tokens()
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
    ServerAuth.write_tokens(remaining)
    out(f"Revoked token labelled {target.label!r} (...{target.value[-8:]}).")
    out("Restart timetrace-server for the revocation to take effect.")
    return 0


def _cmd_repair_data_quality(apply: bool, out: Callable[[str], None]) -> int:
    """Audit or transactionally repair deterministic data-quality defects."""
    import asyncio
    import datetime as _dt

    from timetrace.server.db import Database

    cfg = AppConfig()
    backup_path = None
    if apply:
        stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
        backup_path = cfg.storage.db_path.with_name(
            f"{cfg.storage.db_path.stem}.pre-quality-repair-{stamp}{cfg.storage.db_path.suffix}"
        )
        # sqlite3.backup() gives a consistent online snapshot even while the
        # WAL-mode server is running; a plain file copy would not.
        source = sqlite3.connect(f"file:{cfg.storage.db_path}?mode=ro", uri=True)
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        if os.name == "posix":
            backup_path.chmod(0o600)
        out(f"backup = {backup_path}")

    async def _run() -> tuple[dict[str, int], dict[str, int] | None, dict[str, int]]:
        db = Database(cfg.storage)
        await db.init()
        try:
            before = await db.inspect_data_quality()
            applied = await db.repair_data_quality() if apply else None
            after = await db.inspect_data_quality()
            return before, applied, after
        finally:
            await db.close()

    try:
        before, applied, after = asyncio.run(_run())
    except Exception:
        if backup_path is not None:
            out(f"repair failed; backup remains at {backup_path}")
        raise
    out(f"before = {before}")
    if applied is None:
        out("dry-run only; re-run with --apply to back up and repair")
    else:
        out(f"applied = {applied}")
        out(f"after = {after}")
    return 0


def _cmd_backfill(start: str, end: str, pause: float, out: Callable[[str], None]) -> int:
    """Build the metrics cascade for ``[start, end)`` over historical logical days.

    One-shot. Safe to run while the server is live: writes are idempotent UPSERTs
    on ``(grain, scope_key)`` and SQLite WAL + ``busy_timeout`` serialize them
    against the server's rollup loop. Only run AFTER classification backfill is
    done, or half-classified days get frozen into metrics (no source_hash
    re-emission yet to self-correct).
    """
    import asyncio
    import datetime as _dt

    from timetrace.server.db import Database
    from timetrace.server.summary.rollup import MetricsCascadeBuilder

    try:
        start_ms = int(_dt.datetime.fromisoformat(start).timestamp() * 1000)
        end_ms = int(_dt.datetime.fromisoformat(end).timestamp() * 1000)
    except ValueError as exc:
        out(f"bad date (use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'): {exc}")
        return 2
    if end_ms <= start_ms:
        out("end must be after start")
        return 2

    cfg = AppConfig()

    async def _run() -> dict:
        db = Database(cfg.storage)
        await db.init()
        try:
            builder = MetricsCascadeBuilder(db, cfg.rollup)
            days = await builder.backfill(start_ms, end_ms, per_day_pause_s=pause)
            rows: dict[str, int] = {}
            for grain in ("5min", "1h", "6h", "day", "week"):
                async with db.conn.execute(
                    "SELECT COUNT(*) FROM summaries WHERE grain=?", (grain,)
                ) as cur:
                    rows[grain] = (await cur.fetchone())[0]
            return {"days": days, "rows": rows}
        finally:
            await db.close()

    result = asyncio.run(_run())
    out(f"backfilled {result['days']} logical day(s) over [{start} .. {end})")
    out(f"summaries rows now (all grains): {result['rows']}")
    return 0


def _cmd_narrate(
    start: str,
    end: str,
    limit: int,
    force: bool,
    max_tokens: int | None,
    grains: str | None,
    out: Callable[[str], None],
) -> int:
    """Generate LLM narratives for finalized windows in ``[start, end)``, bottom-up.

    One-shot, idempotent (only narrates rows still pending). Uses the configured
    VLM chat endpoint (LM Studio). Prints a few real samples so the voice can be
    eyeballed. Run AFTER the metrics cascade is built for the range (backfill /
    rollup loop) — it narrates existing summary rows, it does not build metrics.
    """
    import asyncio
    import datetime as _dt
    import json as _json
    import time as _time

    from openai import AsyncOpenAI

    from timetrace.server.db import Database
    from timetrace.server.summary.narrative import (
        NarrativeBuilder,
        NarrativeCascade,
        OpenAINarrativeLLM,
    )

    try:
        start_ms = int(_dt.datetime.fromisoformat(start).timestamp() * 1000)
        end_ms = int(_dt.datetime.fromisoformat(end).timestamp() * 1000)
    except ValueError as exc:
        out(f"bad date (use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'): {exc}")
        return 2
    if end_ms <= start_ms:
        out("end must be after start")
        return 2

    cfg = AppConfig()
    if cfg.vlm is None:
        out("no LLM configured (set TIMETRACE_VLM_*) — narrative needs a chat endpoint")
        return 1

    grain_filter = tuple(g.strip() for g in grains.split(",") if g.strip()) if grains else None

    async def _run() -> tuple[dict, list[dict]]:
        db = Database(cfg.storage)
        await db.init()
        try:
            client = AsyncOpenAI(base_url=cfg.vlm.base_url, api_key=cfg.vlm.api_key)
            llm = OpenAINarrativeLLM(
                client, cfg.vlm.model, disable_thinking=cfg.vlm.disable_thinking
            )
            cascade = NarrativeCascade(db, NarrativeBuilder(db, llm, max_tokens=max_tokens))
            counts = await cascade.narrate_range(
                start_ms,
                end_ms,
                int(_time.time() * 1000),
                per_grain_limit=limit,
                force=force,
                grains=grain_filter,
            )
            samples: list[dict] = []
            for grain in ("day", "6h", "1h", "5min"):  # coarse first (most interesting)
                for r in await db.get_summaries_in_range(grain, start_ms, end_ms):
                    if r.get("description"):
                        samples.append(r)
            return counts, samples[:4]
        finally:
            await db.close()

    counts, samples = asyncio.run(_run())
    out(f"narrated: {counts}")
    for s in samples:
        body = _json.loads(s.get("body_json") or "{}")
        out("")
        out(f"--- [{s['grain']}] {s['scope_key']} ---")
        out(f"description: {s['description']}")
        out(f"key_points:  {body.get('key_points')}")
        out(f"evaluation:  {s['evaluation']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
