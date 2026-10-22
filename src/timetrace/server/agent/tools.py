"""Activity tools shared by the internal agent loop and the external MCP server.

Single source of truth: both ``server/agent/runner.py`` (the web chat agent)
and ``server/mcp_layer/server.py`` (Claude Code / Desktop over MCP) call these
implementations, so the read surface can't drift between the two clients.

Permission boundary — important. The read tools (``search_activity`` /
``get_recent_activity`` / ``get_app_breakdown`` / ``get_category_stats``) never
mutate. The ONLY write tool is ``apply_label``, and it only sets a record's
category (via the feedback audit trail + ``set_category_final``). It can never
delete or otherwise modify a record or its screenshots. This is the hard rule
for any AI driving TimeTrace: read everything, label only.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from typing import TYPE_CHECKING

import structlog

from timetrace.server.agent.activity_context import (
    PRESENCE_NOTE,
    activity_overview,
    activity_page,
    latest_activity,
    summary_item,
)

# Window math is pure (server/summary/windows.py imports only datetime), so this
# is cycle-free. aggregate_frame_metrics is imported function-locally in
# query_stats instead, because server/summary/metrics.py imports THIS module
# (_clamped_dur_sql/_UNCLASSIFIED) — a top-level import there would be circular.
from timetrace.server.retrieval import (
    fetch_records_by_ids,
    retrieve_records,
    retrieve_summaries,
)
from timetrace.server.summary.windows import CHILD_OF, GRAINS, scope_key, window_bounds
from timetrace.server.tz import format_ms, from_local, to_local, view_tz, view_tz_name

if TYPE_CHECKING:
    from timetrace.server.db import Database
    from timetrace.server.embedding.client import EmbeddingClient

logger = structlog.get_logger(__name__)

_MAX_LIMIT = 2000  # generous: a full day can be 1400+ records; clients page via cursor
_MAX_TOP_N = 50
_MAX_HOURS = 720  # 30 days

# A single record spanning more than this is a stale boundary artifact (laptop
# sleep / lid-close left the last pre-sleep record open until wake), NOT real
# activity time. Capture emits a heartbeat at least every 30s while active and
# idle closes a record after 180s, so nothing legitimate exceeds this. The
# write path rejects such boundaries and the online activity lease expires
# abandoned open rows; this read clamp remains defense in depth for legacy data
# until an explicit, backup-guarded repair has run.
_MAX_PLAUSIBLE_RECORD_MS = 5 * 60 * 1000

# Bucket for records that have NO category yet — either never analyzed, or
# analyzed by an older worker that described but never classified them (the
# pre-classification legacy backlog). This is a SYSTEM-INTERNAL state, NOT the
# user-facing ``uncategorized`` category. Keeping them separate stops reports
# from narrating "未分类 Nh" as if it were real user behavior.
_UNCLASSIFIED = "_unclassified"
_UNCLASSIFIED_NOTE = (
    "已采集但分类尚未完成（旧数据待回填，或仍在排队）。这是系统内部处理状态，"
    "不是用户真实的『未分类』行为，不要据此推断用户在做什么。"
)

# id → 中文名 for the flat-6 taxonomy (mirrors db/sqlite.py _BUILTIN_CATEGORIES).
_CATEGORY_LEGEND = {
    "work": "工作",
    "study": "学习",
    "social": "沟通",
    "entertainment": "娱乐",
    "system": "系统",
    "uncategorized": "未分类",
}


def _clamped_dur_sql(prefix: str = "") -> str:
    """SQL expression for one record's trustworthy duration in ms.

    Negative spans (legacy ts_end<ts_start) floor to 0; spans over the
    plausibility cap (sleep/lid-close artifacts) contribute 0.
    """
    p = f"{prefix}." if prefix else ""
    span = f"COALESCE({p}ts_end, {p}ts_start) - {p}ts_start"
    return f"CASE WHEN {span} > {_MAX_PLAUSIBLE_RECORD_MS} THEN 0 ELSE MAX(0, {span}) END"


def ms_to_iso(ms: int | None) -> str:
    """epoch-ms → ISO 8601 local-tz, sortable + human-readable."""
    if ms is None:
        return ""
    return format_ms(ms, tz=view_tz())


def format_record_for_llm(r: dict, *, desc_chars: int = 160) -> str:
    """Compact one-line text representation of a record for LLM consumption."""
    ts = ms_to_iso(r.get("ts_start"))
    app = r.get("app_name") or ""
    title = (r.get("window_title") or "")[:80]
    desc = r.get("vlm_desc") or ""
    if len(desc) > desc_chars:
        desc = desc[:desc_chars] + "..."
    duration_s = 0
    if r.get("ts_end") and r.get("ts_start"):
        duration_s = max(0, int((r["ts_end"] - r["ts_start"]) / 1000))
    return f"{ts} | {app} | {title} | {duration_s}s | {desc}"


def _window(hours_back: int | None) -> tuple[int, int]:
    """Return (start_ms, end_ms). hours_back falsy → all-time."""
    now_ms = int(time.time() * 1000)
    if not hours_back:
        return 0, now_ms
    hb = min(max(1, int(hours_back)), _MAX_HOURS)
    return now_ms - hb * 3600 * 1000, now_ms


def _resolve_window(
    hours_back: int | None, start_iso: str | None, end_iso: str | None
) -> tuple[int, int]:
    """Resolve a query window; explicit ISO bounds win over ``hours_back``.

    ``start_iso``/``end_iso`` are local 'YYYY-MM-DD[ T]HH:MM[:SS]'. If only one
    is given the other end defaults (start→0, end→now). Falls back to the
    relative ``hours_back`` window when neither is set. Raises ValueError on
    unparseable / inverted bounds (callers surface it as an error dict).
    """
    if start_iso or end_iso:
        now_ms = int(time.time() * 1000)
        start_ms = _parse_iso_local(start_iso) if start_iso else 0
        end_ms = _parse_iso_local(end_iso) if end_iso else now_ms
        if end_ms <= start_ms:
            raise ValueError("end_iso must be after start_iso")
        return start_ms, end_ms
    return _window(hours_back)


def _scope_device(device_id: str | None) -> str | None:
    """Validate the optional device scope (raises ValueError → error dict)."""
    from timetrace.server.db.sqlite import normalize_device_filter  # noqa: PLC0415

    return normalize_device_filter(device_id)


def _device_sql(device_id: str | None, alias: str = "") -> tuple[str, list]:
    from timetrace.server.summary.metrics import device_condition  # noqa: PLC0415

    return device_condition(device_id, alias)


async def _labeler(db: Database):  # noqa: ANN202
    """``device_id → display label`` for result rows (unknown → 未知设备)."""
    labels = await db.device_labels()

    def label(device_id: str | None) -> str:
        if not device_id:
            return "未知设备"
        return labels.get(device_id) or f"设备 {device_id[:8]}"

    return label


# --------------------------------------------------------------------------- #
# Read tools                                                                   #
# --------------------------------------------------------------------------- #


async def search_activity(
    db: Database,
    query: str,
    limit: int = 20,
    hours_back: int | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    device_id: str | None = None,
    *,
    embedding: EmbeddingClient | None = None,
) -> dict:
    """Find records by meaning AND wording: keyword (FTS5 trigram / LIKE) +
    semantic (text-embedding cosine) fused with RRF — the same retrieval path
    as REST ``/v1/search/text``. Also returns the narrated pyramid windows whose
    summary mentions the query (``related_summaries``), so the caller can jump
    to the surrounding story instead of paging raw frames.

    Window: explicit ``start_iso``/``end_iso`` (view zone) take precedence over
    the relative ``hours_back``; neither → all time.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "query must not be empty"}
    limit = min(max(1, int(limit)), _MAX_LIMIT)
    try:
        start_ms, end_ms = _resolve_window(hours_back, start_iso, end_iso)
        device_id = _scope_device(device_id)
    except ValueError as exc:
        return {"error": str(exc)}
    result = await retrieve_records(
        db, embedding, query, start_ms, end_ms, limit=limit, device_id=device_id
    )
    meta = await fetch_records_by_ids(db, [rid for rid, _ in result.ranked])
    label = await _labeler(db)
    items = []
    for rid, _score in result.ranked:
        r = meta.get(rid)
        if r is None:
            continue
        items.append(
            {
                "id": rid,
                "ts_start_iso": ms_to_iso(r.get("ts_start")),
                "ts_end_iso": ms_to_iso(r.get("ts_end")),
                "app_name": r.get("app_name"),
                "window_title": r.get("window_title"),
                "vlm_desc": r.get("vlm_desc"),
                "category": r.get("category_final"),
                "device": label(r.get("device_id")),
                "matched_by": [
                    ch
                    for ch, ranks in (
                        ("keyword", result.keyword_ranks),
                        ("semantic", result.semantic_ranks),
                    )
                    if rid in ranks
                ],
            }
        )
    summaries = await retrieve_summaries(
        db, query, start_ms, end_ms, grains=["1h", "6h", "day"], limit=5
    )
    return {
        "query": query,
        "count": len(items),
        "semantic_channel": result.semantic_channel,
        "items": items,
        "related_summaries": [
            {
                "grain": r["grain"],
                "window_start_iso": ms_to_iso(r["window_start"]),
                "window_end_iso": ms_to_iso(r["window_end"]),
                "description": (r.get("description") or "")[:300],
            }
            for r in summaries
        ],
        "note": (
            "按相关度排序（关键词 + 语义融合），不是时间顺序。"
            "related_summaries 是叙述里提到该关键词的时间窗，可用 search_summaries 下钻。"
        ),
    }


