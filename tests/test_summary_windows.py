"""Unit tests for the deterministic time-window math (no I/O).

Sentinel year 2099 so nothing reads as a real "today". All timestamps are
built from local-time datetimes and compared against local-time expectations,
so the assertions hold regardless of the machine timezone (the deploy target,
Asia/Shanghai, has no DST — which these dates also avoid).
"""

from __future__ import annotations

import datetime as _dt
import re

from timetrace.server.summary.windows import (
    CHILD_OF,
    GRAINS,
    PARENT_OF,
    day_local,
    iter_window_starts,
    scope_key,
    window_bounds,
)

CUT = 4


def _ms(y, mo, d, h, mi=0, s=0) -> int:
    return int(_dt.datetime(y, mo, d, h, mi, s).timestamp() * 1000)


def _dt_of(ms: int) -> _dt.datetime:
    return _dt.datetime.fromtimestamp(ms / 1000)


def test_5min_and_1h_align_to_wall_clock():
    ts = _ms(2099, 6, 9, 18, 27, 33)
    s, e = window_bounds(ts, "5min", CUT)
    assert _dt_of(s) == _dt.datetime(2099, 6, 9, 18, 25, 0)
    assert _dt_of(e) == _dt.datetime(2099, 6, 9, 18, 30, 0)
    s, e = window_bounds(ts, "1h", CUT)
    assert _dt_of(s) == _dt.datetime(2099, 6, 9, 18, 0, 0)
    assert _dt_of(e) == _dt.datetime(2099, 6, 9, 19, 0, 0)


def test_6h_blocks_anchor_to_cut():
    # 18:27 → block idx 2 = [16:00, 22:00) under a 04:00 cut.
    ts = _ms(2099, 6, 9, 18, 27)
    s, e = window_bounds(ts, "6h", CUT)
    assert _dt_of(s) == _dt.datetime(2099, 6, 9, 16, 0)
    assert _dt_of(e) == _dt.datetime(2099, 6, 9, 22, 0)
    assert scope_key(ts, "6h", CUT) == "2099-06-09T2"


def test_day_is_4am_to_4am():
    ts = _ms(2099, 6, 9, 18, 27)
    s, e = window_bounds(ts, "day", CUT)
    assert _dt_of(s) == _dt.datetime(2099, 6, 9, 4, 0)
    assert _dt_of(e) == _dt.datetime(2099, 6, 10, 4, 0)
    assert scope_key(ts, "day", CUT) == "2099-06-09"
    assert day_local(ts, CUT) == "2099-06-09"


def test_before_cut_belongs_to_previous_logical_day():
    # 02:30 is before the 04:00 cut → previous logical day, last 6h block.
    ts = _ms(2099, 6, 9, 2, 30)
    assert day_local(ts, CUT) == "2099-06-08"
    s, e = window_bounds(ts, "day", CUT)
    assert _dt_of(s) == _dt.datetime(2099, 6, 8, 4, 0)
    assert _dt_of(e) == _dt.datetime(2099, 6, 9, 4, 0)
    s6, e6 = window_bounds(ts, "6h", CUT)
    assert _dt_of(s6) == _dt.datetime(2099, 6, 8, 22, 0)
    assert _dt_of(e6) == _dt.datetime(2099, 6, 9, 4, 0)
    assert scope_key(ts, "6h", CUT) == "2099-06-08T3"


def test_week_is_iso_week_monday_anchored():
    ts = _ms(2099, 6, 9, 18, 27)
    s, e = window_bounds(ts, "week", CUT)
    day_start = window_bounds(ts, "day", CUT)[0]
    assert (e - s) == 7 * 24 * 3600 * 1000  # exactly 7 days (no DST locale)
    assert _dt_of(s).weekday() == 0  # Monday
    assert _dt_of(s).hour == CUT
    assert s <= day_start < e
    assert re.fullmatch(r"\d{4}-W\d{2}", scope_key(ts, "week", CUT))


def test_boundary_is_half_open():
    # A frame exactly on a 5min boundary belongs to the window it OPENS, and
    # one millisecond earlier belongs to the previous window — so windows tile
    # without overlap (the SUM-invariant depends on this).
    on = _ms(2099, 6, 9, 18, 25, 0)
    assert _dt_of(window_bounds(on, "5min", CUT)[0]) == _dt.datetime(2099, 6, 9, 18, 25)
    before = on - 1
    assert _dt_of(window_bounds(before, "5min", CUT)[0]) == _dt.datetime(2099, 6, 9, 18, 20)


def test_iter_window_starts_tiles_contiguously():
    start = _ms(2099, 6, 9, 9, 0)
    end = _ms(2099, 6, 9, 9, 23)  # spans 09:00..09:23 → 5 five-min windows
    starts = iter_window_starts(start, end, "5min", CUT)
    assert len(starts) == 5
    # contiguous: each window's end == the next window's start
    for a, b in zip(starts, starts[1:]):
        assert window_bounds(a, "5min", CUT)[1] == b


def test_grain_ladder_relations_are_consistent():
    assert GRAINS == ("5min", "1h", "6h", "day", "week")
    # CHILD_OF and PARENT_OF are inverses across the ladder.
    for higher, lower in CHILD_OF.items():
        assert PARENT_OF[lower] == higher
    assert PARENT_OF["week"] is None


def test_scope_key_is_deterministic_within_window():
    # Every ts within a window maps to the SAME scope_key (so the UNIQUE(grain,
    # scope_key) index collapses a window to one row); the window_end ts belongs
    # to the NEXT window (half-open). Guards the idempotency-key semantics.
    samples = [
        _ms(2099, 6, 9, 9, 0),  # on a 5min/1h boundary
        _ms(2099, 6, 9, 18, 27),  # mid 6h block
        _ms(2099, 6, 9, 23, 30),  # last 6h block (before midnight)
        _ms(2099, 6, 9, 4, 0),  # day start
    ]
    for grain in GRAINS:
        for ts in samples:
            w_start, w_end = window_bounds(ts, grain, CUT)
            key = scope_key(ts, grain, CUT)
            assert scope_key(w_start, grain, CUT) == key
            assert scope_key(w_end - 1, grain, CUT) == key
            assert scope_key(w_end, grain, CUT) != key  # boundary ⇒ next window
