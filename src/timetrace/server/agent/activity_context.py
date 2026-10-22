"""Read-only, self-describing activity contracts for Agent and MCP callers.

No screenshot access and no on-demand LLM generation. A missing observation
is never evidence of a person's presence/absence.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import time
from bisect import bisect_left
from typing import TYPE_CHECKING

from timetrace.server.db.sqlite import UNKNOWN_DEVICE
from timetrace.server.summary.windows import CHILD_OF
from timetrace.server.tz import view_tz, view_tz_name

if TYPE_CHECKING:
    from timetrace.server.db import Database

PRESENCE_NOTE = (
    "记录只证明某设备曾采集到活动，不证明用户本人在线或离线。"
    "分页未读完、摘要未生成、描述为空、采集或上传延迟都不能用来推断离线。"
)
_CURSOR_PREFIX = "activity:v1:"
_RECORD_SELECT = """SELECT r.*, a.vlm_desc, a.category_final,
    a.status AS analysis_status, a.done_at AS analysis_done_at
    FROM records r LEFT JOIN analysis_results a ON a.record_id=r.id"""


def iso(ms: int | None) -> str | None:
    """Offset-bearing, millisecond-preserving timestamps for round trips."""
    if ms is None:
        return None
    local = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).astimezone(view_tz())
    return local.isoformat(timespec="milliseconds")


def device_clause(device_id: str | None, alias: str = "r") -> tuple[str, list]:
    if device_id == UNKNOWN_DEVICE:
        return f" AND {alias}.device_id IS NULL", []
    if device_id is not None:
        return f" AND {alias}.device_id=?", [device_id]
    return "", []


def record_item(row: dict, labels: dict) -> dict:
    did = row.get("device_id") or UNKNOWN_DEVICE
    return {
        "id": row["id"],
        "device_id": did,
        "device": device_label(did, labels),
        "ts_start_iso": iso(row["ts_start"]),
        "ts_end_iso": iso(row.get("ts_end")),
        "server_received_at": iso(row.get("created_at")),
        "app_name": row.get("app_name"),
        "window_title": row.get("window_title"),
        "vlm_desc": (row.get("vlm_desc") or "")[:200],
        "category": row.get("category_final"),
        "analysis_status": row.get("analysis_status") or "not_available",
        "analysis_done_at": iso(row.get("analysis_done_at")),
        "description_available": bool((row.get("vlm_desc") or "").strip()),
    }


def device_label(did: str, labels: dict) -> str:
    return labels.get(did) or ("未知设备" if did == UNKNOWN_DEVICE else f"设备 {did[:8]}")


async def activity_page(
    db: Database,
    *,
    start: int,
    end: int,
    limit: int,
    cursor: str | None,
    device_id: str | None,
    order: str,
    explicit_start: bool,
    explicit_end: bool,
) -> dict:
    """Keyset pagination with fixed window + insertion high-water mark.

    Opaque cursors are selectors, not credentials. Values are validated and
    parameterized. Existing record-ID cursors remain readable for compatibility.
    Bounds remain inclusive for compatibility. Restart after deletion/VACUUM:
    implicit rowids are an insertion high-water mark, not a durable snapshot.
    """
    if order not in ("asc", "desc"):
        raise ValueError("order must be asc or desc")
    after = None
    snapshot = None
    if cursor and cursor.startswith(_CURSOR_PREFIX):
        try:
            raw = cursor[len(_CURSOR_PREFIX) :]
            if len(raw) > 4096:
                raise ValueError
            c = json.loads(base64.urlsafe_b64decode(raw).decode())
            if not isinstance(c, dict):
                raise ValueError
            if any(type(c[k]) is not int for k in ("start", "end", "snapshot", "ts")):
                raise ValueError
            if not (0 <= c["start"] < c["end"] <= 253402300799999):
                raise ValueError
            if not (0 <= c["snapshot"] <= 2**63 - 1 and c["start"] <= c["ts"] <= c["end"]):
                raise ValueError
            if not isinstance(c["id"], str) or not (1 <= len(c["id"]) <= 128):
                raise ValueError
            if c["device"] != device_id or c["order"] != order:
                raise ValueError
            if (explicit_start and start != c["start"]) or (explicit_end and end != c["end"]):
                raise ValueError
            start, end, snapshot = c["start"], c["end"], c["snapshot"]
            after = (c["ts"], c["id"])
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ValueError("invalid cursor or cursor scope/order/window mismatch") from exc
    clause, params = device_clause(device_id)
    async with db.lock:
        if snapshot is None:
            async with db.conn.execute("SELECT COALESCE(MAX(rowid),0) FROM records") as cur:
                snapshot = (await cur.fetchone())[0]
        if cursor and after is None:
            async with db.conn.execute(
                "SELECT r.ts_start,r.id FROM records r WHERE r.id=? "
                "AND r.ts_start BETWEEN ? AND ?" + clause,
                (cursor, start, end, *params),
            ) as cur:
                anchor = await cur.fetchone()
            if anchor is None:
                raise ValueError("unknown cursor or cursor outside requested scope/window")
            after = (anchor["ts_start"], anchor["id"])
        where = " WHERE r.ts_start BETWEEN ? AND ? AND r.rowid<=?" + clause
        args = [start, end, snapshot, *params]
        if after:
            where += " AND (r.ts_start,r.id) " + (">" if order == "asc" else "<") + " (?,?)"
            args.extend(after)
        async with db.conn.execute(
            _RECORD_SELECT + where + f" ORDER BY r.ts_start {order},r.id {order} LIMIT ?",
            (*args, limit + 1),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        c = dict(
            start=start,
            end=end,
            snapshot=snapshot,
            device=device_id,
            order=order,
            ts=rows[-1]["ts_start"],
            id=rows[-1]["id"],
        )
        next_cursor = _CURSOR_PREFIX + base64.urlsafe_b64encode(json.dumps(c).encode()).decode()
    labels = await db.device_labels()
    return {
        "count": len(rows),
        "items": [record_item(r, labels) for r in rows],
        "next_cursor": next_cursor,
        "has_more": more,
        "order": order,
        "window_start_iso": iso(start),
        "window_end_iso": iso(end),
        "window_bounds": "inclusive",
        "timezone": view_tz_name(),
        "device_id": device_id,
        "page_first_at": iso(rows[0]["ts_start"]) if rows else None,
        "page_last_at": iso(rows[-1]["ts_start"]) if rows else None,
        "snapshot_semantics": "固定查询时间窗和首次读取时已有的记录；分析结果仍可能更新。",
        "note": "这是分页结果，页尾不是全局最新记录；查询最新活动请用 get_latest_activity。"
        + PRESENCE_NOTE,
    }


async def latest_activity(db: Database, device_id: str | None) -> dict:
    now = int(time.time() * 1000)
    devices = {d["id"]: d for d in await db.list_devices()}
    labels = await db.device_labels()
    # Include legacy/unknown devices, as well as registered devices with no data.
    scopes = {device_id} if device_id is not None else set(devices)
    if device_id is None:
        async with db.lock:
            # The non-NULL partial index covers orphaned device IDs too.
            async with db.conn.execute(
                "SELECT DISTINCT device_id FROM records WHERE device_id IS NOT NULL"
            ) as cur:
                scopes.update(r[0] for r in await cur.fetchall())
            async with db.conn.execute(
                "SELECT 1 FROM records WHERE device_id IS NULL LIMIT 1"
            ) as cur:
                if await cur.fetchone():
                    scopes.add(UNKNOWN_DEVICE)
    items = []
    for did in sorted(scopes):
        clause, params = device_clause(did)
        async with db.lock:
            async with db.conn.execute(
                _RECORD_SELECT
                + " WHERE 1=1"
                + clause
                + " ORDER BY r.ts_start DESC,r.id DESC LIMIT 1",
                params,
            ) as cur:
                row = await cur.fetchone()
        d = devices.get(did, {})
        r = dict(row) if row else None
        age = (now - r["ts_start"]) / 1000 if r else None
        items.append(
            {
                "device_id": did,
                "device": device_label(did, labels),
                "device_description": d.get("description") or d.get("reported_description") or "",
                "revoked": d.get("revoked_at") is not None,
                "last_contact_at": iso(d.get("last_seen_at") or None),
                "latest_record": record_item(r, labels) if r else None,
                "capture_age_seconds": age,
                "capture_clock_ahead": age is not None and age < 0,
                "data_status": "record_available" if r else "no_records",
                "presence": "unknown",
            }
        )
    return {
        "as_of": iso(now),
        "timezone": view_tz_name(),
        "count": len(items),
        "items": items,
        "note": PRESENCE_NOTE + " last_contact_at 是设备最后联系服务端时间，"
        "server_received_at 是该记录首次入库时间；积压上传不等于刚刚发生的活动。",
    }


def summary_item(row: dict, now: int) -> dict:
    body = json.loads(row.get("body_json") or "{}")
    ready = row.get("status") == "narrated" and bool((row.get("description") or "").strip())
    closed = row["window_end"] <= now
    return {
        "grain": row["grain"],
        "scope_key": row["scope_key"],
        "window_start_iso": iso(row["window_start"]),
        "window_end_iso": iso(row["window_end"]),
        "description": row.get("description") or "",
        "key_points": body.get("key_points") or [],
        "evaluation": row.get("evaluation") or "",
        "status": row["status"],
        "narrated": ready,
        "window_closed": closed,
        "availability": "open_window" if not closed else "ready" if ready else "pending_summary",
        "metrics_only": bool(body.get("metrics_only")),
        "drill_down_grain": CHILD_OF.get(row["grain"]),
    }


async def activity_overview(
    db: Database,
    *,
    start: int,
    end: int,
    device_id: str | None,
    max_grain: str,
    limit: int,
) -> dict:
    """Select disjoint ready windows, coarse first, separately for each device.

    Closed parents outside the requested range are not clipped and presented
    as if their narrative described only that range. Descend instead. Metrics
    coverage is checked against live record timestamps (not summary counts).
    """
    now = int(time.time() * 1000)
    # Civil 31-day spans can be 31 days + 1 hour across a fall-back transition.
    local_start = dt.datetime.fromisoformat(iso(start)).replace(tzinfo=None)
    local_end = dt.datetime.fromisoformat(iso(end)).replace(tzinfo=None)
    if local_end - local_start > dt.timedelta(days=31):
        raise ValueError("overview is limited to 31 days; split longer ranges")
    grains = ["week", "day", "6h", "1h", "5min"]
    grains = grains[grains.index(max_grain) :]
    labels = await db.device_labels()
    clause, params = device_clause(device_id)
    async with db.lock:
        async with db.conn.execute(
            "SELECT r.device_id,r.ts_start FROM records r "
            "WHERE r.ts_start>=? AND r.ts_start<?" + clause + " ORDER BY r.ts_start",
            (start, end, *params),
        ) as cur:
            captured = await cur.fetchall()
    # One consistent timestamp read, then count windows outside the DB lock.
    timestamps: dict[str, list[int]] = {}
    for r in captured:
        timestamps.setdefault(r["device_id"] or UNKNOWN_DEVICE, []).append(r["ts_start"])
    counts = {did: len(values) for did, values in timestamps.items()}
    scopes = set(counts) | {d["id"] for d in await db.list_devices()}
    if device_id is not None:
        scopes = {device_id}
    rows_by_device: dict[str, list] = {d: [] for d in scopes}
    for grain in grains:
        rows = await db.get_device_summaries(
            grain, start, end, device_scope=device_id, overlap=True
        )
        for row in rows:
            rows_by_device.setdefault(row["device_scope"], []).append(row)
    output = []
    for did, rows in sorted(rows_by_device.items()):
        selected: list[dict] = []
        starts: list[int] = []
        for row in rows:  # coarse→fine, oldest→newest at each level
            a, b = row["window_start"], row["window_end"]
            if a < start or b > min(end, now):
                continue
            if row["status"] != "narrated" or not (row.get("description") or "").strip():
                continue
            index = bisect_left(starts, a)
            if index and selected[index - 1]["window_end"] > a:
                continue
            if index < len(selected) and selected[index]["window_start"] < b:
                continue
            selected.insert(index, row)
            starts.insert(index, a)
        available = len(selected)
        selected = selected[:limit]
        times = timestamps.get(did, [])
        covered = sum(
            bisect_left(times, s["window_end"]) - bisect_left(times, s["window_start"])
            for s in selected
        )
        total = counts.get(did, 0)
        # Gaps are time ranges without returned narratives, not inactivity intervals.
        gap_bounds = []
        pos = start
        for s in selected:
            if s["window_start"] > pos:
                gap_bounds.append((pos, s["window_start"]))
            pos = s["window_end"]
        if pos < end:
            gap_bounds.append((pos, end))
        gaps = [{"start": iso(a), "end": iso(b)} for a, b in gap_bounds]
        gap_ends = [b for _, b in gap_bounds]

        def in_gap(row: dict) -> bool:
            i = bisect_left(gap_ends, row["window_start"] + 1)
            return i < len(gap_bounds) and gap_bounds[i][0] < row["window_end"]

        missing_rows = [r for r in rows if in_gap(r)]
        output.append(
            {
                "device_id": did,
                "device": device_label(did, labels),
                "items": [summary_item(s, now) for s in selected],
                "returned_grains": sorted({s["grain"] for s in selected}, key=grains.index),
                "fallback_used": any(s["grain"] != max_grain for s in selected),
                "truncated": available > limit,
                "available_windows": available,
                "selection_order": "oldest_first",
                "truncation_omits": "newer_windows" if available > limit else None,
                "coverage": {
                    "captured_records": total,
                    "records_in_returned_windows": covered,
                    "records_outside_returned_windows": max(0, total - covered),
                    "uncovered_ranges": gaps,
                },
                "summary_state": "no_captured_records"
                if not total
                else "unavailable"
                if not selected
                else "partial"
                if covered < total or available > limit
                else "covered",
                "pending_windows": sum(
                    r["window_end"] <= now
                    and (r["status"] != "narrated" or not (r.get("description") or "").strip())
                    for r in missing_rows
                ),
                "open_windows": sum(r["window_end"] > now for r in missing_rows),
                "window_counts_scope": "windows_overlapping_uncovered_ranges_across_grains",
            }
        )
    return {
        "as_of": iso(now),
        "timezone": view_tz_name(),
        "period_start_iso": iso(start),
        "period_end_iso": iso(end),
        "requested_grain": max_grain,
        "device_id": device_id,
        "devices": output,
        "fallback_hint": {
            "when": "summary_state=unavailable 或仍有 uncovered_ranges 时继续查询",
            "tool": "search_summaries" if device_id is None else "get_recent_activity",
            "scope": "global_context_only" if device_id is None else "same_device",
            "instruction": (
                "用 search_summaries 不传 device_id，传 custom 和相同起止读取历史全局摘要；"
                "全局叙述仅作补充，不能归属给某台设备。仍无可用摘要则逐页读取原始记录。"
                if device_id is None
                else "保持 device_id 和起止范围，逐页读取原始记录；不要用全局摘要冒充该设备活动。"
            ),
        },
        "note": "按设备选择已闭合且已生成的摘要；粗层不可用时自动下钻，返回窗不重叠。"
        "coverage 只表示记录落在返回摘要的时间窗内，不保证叙述包含每个细节或最新迟到记录。"
        "uncovered_ranges 不等于无活动；历史设备摘要未回填时不会混入全设备摘要。"
        "truncated=true 时缩小时间范围，或用 search_summaries 指定粒度读取。" + PRESENCE_NOTE,
    }
