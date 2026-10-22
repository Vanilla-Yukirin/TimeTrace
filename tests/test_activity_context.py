"""Agent/MCP freshness, summary coverage and pagination; real isolated SQLite."""

import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

import pytest

from timetrace.common.config import StorageConfig
from timetrace.common.models import CaptureContext
from timetrace.server.agent import tools
from timetrace.server.agent.activity_context import iso
from timetrace.server.db import Database
from timetrace.server.mcp_layer.server import build_mcp_server
from timetrace.server.tz import use_tz

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
T = 978307200000  # fixed UTC sentinel, not the live user's data
H = 3600_000


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    for did, name in ((A, "Office"), (B, "Home"), (C, "Empty")):
        await database.validate_or_register_device(did, token_fingerprint=None, token_label=None)
        await database.update_device(did, name=name, description=name + " device")
    yield database
    await database.close()


async def record(db, ts, device=A, title="record"):
    return await db.insert_record(
        CaptureContext(app_name="Editor", process_name="editor.exe", window_title=title),
        reason="test",
        ts_start=ts,
        device_id=device,
    )


async def summary(db, grain, start, end, device=A, text="summary", status="narrated"):
    sid = await db.upsert_device_summary(
        device_scope=device,
        grain=grain,
        scope_key=str(start),
        window_start=start,
        window_end=end,
        day_local="2001-01-01",
        metrics_json='{"record_count": 1}',
        record_count=1,
    )
    if status == "narrated":
        await db.save_device_summary_narrative(
            sid,
            description=text,
            evaluation="",
            body_json='{"key_points": []}',
        )
    return sid


def bounds(start=T, end=T + 4 * H):
    return dict(start_iso=iso(start), end_iso=iso(end))


async def test_latest_is_per_device_not_oldest_page_or_last_upload(db):
    await record(db, T, title="old")
    newest = await record(db, T + H, title="latest")
    await record(db, T - H, title="late backlog")  # received last, captured earlier
    home = await record(db, T + 2 * H, device=B)
    legacy = await record(db, T + 3 * H, device=None)
    out = await tools.get_latest_activity(db)
    rows = {r["device_id"]: r for r in out["items"]}
    assert rows[A]["latest_record"]["id"] == newest
    assert rows[B]["latest_record"]["id"] == home
    assert rows["_unknown_device"]["latest_record"]["id"] == legacy
    assert rows[C]["latest_record"] is None and rows[C]["data_status"] == "no_records"
    assert rows[C]["last_contact_at"] is None  # registered but never contacted
    assert all(r["presence"] == "unknown" for r in rows.values())
    assert rows[A]["capture_age_seconds"] > 86400
    r = rows[A]["latest_record"]
    assert r["server_received_at"] != r["ts_start_iso"]
    assert not r["description_available"] and r["analysis_done_at"] is None
    assert "thumb_path" not in r and "image_path" not in r
    scoped = await tools.get_latest_activity(db, device_id=B)
    assert [i["device_id"] for i in scoped["items"]] == [B]


async def test_latest_flags_clock_skew_not_online(db, monkeypatch):
    await record(db, T + H)
    monkeypatch.setattr(time, "time", lambda: T / 1000)
    item = (await tools.get_latest_activity(db, device_id=A))["items"][0]
    assert item["capture_clock_ahead"] and item["capture_age_seconds"] == -3600
    assert item["presence"] == "unknown"


@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_pages_same_millisecond_exact_multiple_and_fixed_snapshot(db, order):
    ids = sorted([await record(db, T + 1000) for _ in range(4)], reverse=order == "desc")
    first = await tools.get_recent_activity(db, limit=2, order=order, **bounds())
    assert first["has_more"] and first["next_cursor"]
    assert [i["id"] for i in first["items"]] == ids[:2]
    await record(db, T + 1000, title="arrived during paging")
    # No explicit bounds: cursor keeps the historical window, not now-24h.
    last = await tools.get_recent_activity(db, cursor=first["next_cursor"], limit=2, order=order)
    assert [i["id"] for i in last["items"]] == ids[2:]
    assert not last["has_more"] and last["next_cursor"] is None
    assert last["window_start_iso"] == first["window_start_iso"]


async def test_page_rejects_changed_scope_window_order_and_invalid_cursor(db):
    await record(db, T + 1)
    await record(db, T + 2)
    first = await tools.get_recent_activity(db, device_id=A, limit=1, **bounds())
    cursor = first["next_cursor"]
    for args in [
        dict(device_id=B),
        dict(device_id=A, order="desc"),
        dict(device_id=A, **bounds(T + 1)),
        dict(device_id=A, cursor="activity:v1:!"),
        dict(device_id=A, cursor="missing"),
    ]:
        result = await tools.get_recent_activity(db, **(dict(cursor=cursor) | args))
        assert "error" in result
    assert "error" in await tools.get_recent_activity(db, order="invalid")


async def test_legacy_id_cursor_is_accepted_but_scope_checked(db):
    a = await record(db, T + 1)
    b = await record(db, T + 2)
    res = await tools.get_recent_activity(db, cursor=a, device_id=A, **bounds())
    assert [r["id"] for r in res["items"]] == [b]
    assert "error" in await tools.get_recent_activity(db, cursor=a, device_id=B, **bounds())


