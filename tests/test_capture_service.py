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

    async def close_record(self, record_id: str, *, ts_end_ms: int | None = None) -> None:
        self.closed.append((record_id, ts_end_ms))

    async def submit_record(self, _ctx, reason: str, event_type: str = "heartbeat") -> str:
        record_id = f"new-{len(self.submitted) + 1}"
        self.submitted.append(record_id)
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