async def get_recent_activity(
    db: Database,
    hours_back: int = 24,
    limit: int = 50,
    start_iso: str | None = None,
    end_iso: str | None = None,
    cursor: str | None = None,
    device_id: str | None = None,
    order: str = "asc",
) -> dict:
    """Chronological snapshot of activity (oldest→newest within the window).

    To pull a FULL day, keep the window fixed and re-call with the returned
    ``next_cursor`` as ``cursor`` until it comes back ``null`` (= last page).
    Explicit ``start_iso``/``end_iso`` (local time) take precedence over
    ``hours_back``.
    """
    hours_back = min(max(1, int(hours_back)), _MAX_HOURS)
    limit = min(max(1, int(limit)), _MAX_LIMIT)
    try:
        start_ms, end_ms = _resolve_window(hours_back, start_iso, end_iso)
        device_id = _scope_device(device_id)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        result = await activity_page(
            db,
            start=start_ms,
            end=end_ms,
            limit=limit,
            cursor=cursor,
            device_id=device_id,
            order=order,
            explicit_start=bool(start_iso),
            explicit_end=bool(end_iso),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    result["hours_back"] = hours_back
    return result


async def get_latest_activity(db: Database, device_id: str | None = None) -> dict:
    """Latest captured record per device, without paging or LLM calls."""
    try:
        device_id = _scope_device(device_id)
    except ValueError as exc:
        return {"error": str(exc)}
    return await latest_activity(db, device_id)


async def get_activity_overview(
    db: Database,
    period: str = "today",
    start_iso: str | None = None,
    end_iso: str | None = None,
    device_id: str | None = None,
    max_grain: str = "day",
    limit: int = 50,
) -> dict:
    """Per-device narrative overview with automatic coarse-to-fine fallback.

    Named periods use civil boundaries in the caller's view zone. Stored
    pyramid windows keep their own server zone / cut-hour; never rewrite them.
    """
    if max_grain not in GRAINS:
        return {"error": f"unknown grain: {max_grain}", "valid_grains": list(GRAINS)}
    try:
        start, end = _period_bounds(period, start_iso, end_iso, 0)
        device_id = _scope_device(device_id)
        result = await activity_overview(
            db,
            start=start,
            end=end,
            device_id=device_id,
            max_grain=max_grain,
            limit=min(max(1, int(limit)), _MAX_LIMIT),
        )
    except ValueError as exc:
        return {"error": str(exc)}
    result["period"] = period
    return result


def _stats_window(
    hours_back: int, period: str | None, start_iso: str | None, end_iso: str | None
) -> tuple[int, int, int]:
    """Window for the breakdown tools: a named ``period`` (civil, view zone,
    4AM cut — so "today" means today, not the last 24h) wins over
    ``hours_back``. Returns ``(start_ms, end_ms, hours_back)``; raises ValueError."""
    hours_back = min(max(1, int(hours_back)), _MAX_HOURS)
    if period:
        if period not in _VALID_PERIODS:
            raise ValueError(f"unknown period: {period}; valid: {list(_VALID_PERIODS)}")
        start_ms, end_ms = _period_bounds(period, start_iso, end_iso, 4)
        return start_ms, end_ms, hours_back
    start_ms, end_ms = _window(hours_back)
    return start_ms, end_ms, hours_back


async def get_app_breakdown(
    db: Database,
    hours_back: int = 24,
    top_n: int = 20,
    period: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    device_id: str | None = None,
) -> dict:
    """Aggregate active duration per app over the window (closed sessions only)."""
    top_n = min(max(1, int(top_n)), _MAX_TOP_N)
    try:
        start_ms, end_ms, hours_back = _stats_window(hours_back, period, start_iso, end_iso)
        device_id = _scope_device(device_id)
    except ValueError as exc:
        return {"error": str(exc)}
    dev_sql, dev_params = _device_sql(device_id)
    async with db.lock:
        async with db.conn.execute(
            # Per-record duration is clamped: negative spans floor to 0 and
            # implausible (>5min, sleep/lid-close) spans contribute 0 — see
            # _clamped_dur_sql. Otherwise a single 9h sleep gap dwarfs the day.
            # Per-record duration is clamped: negative spans floor to 0 and
            # implausible (>5min, sleep/lid-close) spans contribute 0 — see
            # _clamped_dur_sql. Otherwise a single 9h sleep gap dwarfs the day.
            # NOTE: inclusive BETWEEN (end == now) is intentional here; the
            # summary cascade uses half-open [start,end) for window TILING, but
            # these hours_back windows end at `now` so the only difference is a
            # frame captured in the exact query millisecond — which the cascade
            # still counts in its open current window, so totals agree.
            f"""SELECT app_name,
                      COUNT(*) AS records,
                      SUM({_clamped_dur_sql()}) AS total_ms
               FROM records
               WHERE ts_start BETWEEN ? AND ? AND app_name != ''{dev_sql}
               GROUP BY app_name
               ORDER BY total_ms DESC
               LIMIT ?""",
            (start_ms, end_ms, *dev_params, top_n),
        ) as cur:
            rows = await cur.fetchall()
    items = [
        {
            "app_name": r["app_name"],
            "records": r["records"],
            "total_seconds": int((r["total_ms"] or 0) / 1000),
        }
        for r in rows
    ]
    return {
        "hours_back": None if period else hours_back,
        "period": period,
        "device_id": device_id,
        "window_start_iso": ms_to_iso(start_ms),
        "window_end_iso": ms_to_iso(end_ms),
        "total_seconds": sum(it["total_seconds"] for it in items),
        "capped_per_record_seconds": _MAX_PLAUSIBLE_RECORD_MS // 1000,
        "items": items,
    }


async def get_category_stats(
    db: Database,
    hours_back: int = 24,
    top_n: int = 20,
    period: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    device_id: str | None = None,
) -> dict:
    """Aggregate active duration per category over the window.

    ``category_final`` is the AI-assigned label (rule override, else the VLM's
    pick). TWO buckets are deliberately kept distinct because conflating them
    produces misleading reports:

    - ``"_unclassified"`` (``is_unclassified``): records with NO category yet —
      never analyzed, or analyzed by an older worker that described but never
      classified them. This is a SYSTEM-INTERNAL backlog, not user behavior.
    - ``"uncategorized"`` (``is_genuinely_uncategorized``): the real catch-all
      category the classifier assigned when nothing fit.

    Per-record durations are clamped (see ``_clamped_dur_sql``) so sleep/lid-
    close artifacts don't inflate totals. ``categories_legend`` maps the flat-6
    ids to their 中文 names so a caller never has to guess what a category means.
    """
    top_n = min(max(1, int(top_n)), _MAX_TOP_N)
    try:
        start_ms, end_ms, hours_back = _stats_window(hours_back, period, start_iso, end_iso)
        device_id = _scope_device(device_id)
    except ValueError as exc:
        return {"error": str(exc)}
    dev_sql, dev_params = _device_sql(device_id, "r")
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
               WHERE r.ts_start BETWEEN ? AND ?{dev_sql}
               GROUP BY category
               ORDER BY total_ms DESC
               LIMIT ?""",
            (start_ms, end_ms, *dev_params, top_n),
        ) as cur:
            rows = await cur.fetchall()
    items = []
    for r in rows:
        cat = r["category"]
        item = {
            "category": cat,
            "records": r["records"],
            "total_seconds": int((r["total_ms"] or 0) / 1000),
        }
        if cat == _UNCLASSIFIED:
            item["is_unclassified"] = True
            item["note"] = _UNCLASSIFIED_NOTE
        elif cat == "uncategorized":
            item["is_genuinely_uncategorized"] = True
        items.append(item)
    return {
        "hours_back": None if period else hours_back,
        "period": period,
        "device_id": device_id,
        "window_start_iso": ms_to_iso(start_ms),
        "window_end_iso": ms_to_iso(end_ms),
        "total_seconds": sum(it["total_seconds"] for it in items),
        "capped_per_record_seconds": _MAX_PLAUSIBLE_RECORD_MS // 1000,
        "categories_legend": _CATEGORY_LEGEND,
        "items": items,
    }


# --------------------------------------------------------------------------- #
# query_stats — cascade-first aggregate stats over named periods               #
# --------------------------------------------------------------------------- #

_VALID_METRICS = (
    "seconds_by_category",
    "seconds_by_app",
    "seconds_by_device",
    "active_seconds",
    "record_count",
)
_VALID_PERIODS = ("today", "this_week", "this_month", "custom")


def _parse_iso_local(s: str) -> int:
    """Parse 'YYYY-MM-DD[ T]HH:MM[:SS]' in the view zone → epoch-ms.

    An explicit offset in the string (``…+09:00`` / ``Z``) wins over the zone.
    """
    return from_local(_dt.datetime.fromisoformat(s.strip()), view_tz())


def _period_bounds(
    period: str, start_iso: str | None, end_iso: str | None, cut_hour: int
) -> tuple[int, int]:
    """Resolve a named period to a half-open ``[start_ms, end_ms)`` (local time)."""
    now = int(time.time() * 1000)
    if period == "custom":
        if not start_iso or not end_iso:
            raise ValueError("period=custom requires start_iso and end_iso")
        start_ms, end_ms = _parse_iso_local(start_iso), _parse_iso_local(end_iso)
        if end_ms <= start_ms:
            raise ValueError("end_iso must be after start_iso")
        return start_ms, end_ms
    # Named periods are civil periods in the caller's view zone (the browser's,
    # when it sent one). When that zone equals the pyramid zone these bounds
    # coincide with stored day/week windows and query_stats can use the digest.
    tz = view_tz()
    if period == "today":
        return window_bounds(now, "day", cut_hour, tz=tz)[0], now
    if period == "this_week":
        return window_bounds(now, "week", cut_hour, tz=tz)[0], now
    if period == "this_month":
        d = to_local(now, tz)
        first = d.replace(day=1, hour=cut_hour, minute=0, second=0, microsecond=0)
        if first > d:  # before the cut on the 1st → still last month's logical span
            prev = (first - _dt.timedelta(days=1)).replace(day=1)
            first = prev
        return from_local(first, tz), now
    raise ValueError(f"unknown period: {period}")


async def _try_cascade_metrics(
    db: Database, start_ms: int, end_ms: int, cut_hour: int
) -> dict | None:
    """Return a finalized cascade window's metrics if ``[start_ms, end_ms)`` is
    EXACTLY a closed day/week window with a built row, else None (→ live path).

    Only closed windows (``window_end <= now``) qualify; an open window's row is
    partial and would undercount, so those always fall through to live SQL.
    """
    now = int(time.time() * 1000)
    if end_ms > now:
        return None
    for grain in ("week", "day"):
        if window_bounds(start_ms, grain, cut_hour) == (start_ms, end_ms):
            row = await db.get_summary(grain, scope_key(start_ms, grain, cut_hour))
            if row and row.get("metrics_json"):
                return json.loads(row["metrics_json"])
    return None


def _duration_semantics(metric: str) -> dict:
    """Self-describing note on what a duration number here IS — and is NOT.

    Durations are a FLOOR: only slices this device actually captured, with idle
    and uncaptured time excluded. An agent must phrase them as "本机捕获的活跃
    下限", never as total/wall-clock time, or it will say things like
    "娱乐 2.5 分钟/天" as if that were real total entertainment.
    """
    is_count = metric == "record_count"
    sem: dict = {
        "definition": (
            "本机捕获的记录条数（floor / 下限）。"
            if is_count
            else "本机捕获的活跃时长（floor / 下限），不是总时间，也不是墙钟时间。"
        ),
        "is_a_floor_because": [
            "只计本设备采集到的瞬时切片；采集间隔与 idle(>5min) 不计入。",
            "本设备未必全程在用户身边（关机、同步断档、多设备分摊都会少计）。",
        ],
        "how_to_phrase": (
            "如实说明这是『本机捕获的活跃下限』，不要表述成『总共/一共』。"
            "占比(share) 比绝对秒数可信；跨多天的小数字往往是设备覆盖不全，不是用户真没做。"
        ),
        "capped_per_record_seconds": _MAX_PLAUSIBLE_RECORD_MS // 1000,
    }
    if metric == "active_seconds":
        sem["aggregation"] = (
            "墙钟并集：重叠的采集区间已去重(union)，所以 active_seconds ≤ 各分类秒数之和。"
        )
    elif metric == "seconds_by_device":
        sem["aggregation"] = (
            "每台设备各自的切片之和；多台设备同时在用时，各设备相加会超过实际经过时间，"
            "实际经过时间请用 active_seconds（墙钟并集）。"
        )
    elif metric in ("seconds_by_category", "seconds_by_app"):
        sem["aggregation"] = (
            "各记录 clamp 后切片之和(可加、可跨层 SUM)；短暂重叠会被轻微重复计，"
            "略高于 active_seconds 的墙钟并集。"
        )
    return sem


async def query_stats(
    db: Database,
    metric: str = "seconds_by_category",
    period: str = "today",
    start_iso: str | None = None,
    end_iso: str | None = None,
    filter_category: str | None = None,
    top_n: int = 20,
    device_id: str | None = None,
) -> dict:
    """Aggregate time/count over a named period, cascade-first, ZERO heavy reading.

    Reads a precomputed ``summaries`` row when the period is exactly a finalized
    day/week window; otherwise aggregates live frames (pure SQL, self-correcting
    while classification is still in flight). Never touches ``vlm_desc``. Returns
    seconds (the cascade stores ms internally for exact additivity).

    ``active_seconds`` is special: always computed live as a wall-clock UNION
    (dedups overlapping captures), since union is not additive and so isn't
    rolled into the cascade. Every result carries a ``semantics`` block — these
    numbers are a captured-activity FLOOR, not total time.
    """
    # cut_hour default mirrors RollupConfig.cut_hour=4; TODO thread the config
    # through if a deployment ever changes the cut (period bounds must match the
    # cascade's window grid for the digest path to line up).
    cut_hour = 4
    if metric not in _VALID_METRICS:
        return {"error": f"unknown metric: {metric}", "valid_metrics": list(_VALID_METRICS)}
    if period not in _VALID_PERIODS:
        return {"error": f"unknown period: {period}", "valid_periods": list(_VALID_PERIODS)}
    try:
        start_ms, end_ms = _period_bounds(period, start_iso, end_iso, cut_hour)
        device_id = _scope_device(device_id)
    except ValueError as exc:
        return {"error": str(exc)}
    top_n = min(max(1, int(top_n)), _MAX_TOP_N)

    # function-local imports break the agent.tools ↔ summary.metrics cycle.
    if metric == "active_seconds":
        # Always live + union: dedups overlapping captures. Not from the cascade
        # (union is not additive across windows), and cheap enough live anyway.
        from timetrace.server.summary.metrics import active_wall_ms  # noqa: PLC0415

        wall_ms = await active_wall_ms(db, start_ms, end_ms, device_id=device_id)
        return {
            "metric": metric,
            "period": period,
            "device_id": device_id,
            "source": "live",
            "period_start_iso": ms_to_iso(start_ms),
            "period_end_iso": ms_to_iso(end_ms),
            "total_seconds": wall_ms // 1000,
            "semantics": _duration_semantics(metric),
        }

    # The cascade aggregates every device, so a device-scoped query is always
    # live; so is seconds_by_device over digest rows built before device_ms existed.
    m = None
    if device_id is None:
        m = await _try_cascade_metrics(db, start_ms, end_ms, cut_hour)
        if m is not None and metric == "seconds_by_device" and "device_ms" not in m:
            m = None
    if m is not None:
        source = "digest"
    else:
        from timetrace.server.summary.metrics import aggregate_frame_metrics  # noqa: PLC0415

        m = await aggregate_frame_metrics(db, start_ms, end_ms, device_id=device_id)
        source = "live"

    out: dict = {
        "metric": metric,
        "period": period,
        "device_id": device_id,
        "source": source,
        "period_start_iso": ms_to_iso(start_ms),
        "period_end_iso": ms_to_iso(end_ms),
        "timezone": view_tz_name(),
        "capped_per_record_seconds": _MAX_PLAUSIBLE_RECORD_MS // 1000,
        "semantics": _duration_semantics(metric),
    }
    if metric == "record_count":
        out["record_count"] = m["record_count"]
        return out

    if metric == "seconds_by_device":
        label = await _labeler(db)
        key_ms = {
            label(None if k == "_unknown_device" else k): v
            for k, v in (m.get("device_ms") or {}).items()
        }
    else:
        key_ms = m["cat_ms"] if metric == "seconds_by_category" else m["app_ms"]
    if metric == "seconds_by_category":
        out["categories_legend"] = _CATEGORY_LEGEND
    if filter_category:
        key_ms = {k: v for k, v in key_ms.items() if k == filter_category}
    total_ms = sum(key_ms.values())
    items = []
    for key, ms in sorted(key_ms.items(), key=lambda kv: kv[1], reverse=True)[:top_n]:
        item = {
            "key": key,
            "total_seconds": ms // 1000,
            "share": round(ms / total_ms, 4) if total_ms else 0.0,
        }
        if metric == "seconds_by_category" and key == _UNCLASSIFIED:
            item["is_unclassified"] = True
            item["note"] = _UNCLASSIFIED_NOTE
        elif metric == "seconds_by_category" and key == "uncategorized":
            item["is_genuinely_uncategorized"] = True
        items.append(item)
    out["items"] = items
    out["total_seconds"] = total_ms // 1000  # over ALL keys, not just top_n
    return out


def _top_cats(metrics: dict, top_n: int = 3) -> list[dict]:
    """Top categories of a window's metrics by captured seconds (+ share)."""
    cat = metrics.get("cat_ms") or {}
    total = sum(cat.values())
    return [
        {
            "category": k,
            "seconds": ms // 1000,
            "share": round(ms / total, 3) if total else 0.0,
        }
        for k, ms in sorted(cat.items(), key=lambda kv: -kv[1])[:top_n]
    ]


async def search_summaries(
    db: Database,
    grain: str = "day",
    period: str = "today",
    start_iso: str | None = None,
    end_iso: str | None = None,
    query: str | None = None,
    limit: int = 50,
    device_id: str | None = None,
) -> dict:
    """Read the narrative pyramid: per-window 流水账/重点/评价 at a chosen grain.

    The FOLDING entry point for "what did I do". Start coarse (``grain='day'``)
    for an overview, then drill into an interesting window by calling again with
    a finer grain (returned as ``drill_down_grain``) and that window's
    ``window_start_iso`` / ``window_end_iso`` as ``period='custom'`` bounds — the
    time range IS the parent→child link. Optional ``query`` filters windows whose
    description / key_points / evaluation contain the substring.

    Durations inside are a captured-activity FLOOR, not total wall-clock time.
    """
    cut_hour = 4  # mirrors RollupConfig.cut_hour; see query_stats note
    if grain not in GRAINS:
        return {"error": f"unknown grain: {grain}", "valid_grains": list(GRAINS)}
    if period not in _VALID_PERIODS:
        return {"error": f"unknown period: {period}", "valid_periods": list(_VALID_PERIODS)}
    try:
        start_ms, end_ms = _period_bounds(period, start_iso, end_iso, cut_hour)
        device_id = _scope_device(device_id)
    except ValueError as exc:
        return {"error": str(exc)}
    limit = min(max(1, int(limit)), _MAX_LIMIT)

    q = (query or "").strip()
    # Global rows are composed per device (labelled sections); device_id reads
    # that device's own pyramid instead.
    if q and device_id is not None:
        rows = await db.search_device_summaries_text(
            q, start_ms, end_ms, device_scope=device_id, grains=[grain], limit=limit + 1
        )
    elif q:
        # Indexed narrative search (summaries_fts, BM25) instead of scanning
        # every window's text in Python; overlap semantics so a coarse window
        # containing the period still matches.
        rows = await db.search_summaries_text(q, start_ms, end_ms, grains=[grain], limit=limit + 1)
    elif device_id is not None:
        rows = await db.get_device_summaries(
            grain, start_ms, end_ms, device_scope=device_id, overlap=True
        )
    else:
        rows = await db.get_summaries_overlapping(grain, start_ms, end_ms)
    items: list[dict] = []
    for r in rows:
        metrics = json.loads(r.get("metrics_json") or "{}")
        items.append(
            {
                **summary_item(r, int(time.time() * 1000)),
                "day_local": r.get("day_local"),
                "top_categories": _top_cats(metrics),
                "record_count": metrics.get("record_count", 0),
                "compression_ratio": r.get("compression_ratio"),
            }
        )
        if len(items) >= limit:
            break

    out: dict = {
        "grain": grain,
        "period": period,
        "period_start_iso": ms_to_iso(start_ms),
        "period_end_iso": ms_to_iso(end_ms),
        "timezone": view_tz_name(),
        "device_id": device_id,
        "count": len(items),
        "has_more": len(rows) > limit,
        "order": "relevance" if q else "asc",
        "coverage_scope": "returned_windows_only",
        "ready_count": sum(i["availability"] == "ready" for i in items),
        "pending_count": sum(i["availability"] == "pending_summary" for i in items),
        "open_count": sum(not i["window_closed"] for i in items),
        "overview_tool": "get_activity_overview",
        "items": items,
        "note": (
            "叙述层：每个时间窗的流水账(description)/重点(key_points)/评价(evaluation)。"
            "时长是『本机捕获活跃下限』，不是总时间。metrics_only=true 表示该窗无截图、"
            "仅指标无叙述。窗口可能超出请求范围，起止以各条返回值为准。"
            "has_more=true 表示结果截断，不能当成完整覆盖；缩小时间范围继续查。"
            "需自动补足未完成父层时使用 get_activity_overview。" + PRESENCE_NOTE
        ),
    }
    child = CHILD_OF.get(grain)
    if child:
        out["drill_down_grain"] = child
        out["drill_down_hint"] = (
            f"想看某窗更细的内容：grain='{child}' + period='custom' + 该窗的 "
            f"window_start_iso/window_end_iso 再调一次。"
        )
    return out


async def list_devices(db: Database) -> dict:
    """The user's capture devices: id, display name and the owner's own
    description (e.g. "办公室台式机，主要写代码"), so questions like "在家用电脑
    上做了什么" can be mapped to a ``device_id`` scope."""
    label = await _labeler(db)
    items = [
        {
            "device_id": d["id"],
            "name": label(d["id"]),
            "description": d["description"] or d["reported_description"] or "",
            "last_seen_iso": ms_to_iso(d["last_seen_at"]),
            "revoked": d["revoked_at"] is not None,
        }
        for d in await db.list_devices()
    ]
    unknown = await db.count_unknown_device_records()
    out: dict = {"count": len(items), "items": items}
    if unknown:
        out["unknown_device"] = {
            "device_id": "_unknown_device",
            "name": "未知设备",
            "record_count": unknown,
            "note": "设备身份上线前的历史记录，不猜测其归属。",
        }
    return out


# --------------------------------------------------------------------------- #
# Write tool (the ONLY one) — label only, never destructive                    #
# --------------------------------------------------------------------------- #


async def apply_label(
    db: Database,
    record_id: str,
    category: str,
    note: str | None = None,
) -> dict:
    """Set/replace a record's category label. The agent's only write surface.

    Accepts a category id (``work``) or its display name (``工作``).
    Writes a ``feedback`` audit row (action=edit) AND authoritatively sets
    ``category_final`` (creating the analysis row if the worker hasn't run yet).
    Returns an ``error`` dict (not an exception) on unknown record/category so
    the model can self-correct in the loop.
    """
    if await db.get_record_ts_start(record_id) is None:
        return {"error": f"unknown record_id: {record_id}"}
    cats = await db.get_categories(include_hidden=True)
    by_id = {c["id"] for c in cats}
    by_name = {c["name"]: c["id"] for c in cats}
    resolved = category if category in by_id else by_name.get(category)
    if resolved is None:
        return {"error": f"unknown category: {category}", "valid_categories": sorted(by_id)}
    before = await db.get_category_final(record_id)
    await db.set_category_final(record_id, resolved)
    feedback_id = await db.insert_feedback(
        record_id=record_id,
        action="edit",
        category_before=before,
        category_after=resolved,
        user_note=note,
    )
    logger.info("agent.apply_label", record_id=record_id, before=before, after=resolved)
    return {
        "status": "labeled",
        "record_id": record_id,
        "category_before": before,
        "category_after": resolved,
        "feedback_id": feedback_id,
    }


# --------------------------------------------------------------------------- #
# OpenAI-style tool schemas + dispatch (for the agent loop)                    #
# --------------------------------------------------------------------------- #


def _int_param(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def _str_param(desc: str) -> dict:
    return {"type": "string", "description": desc}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_activity",
            "description": (
                "按内容检索活动记录：关键词（标题/应用/进程/URL/AI 描述）+ 语义相似度融合，"
                "同义说法也能命中。结果按相关度排序；related_summaries 给出叙述里提到它的时间窗。"
                "找『什么时候做过 X / 看过 X』用它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": _str_param(
                        "可选：只看某台设备（list_devices 返回的 device_id；"
                        "_unknown_device=身份上线前的旧记录）。不传=全部设备"
                    ),
                    "query": {
                        "type": "string",
                        "description": "要找的内容，可以是关键词或一句描述",
                    },
                    "limit": _int_param("返回条数，默认 20，最大 2000"),
                    "hours_back": _int_param("只看最近 N 小时，不传则全时段"),
                    "start_iso": _str_param("绝对起始时间（本地），优先于 hours_back"),
                    "end_iso": _str_param("绝对结束时间（本地）"),
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_activity",
            "description": (
                "逐页读原始记录，默认最早在前；页尾不是最新活动。"
                "当前近况用 get_latest_activity，时段总览用 get_activity_overview。"
                "has_more=true 必须继续分页，不能据此声称后面没记录或用户离线。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": _str_param(
                        "可选：只看某台设备（list_devices 返回的 device_id；"
                        "_unknown_device=身份上线前的旧记录）。不传=全部设备"
                    ),
                    "hours_back": _int_param("窗口小时数，默认 24，最大 720"),
                    "limit": _int_param("返回条数，默认 50，最大 2000"),
                    "start_iso": _str_param("绝对起始时间（本地），优先于 hours_back"),
                    "end_iso": _str_param("绝对结束时间（本地）"),
                    "cursor": _str_param("翻页游标：传回上次的 next_cursor 取下一页"),
                    "order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "默认 asc，最早在前；desc 最新在前，翻页时保持不变",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_app_breakdown",
            "description": (
                "统计某时间窗内每个应用的累计时长（秒），降序。"
                "『今天/本周』请传 period，不要用 hours_back 近似。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": _str_param(
                        "可选：只看某台设备（list_devices 返回的 device_id；"
                        "_unknown_device=身份上线前的旧记录）。不传=全部设备"
                    ),
                    "period": {
                        "type": "string",
                        "enum": list(_VALID_PERIODS),
                        "description": "today/this_week/this_month/custom；给了就优先于 hours_back",
                    },
                    "start_iso": _str_param("period=custom 起，本地 'YYYY-MM-DD HH:MM:SS'"),
                    "end_iso": _str_param("period=custom 止（不含）"),
                    "hours_back": _int_param("最近 N 小时（滚动窗口），默认 24，最大 720"),
                    "top_n": _int_param("返回前 N 个应用，默认 20，最大 50"),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_category_stats",
            "description": (
                "统计某时间窗内每个分类的累计时长（秒），降序，时长已对单条记录封顶剔除休眠伪影。"
                "返回里 _unclassified(is_unclassified) 是『尚未分类的系统内部积压』、"
                "与真正的 uncategorized 分类不是一回事；categories_legend 给出分类 id→中文名。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": _str_param(
                        "可选：只看某台设备（list_devices 返回的 device_id；"
                        "_unknown_device=身份上线前的旧记录）。不传=全部设备"
                    ),
                    "period": {
                        "type": "string",
                        "enum": list(_VALID_PERIODS),
                        "description": "today/this_week/this_month/custom；给了就优先于 hours_back",
                    },
                    "start_iso": _str_param("period=custom 起，本地 'YYYY-MM-DD HH:MM:SS'"),
                    "end_iso": _str_param("period=custom 止（不含）"),
                    "hours_back": _int_param("最近 N 小时（滚动窗口），默认 24，最大 720"),
                    "top_n": _int_param("返回前 N 个分类，默认 20，最大 50"),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_stats",
            "description": (
                "按命名时段聚合时长/计数，优先读预算好的金字塔摘要、否则现算原始帧（纯 SQL，"
                "不读 AI 描述）。问『这个月/这周/今天 在某分类或某应用上花了多少』先用它。"
                "返回秒；source=digest 表示命中预聚合、live 表示现算。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": _str_param(
                        "可选：只看某台设备（list_devices 返回的 device_id；"
                        "_unknown_device=身份上线前的旧记录）。不传=全部设备"
                    ),
                    "metric": {
                        "type": "string",
                        "enum": list(_VALID_METRICS),
                        "description": "统计口径：按分类/按应用秒数、活跃总秒数、记录数",
                    },
                    "period": {
                        "type": "string",
                        "enum": list(_VALID_PERIODS),
                        "description": "today/this_week/this_month/custom",
                    },
                    "start_iso": {
                        "type": "string",
                        "description": "custom 起，本地 'YYYY-MM-DD HH:MM:SS'",
                    },
                    "end_iso": {
                        "type": "string",
                        "description": "custom 止（不含）",
                    },
                    "filter_category": {
                        "type": "string",
                        "description": "可选：只看某一个分类 id",
                    },
                    "top_n": _int_param("返回前 N 项，默认 20，最大 50"),
                },
                "required": ["metric", "period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_summaries",
            "description": (
                "读『金字塔』叙述层：某时间窗的流水账/重点/评价（按 5min→1h→6h→day→week 分层）。"
                "精确粒度查询或总览后的下钻用它；总览优先 get_activity_overview 自动补细层。"
                "按 drill_down_grain + 该窗 window_start_iso/window_end_iso 下钻细看。"
                "检查 availability/window_closed/has_more，未完成或截断不能当成无活动。"
                "比逐条 search_activity 高效得多。时长是『本机捕获活跃下限』。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device_id": _str_param(
                        "可选：只读某台设备自己的叙述金字塔（list_devices 的 device_id）。"
                        "不传=全局视图（多设备时按设备分段）"
                    ),
                    "grain": {
                        "type": "string",
                        "enum": list(GRAINS),
                        "description": "粒度：5min/1h/6h/day/week，总览用 day 或 week",
                    },
                    "period": {
                        "type": "string",
                        "enum": list(_VALID_PERIODS),
                        "description": "today/this_week/this_month/custom",
                    },
                    "start_iso": _str_param(
                        "custom 起，本地 'YYYY-MM-DD HH:MM:SS'；下钻时传上层窗口的 start"
                    ),
                    "end_iso": _str_param("custom 止（不含）；下钻时传上层窗口的 end"),
                    "query": _str_param("可选：只返回叙述里含该关键词的窗口（全文索引，按相关度）"),
                    "limit": _int_param("返回窗口数，默认 50，最大 2000"),
                },
                "required": ["grain", "period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_devices",
            "description": (
                "列出用户的采集设备（device_id、名称、用户写的设备描述）。"
                "问题涉及某台电脑（如『办公电脑』『家里那台』）时先用它找到 device_id，"
                "再把 device_id 传给其它工具。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_label",
            "description": (
                "给某条记录打/改分类标签。这是你唯一的写权限，不能删除或修改记录本身。"
                "record_id 从检索结果的 id 字段取；category 用分类 id 或中文名。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "目标记录 id"},
                    "category": {"type": "string", "description": "分类 id 或中文名"},
                    "note": {"type": "string", "description": "可选：打标签的理由"},
                },
                "required": ["record_id", "category"],
            },
        },
    },
]

TOOL_SCHEMAS.extend(
    [
        {
            "type": "function",
            "function": {
                "name": "get_latest_activity",
                "description": (
                    "查每台设备最新采集记录，无需翻页；区分采集、首次入库、最后联系与分析状态。"
                    "不能据此断言用户在线或离线。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device_id": _str_param(
                            "可选：list_devices 的设备 ID；省略返回各设备，包括未知设备"
                        )
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_activity_overview",
                "description": (
                    "回答今天/这周做了什么的首选。分设备读分级摘要，粗层未完成时自动补细层；"
                    "返回覆盖缺口和截断标志，不调用模型。"
                    "缺口按 fallback_hint 继续：全设备可补全局摘要，指定设备保持筛选读原始记录。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "period": {
                            "type": "string",
                            "enum": list(_VALID_PERIODS),
                            "description": "默认 today；自然日从零点起",
                        },
                        "start_iso": _str_param("custom 必填：起始时间，可带时区偏移"),
                        "end_iso": _str_param("custom 必填：结束时间，不包含该时刻；最多31天"),
                        "device_id": _str_param("可选设备 ID；不传按每台设备分别返回"),
                        "max_grain": {
                            "type": "string",
                            "enum": list(GRAINS),
                            "description": "最粗粒度，默认 day；自动下钻至5min",
                        },
                        "limit": _int_param(
                            "每设备最多返回窗口数，默认50，最大2000；截断时缩小范围"
                        ),
                    },
                },
            },
        },
    ]
)

_IMPLS = {
    "search_activity": search_activity,
    "get_recent_activity": get_recent_activity,
    "get_latest_activity": get_latest_activity,
    "get_activity_overview": get_activity_overview,
    "get_app_breakdown": get_app_breakdown,
    "get_category_stats": get_category_stats,
    "query_stats": query_stats,
    "search_summaries": search_summaries,
    "list_devices": list_devices,
    "apply_label": apply_label,
}


# Tools that use the text-embedding endpoint; the dependency is injected by the
# caller (never taken from model-supplied arguments).
_NEEDS_EMBEDDING = {"search_activity"}


async def dispatch_tool(
    db: Database, name: str, arguments: dict, *, embedding: EmbeddingClient | None = None
) -> dict:
    """Run a tool by name with a (parsed) arguments dict. Never raises — returns
    an ``error`` dict so the agent loop can feed it back and self-correct."""
    fn = _IMPLS.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    arguments = {k: v for k, v in arguments.items() if k != "embedding"}
    try:
        if name in _NEEDS_EMBEDDING:
            return await fn(db, **arguments, embedding=embedding)
        return await fn(db, **arguments)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent.tool_failed", tool=name)
        return {"error": f"tool {name} failed: {exc}"}
