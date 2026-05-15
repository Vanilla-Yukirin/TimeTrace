"""Application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class VLMConfig:
    """VLM endpoint credentials and provider quirks.

    Lives in `common/` because both client (config plumbing) and server (actual
    VLM calls) need it; keeping it here avoids a common→server reverse import.
    """

    base_url: str
    api_key: str
    model: str
    # Some OpenAI-compatible providers (DashScope qwen, SiliconFlow Qwen, etc.)
    # default thinking ON and accept ``extra_body={"enable_thinking": False}``
    # to disable it. Vanilla OpenAI rejects unknown body fields with HTTP 400,
    # so we only opt into this defensive override when the user asks for it.
    disable_thinking: bool = False

    @classmethod
    def from_env(cls) -> VLMConfig | None:
        """Build config from environment; return None if API key is missing/blank."""
        api_key = os.getenv("TIMETRACE_VLM_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            base_url=os.getenv("TIMETRACE_VLM_BASE_URL", "https://api.openai.com/v1").strip(),
            api_key=api_key,
            model=os.getenv("TIMETRACE_VLM_MODEL", "gpt-4o-mini").strip(),
            disable_thinking=_env_truthy(os.getenv("TIMETRACE_VLM_DISABLE_THINKING")),
        )


@dataclass
class WorkerConfig:
    """Tunable parameters for the analysis worker."""

    vlm_concurrency: int = 2
    max_retries: int = 5
    backoff_base_s: float = 60.0
    backoff_max_s: float = 600.0


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
    # P4 OCR + classifier + blur pipeline selector. Operational today's runtime
    # privacy is the four fields above; `mode` is forward-compat for the
    # client-side text/full filter that lands in P4.
    #   off       — current behavior (no OCR, no blur)
    #   text_only — title keyword block only
    #   full      — OCR + classifier + strong blur
    mode: str = "off"


@dataclass
class AppConfig:
    """Top-level application configuration."""

    capture: CaptureConfig = field(default_factory=CaptureConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    vlm: VLMConfig | None = field(default_factory=VLMConfig.from_env)
    api_host: str = "127.0.0.1"
    api_port: int = 8765
