"""``timetrace-embserver`` entry point — local Qwen3-VL embedding daemon + control.

Subcommands:
  serve (default)  run the daemon (uvicorn + JIT/TTL model lifecycle)
  info             print resolved config + listen addr (no daemon needed)
  status | ps      GET  /admin/status   (model loaded? dtype? vram? idle?)
  load             POST /admin/load     (--dtype / --model to swap precision/size)
  unload           POST /admin/unload   (free VRAM now)
  ttl <seconds>    POST /admin/ttl      (0 = resident; >0 = idle auto-unload)
  selftest         POST /admin/selftest (drift vs bundled fp32 golden master)

Control subcommands are thin HTTP clients to a *running* daemon, mirroring how
`lms` drives LM Studio. They need the api key pinned via
TIMETRACE_EMBSERVER_API_KEY (shared with the daemon) — an auto-generated key
can't be known by a separate CLI process.

Heavy deps (torch/transformers) live behind the `embserver` optional extra and
are only imported by `serve`; info/status/etc. work without them.
"""

from __future__ import annotations

import json
import logging
import sys

import structlog
from dotenv import load_dotenv

load_dotenv()

from .config import EmbServerConfig  # noqa: E402

logger = structlog.get_logger(__name__)

_HELP = """\
timetrace-embserver — local Qwen3-VL multimodal embedding daemon.

Usage:
  timetrace-embserver               Run the server (default).
  timetrace-embserver info          Print resolved config + listen addr.
  timetrace-embserver status|ps     Show model load state / dtype / vram / idle.
  timetrace-embserver load [--dtype D] [--model PATH]   (Re)load, optionally swap.
  timetrace-embserver unload        Free VRAM now.
  timetrace-embserver ttl <seconds> Set idle auto-unload (0 = resident).
  timetrace-embserver selftest [--threshold T]   Drift check vs golden master.
  timetrace-embserver -h|--help

Config via env (or .env):
  TIMETRACE_EMBSERVER_HOST    (default 127.0.0.1)
  TIMETRACE_EMBSERVER_PORT    (default 8766)
  TIMETRACE_EMBSERVER_MODEL   (default ~/TimeTraceData/models/Qwen3-VL-Embedding-2B)
  TIMETRACE_EMBSERVER_DTYPE   (bfloat16|float16|float32|int8|int4|auto; default bfloat16)
  TIMETRACE_EMBSERVER_API_KEY (generated+logged once if unset; PIN it to use control subcommands)
  TIMETRACE_EMBSERVER_TTL     (idle seconds before auto-unload; 0 = resident)
  TIMETRACE_EMBSERVER_PRELOAD (1 = load at startup instead of JIT)
"""


def _print_info(cfg: EmbServerConfig) -> None:
    key, generated = cfg.ensure_api_key()
    print(f"listen      : http://{cfg.host}:{cfg.port}")
    print(f"model_path  : {cfg.model_path}")
    print(f"dtype       : {cfg.dtype}")
    print(f"idle_ttl    : {cfg.idle_ttl_seconds}s (0 = resident)")
    print(f"preload     : {cfg.preload}")
    suffix = "  (generated; set TIMETRACE_EMBSERVER_API_KEY to pin)" if generated else ""
    print(f"api_key     : {key}{suffix}")


# ---- control subcommands (HTTP client to a running daemon) ----------------


def _client_call(cfg: EmbServerConfig, method: str, path: str, body: dict | None = None) -> int:
    import httpx  # noqa: PLC0415

    if not cfg.api_key:
        print(
            "error: TIMETRACE_EMBSERVER_API_KEY not set — control subcommands need the "
            "key pinned (shared with the running daemon).",
            file=sys.stderr,
        )
        return 2
    url = f"http://{cfg.host}:{cfg.port}{path}"
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    try:
        resp = httpx.request(method, url, headers=headers, json=body, timeout=600)
    except httpx.HTTPError as exc:
        print(f"error: cannot reach daemon at {url}: {exc}", file=sys.stderr)
        return 1
    if resp.status_code >= 400:
        print(f"error {resp.status_code}: {resp.text}", file=sys.stderr)
        return 1
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    return 0


def _flag(args: list[str], name: str) -> str | None:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _run_control(cfg: EmbServerConfig, args: list[str]) -> int:
    cmd = args[0]
    if cmd in ("status", "ps"):
        return _client_call(cfg, "GET", "/admin/status")
    if cmd == "unload":
        return _client_call(cfg, "POST", "/admin/unload")
    if cmd == "load":
        body: dict = {}
        if d := _flag(args, "--dtype"):
            body["dtype"] = d
        if m := _flag(args, "--model"):
            body["model_path"] = m
        return _client_call(cfg, "POST", "/admin/load", body)
    if cmd == "ttl":
        if len(args) < 2 or not args[1].lstrip("-").isdigit():
            print("usage: timetrace-embserver ttl <seconds>", file=sys.stderr)
            return 2
        return _client_call(cfg, "POST", "/admin/ttl", {"ttl_seconds": int(args[1])})
    if cmd == "selftest":
        th = _flag(args, "--threshold")
        return _client_call(cfg, "POST", "/admin/selftest", {"threshold": float(th)} if th else {})
    return -1  # not a control command


def _serve(cfg: EmbServerConfig) -> None:
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))

    key, generated = cfg.ensure_api_key()
    if generated:
        logger.warning(
            "embserver.apikey.generated",
            api_key=key,
            hint="set TIMETRACE_EMBSERVER_API_KEY to pin this across restarts",
        )

    import uvicorn  # noqa: PLC0415

    from .api import create_app  # noqa: PLC0415
    from .engine import EmbeddingEngine  # noqa: PLC0415

    engine = EmbeddingEngine(
        model_path=cfg.model_path, dtype=cfg.dtype, idle_ttl_seconds=cfg.idle_ttl_seconds
    )
    app = create_app(cfg, engine)
    logger.info(
        "embserver.serve",
        host=cfg.host,
        port=cfg.port,
        model=str(cfg.model_path),
        dtype=cfg.dtype,
        ttl=cfg.idle_ttl_seconds,
    )
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(_HELP)
        return

    cfg = EmbServerConfig.from_env()

    if args and args[0] == "info":
        _print_info(cfg)
        return

    if args and args[0] in ("status", "ps", "load", "unload", "ttl", "selftest"):
        sys.exit(_run_control(cfg, args))

    if args:
        print(f"Unknown command: {args[0]}\n", file=sys.stderr)
        print(_HELP, file=sys.stderr)
        sys.exit(2)

    _serve(cfg)


if __name__ == "__main__":
    main()
