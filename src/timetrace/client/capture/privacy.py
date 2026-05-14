"""Privacy gating for capture decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from timetrace.common.config import PrivacyConfig
    from timetrace.common.models import CaptureContext


def should_capture(ctx: CaptureContext, privacy_cfg: PrivacyConfig) -> bool:
    """Return True if the current context is allowed to be captured."""
    if privacy_cfg.paused:
        return False
    if ctx.app_name in privacy_cfg.app_blacklist:
        return False
    if any(kw in ctx.window_title for kw in privacy_cfg.title_keywords):
        return False
    return True
