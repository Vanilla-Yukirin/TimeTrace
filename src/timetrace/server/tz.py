"""Request time-zone contract.

Storage is always UTC epoch ms. Anything that turns a timestamp into a civil
date/time — "today", "this week", ISO strings shown to an LLM, ``YYYY-MM-DD``
day bounds — happens in a *view* time zone:

- per request: the browser (or an MCP client) sends an IANA name via the
  ``X-Timezone`` header or a ``tz`` query parameter; :class:`TimezoneMiddleware`
  validates it and binds it to a context variable for the whole request,
  including streamed responses;
- otherwise the server default: ``TIMETRACE_TZ`` when set, else the process
  local zone (the behavior before this contract existed).

The summary pyramid's window grid is a stored, derived layer and therefore
uses :func:`server_tz` only — never a request zone — so its rows are stable no
matter who is looking. Callers working in another zone compute their own bounds
and simply get live aggregation or partially overlapping windows.

No fixed UTC offsets anywhere: conversions go through ``zoneinfo`` so DST and
historical offset changes are handled by the tz database.
"""

from __future__ import annotations

import contextvars
import datetime as _dt
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

logger = structlog.get_logger(__name__)

TZ_HEADER = "x-timezone"
TZ_QUERY = "tz"

_request_tz: contextvars.ContextVar[ZoneInfo | None] = contextvars.ContextVar(
    "timetrace_request_tz", default=None
)


def parse_tz(name: str) -> ZoneInfo:
    """Validate an IANA zone name. Raises ``ValueError`` on anything else."""
    name = (name or "").strip()
    if not name or len(name) > 64 or name.startswith(("/", ".")) or ".." in name:
        raise ValueError(f"invalid time zone: {name!r}")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone: {name!r}") from exc


def server_tz() -> ZoneInfo | None:
    """Server default zone: ``TIMETRACE_TZ`` if valid, else ``None`` (= process local)."""
    raw = os.environ.get("TIMETRACE_TZ", "").strip()
    if not raw:
        return None
    try:
        return parse_tz(raw)
    except ValueError:
        logger.warning("tz.invalid_server_default", value=raw)
        return None


def view_tz() -> ZoneInfo | None:
    """Zone for the current request, falling back to :func:`server_tz`."""
    return _request_tz.get() or server_tz()


def view_tz_name() -> str:
    """Display name of the view zone (IANA key, or the process local abbreviation)."""
    tz = view_tz()
    if tz is not None:
        return tz.key
    return _dt.datetime.now().astimezone().tzname() or "local"


@contextmanager
def use_tz(tz: ZoneInfo | None) -> Iterator[None]:
    """Bind ``tz`` as the view zone for the enclosed block (tests, background jobs)."""
    token = _request_tz.set(tz)
    try:
        yield
    finally:
        _request_tz.reset(token)


def to_local(ts_ms: int, tz: ZoneInfo | None = None) -> _dt.datetime:
    """epoch-ms → datetime in ``tz`` (naive process-local when ``tz`` is None)."""
    if tz is None:
        return _dt.datetime.fromtimestamp(ts_ms / 1000)
    return _dt.datetime.fromtimestamp(ts_ms / 1000, tz)


def from_local(dt: _dt.datetime, tz: ZoneInfo | None = None) -> int:
    """Civil datetime → epoch-ms. A naive ``dt`` is interpreted in ``tz``."""
    if dt.tzinfo is None and tz is not None:
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp() * 1000)


def format_ms(ts_ms: int, fmt: str = "%Y-%m-%d %H:%M:%S", tz: ZoneInfo | None = None) -> str:
    return to_local(ts_ms, tz).strftime(fmt)


class TimezoneMiddleware:
    """Pure-ASGI middleware binding the request's view zone.

    Pure ASGI (not ``BaseHTTPMiddleware``) so the context variable is visible to
    the endpoint, to mounted sub-apps (``/mcp``), and to streamed SSE bodies,
    which all run inside this ``await``. An invalid zone is a client error
    (400) rather than a silent fallback, so a broken browser setting is visible.
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw: str | None = None
        for key, value in scope.get("headers") or ():
            if key == TZ_HEADER.encode():
                raw = value.decode("latin-1")
                break
        query = scope.get("query_string") or b""
        if query:
            values = parse_qs(query.decode("latin-1")).get(TZ_QUERY)
            if values:
                raw = values[-1]
        if raw is None or not raw.strip():
            await self.app(scope, receive, send)
            return
        try:
            tz = parse_tz(raw)
        except ValueError as exc:
            body = json.dumps({"detail": str(exc)})
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body.encode()})
            return
        token = _request_tz.set(tz)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_tz.reset(token)