async def test_overview_falls_back_per_device_without_duplicate_parent_children(db):
    for device in (A, B):
        await record(db, T + 1000, device)
        await record(db, T + H + 1000, device)
    await summary(db, "day", T, T + 24 * H, status="pending_summary")
    await summary(db, "1h", T, T + H, text="A hour")
    await summary(db, "5min", T, T + 300_000, text="duplicate child")
    await summary(db, "5min", T + H, T + H + 300_000, text="A leaf")
    await summary(db, "1h", T, T + H, device=B, text="B hour")
    out = await tools.get_activity_overview(db, period="custom", **bounds())
    rows = {d["device_id"]: d for d in out["devices"]}
    assert [i["description"] for i in rows[A]["items"]] == ["A hour", "A leaf"]
    assert rows[A]["fallback_used"] and rows[A]["summary_state"] == "covered"
    assert rows[B]["summary_state"] == "partial"
    assert rows[B]["coverage"]["records_outside_returned_windows"] == 1
    assert rows[C]["summary_state"] == "no_captured_records"
    assert rows[B]["coverage"]["uncovered_ranges"]
    assert rows[A]["items"][0]["drill_down_grain"] == "5min"
    only_b = await tools.get_activity_overview(db, period="custom", device_id=B, **bounds())
    assert len(only_b["devices"]) == 1 and only_b["devices"][0]["device_id"] == B


async def test_overview_open_parent_boundary_and_limit_are_explicit(db, monkeypatch):
    await record(db, T + 1000)
    await record(db, T + H + 1000)
    await record(db, T + 2 * H + 1000)
    await summary(db, "day", T, T + 24 * H, text="must not show open parent")
    await summary(db, "1h", T, T + H, text="first")
    await summary(db, "1h", T + H, T + 2 * H, text="second")
    monkeypatch.setattr(time, "time", lambda: (T + 2 * H + 2000) / 1000)
    out = await tools.get_activity_overview(db, period="custom", limit=1, device_id=A, **bounds())
    row = out["devices"][0]
    assert row["truncated"] and row["available_windows"] == 2
    assert row["open_windows"] == 1 and row["summary_state"] == "partial"
    assert row["coverage"]["records_outside_returned_windows"] == 2
    # Do not pass a whole hour's narrative off as the requested last half hour.
    narrow = await tools.get_activity_overview(
        db,
        period="custom",
        device_id=A,
        **bounds(T + H // 2, T + H),
    )
    assert narrow["devices"][0]["items"] == []


async def test_overview_missing_device_pyramid_is_not_no_activity(db):
    await record(db, T + 1000)
    out = await tools.get_activity_overview(db, period="custom", device_id=A, **bounds())
    assert out["devices"][0]["summary_state"] == "unavailable"
    assert out["devices"][0]["coverage"]["captured_records"] == 1
    assert "error" in await tools.get_activity_overview(
        db, period="custom", **bounds(T, T + 32 * 24 * H)
    )
    assert "error" in await tools.get_activity_overview(db, max_grain="bogus")
    assert "error" in await tools.get_activity_overview(db, period="custom")


async def test_overview_today_uses_view_midnight_and_iso_offset(db, monkeypatch):
    zone = ZoneInfo("America/New_York")
    now = dt.datetime(2025, 3, 9, 12, tzinfo=zone)  # DST boundary
    monkeypatch.setattr(time, "time", now.timestamp)
    with use_tz(zone):
        out = await tools.get_activity_overview(db)
    assert out["period_start_iso"] == "2025-03-09T00:00:00.000-05:00"
    assert out["period_end_iso"] == "2025-03-09T12:00:00.000-04:00"


async def test_overview_accepts_full_civil_month_across_fall_back(db):
    with use_tz(ZoneInfo("Europe/Berlin")):
        out = await tools.get_activity_overview(
            db,
            period="custom",
            start_iso="2025-10-01",
            end_iso="2025-11-01",
        )
    assert "error" not in out


async def test_blank_narrated_row_is_pending_not_ready(db):
    await record(db, T)
    await summary(db, "1h", T, T + H, text="   ")
    out = await tools.get_activity_overview(db, period="custom", device_id=A, **bounds())
    row = out["devices"][0]
    assert row["summary_state"] == "unavailable" and row["pending_windows"] == 1


def test_iso_zero_is_offset_bearing_on_windows():
    parsed = dt.datetime.fromisoformat(iso(0))
    assert parsed.tzinfo is not None and parsed.timestamp() == 0


async def test_exact_summary_lookup_reports_pending_overlap_and_truncation(db):
    await summary(db, "1h", T, T + H, status="pending_summary")
    await summary(db, "1h", T + H, T + 2 * H)
    out = await tools.search_summaries(
        db,
        grain="1h",
        period="custom",
        device_id=A,
        limit=1,
        **bounds(T + H // 2),
    )
    assert out["has_more"] and out["pending_count"] == 1
    assert out["items"][0]["availability"] == "pending_summary"
    assert out["items"][0]["window_closed"]
    assert out["overview_tool"] == "get_activity_overview"


def structured(result):
    return result[1] if isinstance(result, tuple) else json.loads(result[0].text)


async def test_mcp_and_agent_expose_same_new_contracts(db):
    await record(db, T)
    mcp = build_mcp_server(db, None)
    names = {t.name for t in await mcp.list_tools()}
    schemas = {t["function"]["name"] for t in tools.TOOL_SCHEMAS}
    for name, args in [
        ("get_latest_activity", {"device_id": A}),
        ("get_activity_overview", dict(period="custom", device_id=A, **bounds())),
        ("get_recent_activity", dict(order="desc", device_id=A, **bounds())),
    ]:
        assert name in names and name in schemas
        direct = await tools.dispatch_tool(db, name, args)
        remote = structured(await mcp.call_tool(name, args))
        assert "error" not in direct and "error" not in remote
        assert direct["timezone"] == remote["timezone"]
        if name == "get_recent_activity":
            assert direct["items"] == remote["items"] and remote["order"] == "desc"
