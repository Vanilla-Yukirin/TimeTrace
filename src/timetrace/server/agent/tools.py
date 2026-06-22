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

# Window math is pure (server/summary/windows.py imports only datetime), so this
# is cycle-free. aggregate_frame_metrics is imported function-locally in
# query_stats instead, because server/summary/metrics.py imports THIS module
# (_clamped_dur_sql/_UNCLASSIFIED) — a top-level import there would be circular.
from timetrace.server.summary.windows import scope_key, window_bounds

if TYPE_CHECKING:
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

_MAX_LIMIT = 200
_MAX_TOP_N = 50
_MAX_HOURS = 720  # 30 days

# A single record spanning more than this is a stale boundary artifact (laptop
# sleep / lid-close left the last pre-sleep record open until wake), NOT real
# activity time. Capture emits a heartbeat at least every 30s while active and
# idle closes a record after 180s, so nothing legitimate exceeds this. Mirrors
# the DB-side ``_cap_implausible_record_durations`` cap (db/sqlite.py) on the
# READ path so stats/reports don't inflate by ~9h sleep gaps in the window
# between server restarts (the DB cap only runs at init()).
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
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))


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


# --------------------------------------------------------------------------- #
# Read tools                                                                   #
# --------------------------------------------------------------------------- #


async def search_activity(
    db: Database,
    query: str,
    limit: int = 20,
    hours_back: int | None = None,
) -> dict:
    """Keyword search over activity records (FTS5 trigram / multi-field LIKE)."""
    limit = min(max(1, int(limit)), 100)
    start_ms, end_ms = _window(hours_back)
    rows = await db.query_records(start_ms=start_ms, end_ms=end_ms, limit=limit, keyword=query)
    return {
        "query": query,
        "items": [
            {
                "id": r["id"],
                "ts_start_iso": ms_to_iso(r.get("ts_start")),
                "ts_end_iso": ms_to_iso(r.get("ts_end")),
                "app_name": r.get("app_name"),
                "window_title": r.get("window_title"),
                "vlm_desc": r.get("vlm_desc"),
                "category": r.get("category_final"),
            }
            for r in rows
        ],
    }


async def get_recent_activity(db: Database, hours_back: int = 24, limit: int = 50) -> dict:
    """Chronological snapshot of recent activity (no keyword filter)."""
    hours_back = min(max(1, int(hours_back)), _MAX_HOURS)
    limit = min(max(1, int(limit)), _MAX_LIMIT)
    start_ms, end_ms = _window(hours_back)
    rows = await db.query_records(start_ms=start_ms, end_ms=end_ms, limit=limit)
    return {
        "hours_back": hours_back,
        "items": [
            {
                "id": r["id"],
                "ts_start_iso": ms_to_iso(r.get("ts_start")),
                "app_name": r.get("app_name"),
                "window_title": r.get("window_title"),
                "vlm_desc": (r.get("vlm_desc") or "")[:200],
                "category": r.get("category_final"),
            }
            for r in rows
        ],
    }


async def get_app_breakdown(db: Database, hours_back: int = 24, top_n: int = 20) -> dict:
    """Aggregate active duration per app over the window (closed sessions only)."""
    hours_back = min(max(1, int(hours_back)), _MAX_HOURS)
    top_n = min(max(1, int(top_n)), _MAX_TOP_N)
    start_ms, end_ms = _window(hours_back)
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
               WHERE ts_start BETWEEN ? AND ? AND app_name != ''
               GROUP BY app_name
               ORDER BY total_ms DESC
               LIMIT ?""",
            (start_ms, end_ms, top_n),
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
        "hours_back": hours_back,
        "total_seconds": sum(it["total_seconds"] for it in items),
        "capped_per_record_seconds": _MAX_PLAUSIBLE_RECORD_MS // 1000,
        "items": items,
    }


async def get_category_stats(db: Database, hours_back: int = 24, top_n: int = 20) -> dict:
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
    hours_back = min(max(1, int(hours_back)), _MAX_HOURS)
    top_n = min(max(1, int(top_n)), _MAX_TOP_N)
    start_ms, end_ms = _window(hours_back)
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
               WHERE r.ts_start BETWEEN ? AND ?
               GROUP BY category
               ORDER BY total_ms DESC
               LIMIT ?""",
            (start_ms, end_ms, top_n),
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
        "hours_back": hours_back,
        "total_seconds": sum(it["total_seconds"] for it in items),
        "capped_per_record_seconds": _MAX_PLAUSIBLE_RECORD_MS // 1000,
        "categories_legend": _CATEGORY_LEGEND,
        "items": items,
    }


