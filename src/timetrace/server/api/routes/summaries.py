"""Memory-pyramid summary feed for ONE logical day (powers the /pyramid panel).

Returns every grain's windows that overlap the day, each with its narrative
(description / key_points / evaluation) + a compact metrics summary. The frontend
positions them on a shared time axis so parent↔child line up vertically.

Read-only; gated by ``require_principal`` (business_deps block of ``create_app``).
"""

from __future__ import annotations

import datetime as _dt
import json
import time

from fastapi import APIRouter, Request

from timetrace.server.summary.windows import GRAINS
from timetrace.server.tz import from_local, server_tz, to_local, view_tz, view_tz_name

router = APIRouter(tags=["summaries"])

# Mirrors RollupConfig.cut_hour (4AM logical-day boundary). The pyramid grid
# anchors here, so the day window must too.
_CUT_HOUR = 4


def _day_bounds(day: str | None) -> tuple[int, int, str]:
    """Resolve a 'YYYY-MM-DD' (or None=today) to the [4AM, next 4AM) logical day
    in the request's view zone (see server/tz.py)."""
    tz = view_tz()
    if day:
        d = _dt.date.fromisoformat(day)
    else:
        now = to_local(int(time.time() * 1000), tz)
        # before the cut, "today" is still the previous logical day
        d = (now - _dt.timedelta(hours=_CUT_HOUR)).date()
    start = _dt.datetime(d.year, d.month, d.day, _CUT_HOUR)
    start_ms = from_local(start, tz)
    end_ms = from_local(start + _dt.timedelta(days=1), tz)
    return start_ms, end_ms, d.isoformat()


def _shape(r: dict) -> dict:
    body = json.loads(r.get("body_json") or "{}")
    metrics = json.loads(r.get("metrics_json") or "{}")
    cat = metrics.get("cat_ms") or {}
    top = sorted(cat.items(), key=lambda kv: -kv[1])[:3]
    return {
        "scope_key": r["scope_key"],
        "grain": r["grain"],
        "window_start": r["window_start"],
        "window_end": r["window_end"],
        "status": r["status"],
        "description": r["description"],
        "evaluation": r["evaluation"],
        "key_points": body.get("key_points") or [],
        "metrics_only": bool(body.get("metrics_only")),
        "record_count": metrics.get("record_count", 0),
        "active_seconds": (metrics.get("active_ms") or 0) // 1000,
        "top_categories": [{"category": k, "seconds": v // 1000} for k, v in top],
    }


@router.get("/summaries")
async def summaries_for_day(request: Request, day: str | None = None) -> dict:
    """All grains' windows overlapping the given logical day (default: today).

    Returns ``{day, day_start, day_end, grains: {grain: [windows…]}}``. Windows
    are ordered by ``window_start``; a coarse window (week) that merely contains
    the day is included (overlap query), so the pyramid columns all line up.
    """
    db = request.app.state.db
    start_ms, end_ms, day_iso = _day_bounds(day)
    grains: dict[str, list[dict]] = {}
    for grain in GRAINS:  # 5min … week
        rows = await db.get_summaries_overlapping(grain, start_ms, end_ms)
        grains[grain] = [_shape(r) for r in rows]
    return {
        "day": day_iso,
        "day_start": start_ms,
        "day_end": end_ms,
        "timezone": view_tz_name(),
        # Zone the stored windows were cut in; when it differs from ``timezone``
        # the 6h/day/week windows straddle this day's edges.
        "pyramid_timezone": (server_tz().key if server_tz() else None),
        "grains": grains,
    }
