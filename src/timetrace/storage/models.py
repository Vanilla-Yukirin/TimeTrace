"""Data models used across the application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CaptureContext:
    """Snapshot of the active window at a point in time."""

    app_name: str
    process_name: str
    window_title: str
    url: str | None = None
