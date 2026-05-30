"""EmbServerConfig — standalone config for the local Qwen3-VL embedding daemon.

Deliberately decoupled from ``common.config.AppConfig``: embserver is its own
process on its own port with its own (heavy, optional) dependency footprint.
Everything is env-overridable so a systemd unit can configure it without a file.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

# Same token shape as the rest of TimeTrace (server/auth.py mints tt_live_*).
_KEY_PREFIX = "tt_emb_"


def _default_model_path() -> Path:
    return Path.home() / "TimeTraceData" / "models" / "Qwen3-VL-Embedding-2B"


@dataclass
class EmbServerConfig:
    host: str = "127.0.0.1"
    port: int = 8766  # API is 8765; embserver gets its own port (one of several)
    model_path: Path = field(default_factory=_default_model_path)
    # bf16 is near-lossless (~4.5GB on 2B) per the drift-detector ladder; the
    # safe default. Swap to a quantized checkpoint dir + dtype=auto for smaller VRAM.
    dtype: str = "bfloat16"
    # None on first run -> a key is generated and logged once (see cli.py).
    api_key: str | None = None
    # 0 = keep model resident forever; >0 = unload after N idle seconds (2nd cut).
    idle_ttl_seconds: int = 0
    # Preload the model at startup instead of JIT on first request.
    preload: bool = False

    @classmethod
    def from_env(cls) -> "EmbServerConfig":
        c = cls()
        if v := os.environ.get("TIMETRACE_EMBSERVER_HOST"):
            c.host = v
        if v := os.environ.get("TIMETRACE_EMBSERVER_PORT"):
            c.port = int(v)
        if v := os.environ.get("TIMETRACE_EMBSERVER_MODEL"):
            c.model_path = Path(v)
        if v := os.environ.get("TIMETRACE_EMBSERVER_DTYPE"):
            c.dtype = v
        if v := os.environ.get("TIMETRACE_EMBSERVER_API_KEY"):
            c.api_key = v
        if v := os.environ.get("TIMETRACE_EMBSERVER_TTL"):
            c.idle_ttl_seconds = int(v)
        if os.environ.get("TIMETRACE_EMBSERVER_PRELOAD") in ("1", "true", "True"):
            c.preload = True
        return c

    def ensure_api_key(self) -> tuple[str, bool]:
        """Return (key, was_generated). Generate + set if absent."""
        if self.api_key:
            return self.api_key, False
        self.api_key = _KEY_PREFIX + secrets.token_urlsafe(32)
        return self.api_key, True
