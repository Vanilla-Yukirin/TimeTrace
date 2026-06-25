"""``timetrace-server`` entry point — server half only (no capture, no tray).

Composition is delegated to :mod:`timetrace.server.bootstrap` so this file
and ``main.py`` (the all-in-one entry) cannot drift apart on the server-side
graph. CLI here is just: load env / config, build, serve, handle Ctrl+C.

For the all-in-one single-process default, see ``src/timetrace/main.py``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import structlog
from dotenv import load_dotenv

# load_dotenv() runs BEFORE AppConfig so VLMConfig.from_env() sees the values.
load_dotenv()

from timetrace.common.config import AppConfig  # noqa: E402
from timetrace.server.bootstrap import build_server_components, serve  # noqa: E402

logger = structlog.get_logger(__name__)


_HELP_TEXT = """\
timetrace-server — server half of TimeTrace (API + DB + worker, no capture).

Usage:
  timetrace-server                       Run the server (default).
  timetrace-server info                  Print resolved paths + listen addr.
  timetrace-server tokens list           List all bearer tokens (masked).
  timetrace-server tokens add <label>    Mint a new token; shows the value
                                         ONCE, copy into client.toml.
  timetrace-server tokens revoke <id>    Revoke by label / full / suffix-8.
  timetrace-server backfill <start> <end>  Build the metrics cascade for a
                                         historical date range (one-shot; safe
                                         while the server is running).
  timetrace-server -h | --help           Show this help.

Token file lives at ~/.config/timetrace-server/tokens.json (chmod 600 on POSIX).
After tokens add/revoke, restart the server for changes to take effect.
"""


async def _run(config: AppConfig, quit_event: asyncio.Event) -> None:
    components = await build_server_components(config)
    await serve(components, config, quit_event)


def main() -> None:
    # Subcommand dispatch — bare ``timetrace-server`` runs the server,
    # everything else is admin / one-shot. Hand-rolled rather than argparse
    # subparsers so the bare-no-args daemon path stays uncluttered.
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(_HELP_TEXT)
        return
    if args and args[0] in ("info", "tokens", "backfill"):
        from timetrace.server.admin_cmd import run as admin_run  # noqa: PLC0415

        sys.exit(admin_run(args))
    if args:
        print(f"Unknown command: {args[0]}\n", file=sys.stderr)
        print(_HELP_TEXT, file=sys.stderr)
        sys.exit(2)

    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    config = AppConfig()
    loop = asyncio.new_event_loop()
    quit_event = asyncio.Event()

    def _request_quit() -> None:
        try:
            loop.call_soon_threadsafe(quit_event.set)
        except RuntimeError:
            pass

    signal.signal(signal.SIGINT, lambda sig, frame: _request_quit())

    try:
        loop.run_until_complete(_run(config, quit_event))
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except (KeyboardInterrupt, SystemExit, Exception):
                pass
        loop.close()


if __name__ == "__main__":
    main()