# --------------------------------------------------------------------------- #
# query_stats — cascade-first aggregate stats over named periods               #
# --------------------------------------------------------------------------- #

_VALID_METRICS = ("seconds_by_category", "seconds_by_app", "active_seconds", "record_count")
_VALID_PERIODS = ("today", "this_week", "this_month", "custom")


def _parse_iso_local(s: str) -> int:
    """Parse 'YYYY-MM-DD[ T]HH:MM[:SS]' as LOCAL time → epoch-ms."""
    return int(_dt.datetime.fromisoformat(s.strip()).timestamp() * 1000)


def _period_bounds(period: str, start_iso: str | None, end_iso: str | None, cut_hour: int) -> tuple[
    int, int
]:
    """Resolve a named period to a half-open ``[start_ms, end_ms)`` (local time)."""
    now = int(time.time() * 1000)
    if period == "custom":
        if not start_iso or not end_iso:
            raise ValueError("period=custom requires start_iso and end_iso")
        start_ms, end_ms = _parse_iso_local(start_iso), _parse_iso_local(end_iso)
        if end_ms <= start_ms:
            raise ValueError("end_iso must be after start_iso")
        return start_ms, end_ms
    if period == "today":
        return window_bounds(now, "day", cut_hour)[0], now
    if period == "this_week":
        return window_bounds(now, "week", cut_hour)[0], now
    if period == "this_month":
        d = _dt.datetime.fromtimestamp(now / 1000)
        return int(_dt.datetime(d.year, d.month, 1, cut_hour).timestamp() * 1000), now
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
    except ValueError as exc:
        return {"error": str(exc)}
    top_n = min(max(1, int(top_n)), _MAX_TOP_N)

    # function-local imports break the agent.tools ↔ summary.metrics cycle.
    if metric == "active_seconds":
        # Always live + union: dedups overlapping captures. Not from the cascade
        # (union is not additive across windows), and cheap enough live anyway.
        from timetrace.server.summary.metrics import active_wall_ms  # noqa: PLC0415

        wall_ms = await active_wall_ms(db, start_ms, end_ms)
        return {
            "metric": metric,
            "period": period,
            "source": "live",
            "period_start_iso": ms_to_iso(start_ms),
            "period_end_iso": ms_to_iso(end_ms),
            "total_seconds": wall_ms // 1000,
            "semantics": _duration_semantics(metric),
        }

    m = await _try_cascade_metrics(db, start_ms, end_ms, cut_hour)
    if m is not None:
        source = "digest"
    else:
        from timetrace.server.summary.metrics import aggregate_frame_metrics  # noqa: PLC0415

        m = await aggregate_frame_metrics(db, start_ms, end_ms)
        source = "live"

    out: dict = {
        "metric": metric,
        "period": period,
        "source": source,
        "period_start_iso": ms_to_iso(start_ms),
        "period_end_iso": ms_to_iso(end_ms),
        "capped_per_record_seconds": _MAX_PLAUSIBLE_RECORD_MS // 1000,
        "semantics": _duration_semantics(metric),
    }
    if metric == "record_count":
        out["record_count"] = m["record_count"]
        return out

    key_ms: dict[str, int] = m["cat_ms"] if metric == "seconds_by_category" else m["app_ms"]
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


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_activity",
            "description": "按关键词检索活动记录（标题/应用/进程/URL/AI 描述）。找具体内容用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "limit": _int_param("返回条数，默认 20，最大 100"),
                    "hours_back": _int_param("只看最近 N 小时，不传则全时段"),
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_activity",
            "description": "按时间顺序拉取最近活动快照（无关键词），用于先总览。",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": _int_param("窗口小时数，默认 24，最大 720"),
                    "limit": _int_param("返回条数，默认 50，最大 200"),
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_app_breakdown",
            "description": "统计某时间窗内每个应用的累计时长（秒），降序。",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": _int_param("窗口小时数，默认 24，最大 720"),
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
                    "hours_back": _int_param("窗口小时数，默认 24，最大 720"),
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

_IMPLS = {
    "search_activity": search_activity,
    "get_recent_activity": get_recent_activity,
    "get_app_breakdown": get_app_breakdown,
    "get_category_stats": get_category_stats,
    "query_stats": query_stats,
    "apply_label": apply_label,
}


async def dispatch_tool(db: Database, name: str, arguments: dict) -> dict:
    """Run a tool by name with a (parsed) arguments dict. Never raises — returns
    an ``error`` dict so the agent loop can feed it back and self-correct."""
    fn = _IMPLS.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return await fn(db, **arguments)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent.tool_failed", tool=name)
        return {"error": f"tool {name} failed: {exc}"}
