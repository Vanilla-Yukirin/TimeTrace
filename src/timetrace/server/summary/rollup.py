"""MetricsCascadeBuilder — the deterministic, LLM-free spine of the pyramid.

Builds the metric rows of the ``summaries`` cascade for a time range:

- ``5min`` (leaf): aggregated straight from L1 frames (pure SQL).
- ``1h`` / ``6h`` / ``day`` / ``week``: summary-of-summaries — each higher
  window's metrics are the merge of its already-built children, never a frame
  re-scan. Empty windows produce no row.

Idempotent: ``window ⇒ scope_key`` and ``upsert_summary`` UPSERTs on
``(grain, scope_key)``, so re-running over the same range overwrites the same
rows (never double-counts) — the crash-replay / schedule-overlap safety the
existing worker queue relies on.

The LLM narrative stage (description / evaluation / compression / drill-down
hint), its claim queue, ``source_hash`` population + re-emission, and the
``_rollup_loop`` scheduler wiring are later slices; rows built here sit at
``status='pending_summary'`` (metrics done, narrative pending) and are already
usable by pure-SQL stats.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import structlog

from timetrace.server.summary.metrics import aggregate_frame_metrics, merge_metrics
from timetrace.server.summary.windows import (
    CHILD_OF,
    GRAINS,
    day_local,
    iter_window_starts,
    scope_key,
    window_bounds,
)

if TYPE_CHECKING:
    from timetrace.common.config import RollupConfig
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

# Rows whose metrics are built but whose LLM narrative hasn't run yet. The
# narrative stage (later slice) claims these via the summaries queue.
_METRICS_DONE_STATUS = "pending_summary"


class MetricsCascadeBuilder:
    """Builds the ``summaries`` metric rows for a time range, grain by grain."""

    def __init__(self, db: Database, cfg: RollupConfig) -> None:
        self._db = db
        self._cfg = cfg

    async def build_range(self, start_ms: int, end_ms: int) -> dict[str, int]:
        """Build every grain over ``[start_ms, end_ms)``; return per-grain counts.

        Grains are built fine → coarse so each higher grain's children already
        exist when it rolls up. Higher grains only ever cover the children that
        fall inside the range — a range shorter than a full higher window yields
        a *partial* row (correct; it's re-emitted as more children arrive).
        """
        counts: dict[str, int] = {}
        counts["5min"] = await self._build_leaf(start_ms, end_ms)
        for grain in GRAINS:
            if grain == "5min":
                continue
            counts[grain] = await self._build_rollup(grain, start_ms, end_ms)
        logger.info(
            "rollup.build_range",
            start_ms=start_ms,
            end_ms=end_ms,
            **{f"n_{g}": n for g, n in counts.items()},
        )
        return counts

    async def build_day(self, ts_in_day_ms: int) -> dict[str, int]:
        """Build the whole cascade for the logical day containing ``ts_in_day_ms``."""
        day_start, day_end = window_bounds(ts_in_day_ms, "day", self._cfg.cut_hour)
        return await self.build_range(day_start, day_end)

    async def build_recent(self, now_ms: int) -> dict[str, int]:
        """Rebuild the cascade over the recent lookback window, up to the last
        CLOSED 5min boundary (the still-open current window is skipped — it would
        just be rebuilt next tick). Driven by ``_rollup_loop``.

        Idempotent + the all-children merge means each tick safely refines
        windows as late frames get described/classified, so this is the live
        "keep recent metrics fresh" path. Historical days are NOT touched here —
        that's :meth:`backfill` (deliberate, gated on the classification backfill
        being done).
        """
        last_closed = window_bounds(now_ms, "5min", self._cfg.cut_hour)[0]
        start = last_closed - self._cfg.loop_lookback_h * 3600 * 1000
        if last_closed <= start:
            return {}
        return await self.build_range(start, last_closed)

    async def backfill(self, start_ms: int, end_ms: int, *, per_day_pause_s: float = 0.0) -> int:
        """Build the cascade for historical logical days in ``[start_ms, end_ms)``,
        NEWEST day first, optionally pausing between days to spare the box.

        Explicit + on-demand: nothing auto-calls this. Run it only after the
        historical classification backfill has finished — this slice has no
        source_hash re-emission, so a day built while its records are still being
        (re)classified would freeze inflated ``_unclassified`` time. Returns the
        number of logical days processed.
        """
        day_starts = iter_window_starts(start_ms, end_ms, "day", self._cfg.cut_hour)
        for i, day_start in enumerate(reversed(day_starts)):
            await self.build_day(day_start)
            logger.info("rollup.backfill_day", day_local=day_local(day_start, self._cfg.cut_hour))
            if per_day_pause_s and i < len(day_starts) - 1:
                await asyncio.sleep(per_day_pause_s)
        return len(day_starts)

    async def _build_leaf(self, start_ms: int, end_ms: int) -> int:
        cut = self._cfg.cut_hour
        built = 0
        for w_start in await self._populated_5min_starts(start_ms, end_ms):
            w_s, w_e = window_bounds(w_start, "5min", cut)
            metrics = await aggregate_frame_metrics(self._db, w_s, w_e)
            if metrics["record_count"] == 0:  # empty window — skip (no row)
                continue
            await self._upsert("5min", w_s, w_e, metrics)
            built += 1
        return built

    async def _build_rollup(self, grain: str, start_ms: int, end_ms: int) -> int:
        cut = self._cfg.cut_hour
        child = CHILD_OF[grain]
        # Parent windows touched by any child in the requested range.
        touched = {
            window_bounds(int(row["window_start"]), grain, cut)
            for row in await self._db.get_summaries_in_range(child, start_ms, end_ms)
        }
        for p_start, p_end in sorted(touched):
            # Merge ALL of this parent's children (over the parent's OWN bounds),
            # not just the children inside the requested range — otherwise a
            # partial-range rebuild would downgrade a parent that already has
            # children outside the range (breaking idempotency / crash recovery).
            # A parent therefore always equals the merge of every child that
            # currently exists for it.
            children = await self._db.get_summaries_in_range(child, p_start, p_end)
            parts = [json.loads(c["metrics_json"]) for c in children]
            if not parts:
                continue
            await self._upsert(grain, p_start, p_end, merge_metrics(parts))
        return len(touched)

    async def _populated_5min_starts(self, start_ms: int, end_ms: int) -> list[int]:
        """Distinct 5min window starts that actually contain a frame.

        One cheap query for the range's ``ts_start`` values, bucketed in Python,
        so we never fire an aggregate query against an empty window.
        """
        cut = self._cfg.cut_hour
        async with self._db.lock:
            async with self._db.conn.execute(
                "SELECT ts_start FROM records WHERE ts_start >= ? AND ts_start < ?",
                (start_ms, end_ms),
            ) as cur:
                rows = await cur.fetchall()
        starts = {window_bounds(int(r["ts_start"]), "5min", cut)[0] for r in rows}
        return sorted(starts)

    async def _upsert(self, grain: str, w_start: int, w_end: int, metrics: dict) -> str:
        key = scope_key(w_start, grain, self._cfg.cut_hour)
        return await self._db.upsert_summary(
            grain=grain,
            scope_key=key,
            window_start=w_start,
            window_end=w_end,
            day_local=day_local(w_start, self._cfg.cut_hour),
            metrics_json=json.dumps(metrics, ensure_ascii=False),
            record_count=metrics["record_count"],
            status=_METRICS_DONE_STATUS,
        )
