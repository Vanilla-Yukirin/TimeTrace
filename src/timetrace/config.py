"""Application configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CaptureConfig:
    """Tunable parameters for the Capture Service."""

    min_capture_interval_s: float = 2.0
    max_capture_interval_s: float = 30.0
    idle_threshold_s: float = 180.0
    switch_capture_delay_s: float = 1.5  # Delay after window switch before screenshot
    capture_mode: str = "active_window"  # "active_window" | "fullscreen"


@dataclass
class StorageConfig:
    """Paths for SQLite database and image files."""

    data_dir: Path = field(default_factory=lambda: Path.home() / "TimeTraceData")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "timetrace.db"

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


@dataclass
class PrivacyConfig:
    """Privacy settings."""

    paused: bool = False
    app_blacklist: list[str] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)
    store_images: bool = True  # False → record metadata only


@dataclass
class AppConfig:
    """Top-level application configuration."""

    capture: CaptureConfig = field(default_factory=CaptureConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    api_host: str = "127.0.0.1"
    api_port: int = 8765
