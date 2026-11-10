"""Platform boundary: observations and pixels; persistence stays in CaptureService."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class WindowInfo:
    app_name: str
    process_name: str
    window_title: str
    hwnd: int
    pid: int
    url: str | None = None


class CaptureSource(Protocol):
    @property
    def idle_seconds(self) -> float: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def active_window(self) -> WindowInfo | None: ...
    def screenshot(self, record_id, storage_cfg, hwnd): ...
    def wall_time(self) -> float: ...
