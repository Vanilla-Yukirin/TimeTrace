"""Time-zone contract (W3): UTC storage, per-request IANA view zone."""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from timetrace.common.config import StorageConfig
from timetrace.server.agent import tools as agent_tools
from timetrace.server.api.app import create_app
from timetrace.server.db import Database
from timetrace.server.summary.windows import window_bounds
from timetrace.server.tz import parse_tz, use_tz, view_tz

TOKYO = ZoneInfo("Asia/Tokyo")
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture
async def db(tmp_path):
    database = Database(StorageConfig(data_dir=tmp_path))
    await database.init()
    yield database
    await database.close()


def _ms(dt: _dt.datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.mark.parametrize("bad", ["", "Mars/Olympus", "../etc/passwd", "UTC+9", "/abs"])
def test_parse_tz_rejects_non_iana(bad):
    with pytest.raises(ValueError):
        parse_tz(bad)


def test_server_default_from_env(monkeypatch):
    monkeypatch.setenv("TIMETRACE_TZ", "Asia/Shanghai")
    assert view_tz() == SHANGHAI
    with use_tz(TOKYO):
        assert view_tz() == TOKYO
    monkeypatch.setenv("TIMETRACE_TZ", "not/a-zone")
    assert view_tz() is None  # invalid default → process local, never a crash


def test_day_window_follows_explicit_zone():
    # 2026-10-22 03:30 in Tokyo is still the 10-21 logical day (4AM cut)…
    ts = _ms(_dt.datetime(2026, 10, 22, 3, 30, tzinfo=TOKYO))
    start, end = window_bounds(ts, "day", 4, tz=TOKYO)
    assert start == _ms(_dt.datetime(2026, 10, 21, 4, tzinfo=TOKYO))
    assert end - start == 86_400_000
    # …while in Shanghai (one hour behind) the same instant is 02:30 → same day,
    # but the boundary instant differs by exactly the zone difference.
    s_start, _ = window_bounds(ts, "day", 4, tz=SHANGHAI)
    assert s_start - start == 3_600_000


def test_day_window_across_dst_is_a_civil_day():
    # America/New_York leaves DST at 02:00 on 2026-11-01, so with the 4AM cut
    # the logical day 10-31 04:00 → 11-01 04:00 lasts 25h; the next one is 24h.
    ts = _ms(_dt.datetime(2026, 10, 31, 12, tzinfo=NEW_YORK))
    start, end = window_bounds(ts, "day", 4, tz=NEW_YORK)
    assert end - start == 25 * 3_600_000
    start, end = window_bounds(end, "day", 4, tz=NEW_YORK)
    assert end - start == 24 * 3_600_000


def test_agent_iso_parsing_and_formatting_use_view_zone():
    with use_tz(TOKYO):
        ms = agent_tools._parse_iso_local("2026-10-22 09:00")
        assert ms == _ms(_dt.datetime(2026, 10, 22, 9, tzinfo=TOKYO))
        assert agent_tools.ms_to_iso(ms) == "2026-10-22 09:00:00"
    with use_tz(SHANGHAI):
        assert agent_tools.ms_to_iso(ms) == "2026-10-22 08:00:00"
        # an explicit offset in the string wins over the zone
        assert agent_tools._parse_iso_local("2026-10-22T09:00+09:00") == ms


def test_period_today_starts_at_view_zone_cut(monkeypatch):
    now = _ms(_dt.datetime(2026, 10, 22, 15, tzinfo=TOKYO))
    monkeypatch.setattr(agent_tools.time, "time", lambda: now / 1000)
    with use_tz(TOKYO):
        start, end = agent_tools._period_bounds("today", None, None, 4)
    assert start == _ms(_dt.datetime(2026, 10, 22, 4, tzinfo=TOKYO)) and end == now
    with use_tz(TOKYO):
        start, _ = agent_tools._period_bounds("this_month", None, None, 4)
    assert start == _ms(_dt.datetime(2026, 10, 1, 4, tzinfo=TOKYO))


async def test_request_header_and_query_bind_zone(db):
    app = create_app(db)
    with TestClient(app) as client:
        tokyo = client.get(
            "/v1/summaries", params={"day": "2026-10-22"}, headers={"X-Timezone": "Asia/Tokyo"}
        ).json()
        shanghai = client.get(
            "/v1/summaries", params={"day": "2026-10-22", "tz": "Asia/Shanghai"}
        ).json()
        assert tokyo["timezone"] == "Asia/Tokyo"
        assert tokyo["day_start"] == _ms(_dt.datetime(2026, 10, 22, 4, tzinfo=TOKYO))
        assert shanghai["day_start"] - tokyo["day_start"] == 3_600_000

        bad = client.get("/v1/summaries", headers={"X-Timezone": "Nowhere/Land"})
        assert bad.status_code == 400
        assert "time zone" in bad.json()["detail"]
