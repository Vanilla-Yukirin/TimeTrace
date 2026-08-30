"""Capture interval semantics: unobserved wall time is never activity."""

from __future__ import annotations

from dataclasses import dataclass

from timetrace.client.capture import service as service_module
from timetrace.client.capture.service import CaptureService
from timetrace.client.capture.window import WindowInfo
from timetrace.common.config import CaptureConfig, PrivacyConfig


@dataclass
class _Idle:
    idle_seconds: float = 0.0


class _Backend:
    def __init__(self) -> None:
        self.closed: list[tuple[str, int | None]] = []
        self.submitted: list[str] = []
        self.submitted_starts: list[int | None] = []

    async def close_record(self, record_id: str, *, ts_end_ms: int | None = None) -> None:
        self.closed.append((record_id, ts_end_ms))

    async def submit_record(
        self,
        _ctx,
        reason: str,
        event_type: str = "heartbeat",
        *,
        ts_start_ms: int | None = None,
    ) -> str:
        record_id = f"new-{len(self.submitted) + 1}"
        self.submitted.append(record_id)
        self.submitted_starts.append(ts_start_ms)
        return record_id

    async def submit_screenshot(self, _payload):
        return None

    async def mark_pending(self, _record_id: str) -> None:
        return None


async def test_observation_gap_closes_at_last_seen_time_and_starts_new_record(monkeypatch):
    backend = _Backend()
    capture = CaptureService(CaptureConfig(), PrivacyConfig(), backend)
    capture._idle = _Idle()
    capture._last_record_id = "before-sleep"
    capture._prev_hwnd = 42
    capture._last_tick_monotonic = 100.0
    capture._last_tick_wall_ms = 1_747_300_000_000

    monkeypatch.setattr(service_module.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(service_module.time, "time", lambda: 1_747_343_200.0)
    monkeypatch.setattr(service_module, "should_capture", lambda _ctx, _privacy: True)
    monkeypatch.setattr(
        service_module,
        "get_active_window",
        lambda: WindowInfo(
            app_name="Code",
            process_name="Code.exe",
            window_title="main.py",
            hwnd=42,
            pid=1,
        ),
    )

    await capture._tick()
    await capture._cancel_pending_screenshot()

    assert backend.closed == [("before-sleep", 1_747_300_000_000)]
    assert backend.submitted == ["new-1"]
    assert backend.submitted_starts == [1_747_343_200_000]
    assert capture._last_record_id == "new-1"


async def test_normal_window_switch_closes_at_current_observed_time(monkeypatch):
    backend = _Backend()
    capture = CaptureService(CaptureConfig(), PrivacyConfig(), backend)
    capture._idle = _Idle()
    capture._last_record_id = "old"
    capture._prev_hwnd = 1
    capture._last_tick_monotonic = 100.0
    capture._last_tick_wall_ms = 1_747_300_000_000

    monkeypatch.setattr(service_module.time, "monotonic", lambda: 101.0)
    monkeypatch.setattr(service_module.time, "time", lambda: 1_747_300_001.0)
    monkeypatch.setattr(service_module, "should_capture", lambda _ctx, _privacy: True)
    monkeypatch.setattr(
        service_module,
        "get_active_window",
        lambda: WindowInfo("Browser", "browser.exe", "page", 2, 2),
    )

    await capture._tick()
    await capture._cancel_pending_screenshot()

    assert backend.closed == [("old", 1_747_300_001_000)]
    assert backend.submitted_starts == [1_747_300_001_000]


async def test_slow_submit_uses_tick_observation_as_record_start(monkeypatch):
    backend = _Backend()
    capture = CaptureService(CaptureConfig(), PrivacyConfig(), backend)
    capture._idle = _Idle()

    monkeypatch.setattr(service_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(service_module.time, "time", lambda: 1_747_300_000.0)
    monkeypatch.setattr(service_module, "should_capture", lambda _ctx, _privacy: True)
    monkeypatch.setattr(
        service_module,
        "get_active_window",
        lambda: WindowInfo("Browser", "browser.exe", "page", 2, 2),
    )

    await capture._tick()
    # Model a slow submit/event-loop stall without allowing asyncio internals to
    # consume a mocked monotonic iterator themselves.
    monkeypatch.setattr(service_module.time, "monotonic", lambda: 110.0)
    monkeypatch.setattr(service_module.time, "time", lambda: 1_747_300_010.0)
    await capture._tick()
    await capture._cancel_pending_screenshot()

    assert backend.submitted_starts == [1_747_300_000_000, 1_747_300_010_000]
    assert backend.closed == [("new-1", 1_747_300_000_000)]
