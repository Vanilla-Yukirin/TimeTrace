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
class AuthConfig:
    """Login-system tunables (cookie session + admin seed).

    Bearer-token auth (``ServerAuth`` in ``server/auth.py``) is a separate
    channel and not configured here — its tokens live in ``tokens.json`` and
    are managed by the admin via Web UI / CLI.
    """

    # Username of the seeded admin on first start. ``username`` is PRIMARY KEY
    # in ``auth_users`` so this can't be changed post-seed without manual SQL —
    # picking it up at first boot via env lets the machine-owner avoid having
    # to log in as a generic ``admin`` and rename.
    admin_username: str = "admin"
    # Default password for the seeded admin. Forced-change on first login.
    admin_initial_password: str = "admin"
    # Cookie ``Secure`` flag. Must be False over plain HTTP (local dev), True
    # over HTTPS (public deploy). Toggled via ``TIMETRACE_INSECURE_COOKIE=1``.
    cookie_secure: bool = True
    # Cookie name (kept short, prefixed so it's grep-able in browser devtools).
    cookie_name: str = "tt_session"
    # Session lifetime; reflected both in cookie ``Max-Age`` and ``auth_sessions.expires_at``.
    session_ttl_s: int = 30 * 24 * 3600  # 30 days
    # Login failure rate-limit window (seconds) and threshold (failures).
    login_rate_window_s: int = 15 * 60   # 15 min
    login_rate_threshold: int = 5
    # Lockout duration after the threshold is hit.
    login_lockout_s: int = 30 * 60       # 30 min

    @classmethod
    def from_env(cls) -> AuthConfig:
        return cls(
            admin_username=os.getenv("TIMETRACE_ADMIN_USERNAME", "admin").strip() or "admin",
            cookie_secure=not _env_truthy(os.getenv("TIMETRACE_INSECURE_COOKIE")),
        )


@dataclass
class AppConfig:
    """Top-level application configuration."""

    capture: CaptureConfig = field(default_factory=CaptureConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    vlm: VLMConfig | None = field(default_factory=VLMConfig.from_env)
    auth: AuthConfig = field(default_factory=AuthConfig.from_env)
    api_host: str = "127.0.0.1"
    api_port: int = 8765
