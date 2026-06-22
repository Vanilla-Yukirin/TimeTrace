"""Pure-SQL per-window metrics + a Python child-merge for the cascade.

The leaf (``5min``) metrics are computed straight from L1 frames with the SAME
duration clamp and the SAME ``_unclassified`` vs ``uncategorized`` bucketing the
read-path tools use (``server/agent/tools.py``) — so the numbers the agent shows
and the numbers stored in the cascade are one definition, not two that can
drift. Higher grains are built by summing children (:func:`merge_metrics`),
never by re-scanning frames.

Durations are kept in **milliseconds** (exact integers). Summing truncated
*seconds* per-window would not equal the day total (``sum(trunc) != trunc(sum)``)
and would break the cascade SUM-invariant; ms addition is associative, so the
invariant holds by construction. Seconds conversion happens at the read edge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# The metric definition (clamp + unclassified bucket) is shared with the
# read-path tools so stored aggregates match what the agent reports. These are
# the canonical helpers; importing them keeps a single source of truth (a future
# refactor may relocate them to a neutral db helper).
from timetrace.server.agent.tools import _UNCLASSIFIED, _clamped_dur_sql

if TYPE_CHECKING:
    from timetrace.server.db import Database


def empty_metrics() -> dict:
    """A zeroed metrics dict (ms-based)."""
    return {"cat_ms": {}, "app_ms": {}, "active_ms": 0, "record_count": 0}


def merge_metrics(parts: list[dict]) -> dict:
    """Sum a list of per-window metrics into one (how a higher grain rolls up).

    Pure integer-ms addition, so ``merge`` of a window's children always equals
    a direct frame aggregation over that window's span (the SUM-invariant).
    """
    out = empty_metrics()
    for p in parts:
        if not p:
            continue
        for cat, ms in (p.get("cat_ms") or {}).items():
            out["cat_ms"][cat] = out["cat_ms"].get(cat, 0) + ms
        for app, ms in (p.get("app_ms") or {}).items():
            out["app_ms"][app] = out["app_ms"].get(app, 0) + ms
        out["active_ms"] += p.get("active_ms") or 0
        out["record_count"] += p.get("record_count") or 0
    return out


async def aggregate_frame_metrics(db: Database, start_ms: int, end_ms: int) -> dict:
    """Aggregate L1 frames whose ``ts_start`` ∈ ``[start_ms, end_ms)`` (half-open).

    Half-open so a frame on a window boundary is counted in exactly one window.
    Mirrors ``get_category_stats`` (the ``_unclassified`` CASE) and
    ``get_app_breakdown`` (``app_name != ''``) using the shared ``_clamped_dur_sql``.
    """
    cat_ms: dict[str, int] = {}
    app_ms: dict[str, int] = {}
    active_ms = 0
    record_count = 0
    async with db.lock:
        async with db.conn.execute(
            f"""SELECT CASE
                        WHEN a.record_id IS NULL OR a.category_final IS NULL
                          THEN '{_UNCLASSIFIED}'
                        ELSE a.category_final
                      END AS category,
                      COUNT(*) AS records,
                      SUM({_clamped_dur_sql("r")}) AS total_ms
               FROM records r
               LEFT JOIN analysis_results a ON a.record_id = r.id
               WHERE r.ts_start >= ? AND r.ts_start < ?
               GROUP BY category""",
            (start_ms, end_ms),
        ) as cur:
            for row in await cur.fetchall():
                ms = int(row["total_ms"] or 0)
                cat_ms[row["category"]] = ms
                active_ms += ms
                record_count += int(row["records"] or 0)
        async with db.conn.execute(
            f"""SELECT app_name, SUM({_clamped_dur_sql()}) AS total_ms
                FROM records
                WHERE ts_start >= ? AND ts_start < ? AND app_name != ''
                GROUP BY app_name""",
            (start_ms, end_ms),
        ) as cur:
            for row in await cur.fetchall():
                app_ms[row["app_name"]] = int(row["total_ms"] or 0)
    return {
        "cat_ms": cat_ms,
        "app_ms": app_ms,
        "active_ms": active_ms,
        "record_count": record_count,
    }


async def active_wall_ms(db: Database, start_ms: int, end_ms: int) -> int:
    """Wall-clock active ms over ``[start_ms, end_ms)`` as the UNION of per-record
    capture intervals — overlapping captures count once (ActivityWatch-style).

    Each record covers ``[ts_start, ts_start + clamped_span]`` where the span uses
    the SAME ``_clamped_dur_sql`` clamp as the additive path (negative → 0,
    over-5min sleep/lid artifact → 0). Gaps between records (sampling interval,
    idle) are not covered, so this is a floor on real on-screen time — but it
    DEDUPS the brief overlaps that make the additive ``active_ms`` (Σ own-span)
    slightly over-count. Because every record contributes an identical clamped
    span to both, ``active_wall_ms <= active_ms`` always holds.

    NOT used by the cascade: union is not additive across windows (intervals
    straddling a window boundary would be over/under-counted by a child SUM), so
    the stored ``active_ms`` stays Σ own-span (exact SUM-invariant). This is the
    read-edge dedup, computed live over the exact span.
    """
    dur = _clamped_dur_sql()  # CASE WHEN span>cap THEN 0 ELSE MAX(0, span) END
    async with db.lock:
        async with db.conn.execute(
            f"""WITH clamped AS (
                    SELECT ts_start AS s, ts_start + ({dur}) AS e
                    FROM records WHERE ts_start >= ? AND ts_start < ?
                ),
                ordered AS (
                    SELECT s, e,
                           MAX(e) OVER (ORDER BY s
                               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS run_max
                    FROM clamped
                ),
                grouped AS (
                    SELECT s, e, SUM(CASE WHEN run_max IS NULL OR s > run_max THEN 1 ELSE 0 END)
                                 OVER (ORDER BY s) AS g
                    FROM ordered
                )
                SELECT COALESCE(SUM(seg_e - seg_s), 0) AS wall_ms
                FROM (SELECT MIN(s) AS seg_s, MAX(e) AS seg_e FROM grouped GROUP BY g)""",
            (start_ms, end_ms),
        ) as cur:
            row = await cur.fetchone()
    return int(row["wall_ms"] or 0) if row else 0
