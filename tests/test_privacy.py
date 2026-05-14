"""Tests for the capture privacy gate."""

from timetrace.client.capture.privacy import should_capture
from timetrace.common.config import PrivacyConfig
from timetrace.common.models import CaptureContext


def _ctx(app: str = "VSCode", title: str = "main.py") -> CaptureContext:
    return CaptureContext(app_name=app, process_name=app, window_title=title)


def test_capture_allowed_by_default():
    assert should_capture(_ctx(), PrivacyConfig()) is True


def test_capture_blocked_when_paused():
    cfg = PrivacyConfig(paused=True)
    assert should_capture(_ctx(), cfg) is False


def test_capture_blocked_by_app_blacklist():
    cfg = PrivacyConfig(app_blacklist=["SecretApp"])
    assert should_capture(_ctx(app="SecretApp"), cfg) is False


def test_capture_allowed_when_app_not_in_blacklist():
    cfg = PrivacyConfig(app_blacklist=["SecretApp"])
    assert should_capture(_ctx(app="VSCode"), cfg) is True


def test_capture_blocked_by_title_keyword():
    cfg = PrivacyConfig(title_keywords=["password"])
    assert should_capture(_ctx(title="enter your password"), cfg) is False


def test_capture_allowed_when_title_clean():
    cfg = PrivacyConfig(title_keywords=["password"])
    assert should_capture(_ctx(title="project README"), cfg) is True
