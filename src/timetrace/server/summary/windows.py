"""Deterministic time-window math for the summary cascade.

Pure functions, no I/O — every window boundary / scope key is computed from a
frame's ``ts_start`` (the business clock) and a configurable ``cut_hour``
(the 4AM-style logical-day boundary). This is the whole point of switching the
load-bearing layer from semantic episodes to fixed time windows: there is no
boundary-detection knob (γ) to mis-tune — windows are a dead-simple grid, so
the cascade is deterministic and idempotent (window ⇒ key).

Local civil time is used throughout (mirrors the rest of the codebase, which
uses ``time.localtime`` for day grouping). DST is NOT handled — the deployment
target (Asia/Shanghai) has none; revisit if a DST locale ever matters.

Grain ladder, fine → coarse::

    5min → 1h → 6h → day → week

``5min`` / ``1h`` align to the wall clock (the cut is a whole hour, so they
also align to the logical day). ``6h`` / ``day`` / ``week`` anchor to the
``cut_hour`` logical-day boundary.
"""

from __future__ import annotations

import datetime as _dt

# Fine → coarse. Mirrors RollupConfig.grains.
GRAINS: tuple[str, ...] = ("5min", "1h", "6h", "day", "week")

# child grain feeding each higher grain (higher grain ⇒ the grain it merges).
CHILD_OF: dict[str, str] = {"1h": "5min", "6h": "1h", "day": "6h", "week": "day"}
# inverse: the grain each one rolls up into (None for the top).
PARENT_OF: dict[str, str | None] = {
    "5min": "1h",
    "1h": "6h",
    "6h": "day",
    "day": "week",
    "week": None,
}

_FIXED_MINUTES = {"5min": 5, "1h": 60}


def _local(ts_ms: int) -> _dt.datetime:
    """epoch-ms → naive local-time datetime (mirrors time.localtime usage)."""
    return _dt.datetime.fromtimestamp(ts_ms / 1000)


def _ms(dt: _dt.datetime) -> int:
    """naive local-time datetime → epoch-ms."""
    return int(dt.timestamp() * 1000)


def _logical_day_start(dt: _dt.datetime, cut_hour: int) -> _dt.datetime:
    """The ``cut_hour`` boundary opening the logical day that contains ``dt``.

    e.g. with cut_hour=4, a 02:00 timestamp belongs to the *previous* calendar
    day's logical day (which opened at 04:00 yesterday).
    """
    anchor = dt.replace(hour=cut_hour, minute=0, second=0, microsecond=0)
    if dt < anchor:
        anchor -= _dt.timedelta(days=1)
    return anchor


def day_local(ts_ms: int, cut_hour: int = 4) -> str:
    """``'YYYY-MM-DD'`` logical-day key for ``ts_ms`` under the 4AM cut."""
    return _logical_day_start(_local(ts_ms), cut_hour).strftime("%Y-%m-%d")


def window_bounds(ts_ms: int, grain: str, cut_hour: int = 4) -> tuple[int, int]:
    """Return ``(start_ms, end_ms)`` of the ``grain`` window containing ``ts_ms``.

    Intervals are half-open ``[start, end)`` so the windows of a grain TILE the
    timeline without overlap — every frame falls in exactly one window per
    grain, which is what makes the cascade's SUM-invariant hold.
    """
    dt = _local(ts_ms)
    if grain in _FIXED_MINUTES:
        step = _FIXED_MINUTES[grain]
        if grain == "5min":
            start = dt.replace(second=0, microsecond=0, minute=(dt.minute // step) * step)
        else:  # "1h"
            start = dt.replace(minute=0, second=0, microsecond=0)
        return _ms(start), _ms(start + _dt.timedelta(minutes=step))
    if grain == "6h":
        lds = _logical_day_start(dt, cut_hour)
        idx = int((dt - lds) // _dt.timedelta(hours=6))  # 0..3 within the logical day
        start = lds + _dt.timedelta(hours=6 * idx)
        return _ms(start), _ms(start + _dt.timedelta(hours=6))
    if grain == "day":
        lds = _logical_day_start(dt, cut_hour)
        return _ms(lds), _ms(lds + _dt.timedelta(days=1))
    if grain == "week":
        lds = _logical_day_start(dt, cut_hour)
        week_start = lds - _dt.timedelta(days=lds.weekday())  # ISO week starts Monday
        return _ms(week_start), _ms(week_start + _dt.timedelta(days=7))
    raise ValueError(f"unknown grain: {grain}")


def scope_key(ts_ms: int, grain: str, cut_hour: int = 4) -> str:
    """Deterministic, human-readable window key (also the UNIQUE idempotency key).

    ``5min`` → ``'2026-06-09T18:25'``, ``1h`` → ``'2026-06-09T18'``,
    ``6h`` → ``'2026-06-09T2'`` (0..3 block within the logical day),
    ``day`` → ``'2026-06-09'``, ``week`` → ``'2026-W23'`` (ISO week).
    """
    start_ms, _ = window_bounds(ts_ms, grain, cut_hour)
    start = _local(start_ms)
    if grain == "5min":
        return start.strftime("%Y-%m-%dT%H:%M")
    if grain == "1h":
        return start.strftime("%Y-%m-%dT%H")
    if grain == "6h":
        lds = _logical_day_start(start, cut_hour)
        idx = int((start - lds) // _dt.timedelta(hours=6))
        return f"{lds.strftime('%Y-%m-%d')}T{idx}"
    if grain == "day":
        return start.strftime("%Y-%m-%d")
    if grain == "week":
        iso = start.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"unknown grain: {grain}")


def iter_window_starts(start_ms: int, end_ms: int, grain: str, cut_hour: int = 4) -> list[int]:
    """All distinct ``grain`` window-start ms intersecting ``[start_ms, end_ms)``.

    Walks window-by-window (each window's end is the next window's start), so it
    is robust to variable-length windows (``day`` under odd cut hours) without
    assuming a fixed step. Empty windows are still listed; the builder skips
    those that contain no frames.
    """
    out: list[int] = []
    if end_ms <= start_ms:
        return out
    cursor = window_bounds(start_ms, grain, cut_hour)[0]
    guard = 0
    while cursor < end_ms:
        out.append(cursor)
        nxt = window_bounds(cursor, grain, cut_hour)[1]
        if nxt <= cursor:  # pragma: no cover - defensive, should never happen
            break
        cursor = nxt
        guard += 1
        if guard > 100_000:  # pragma: no cover - runaway guard
            raise RuntimeError("iter_window_starts overran; bad grain math")
    return out
