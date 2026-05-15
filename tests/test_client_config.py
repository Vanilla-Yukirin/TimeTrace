"""Tests for ClientConfig (load_or_default, save, ensure_device_id, env overrides)."""

from __future__ import annotations

from pathlib import Path

import pytest

from timetrace.client.core.config import ClientConfig


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = ClientConfig.load_or_default(tmp_path / "nope.toml")
    assert cfg.server.url == "http://127.0.0.1:8765"
    assert cfg.server.auth_token == ""
    assert cfg.device.id == ""
    assert cfg.upload.max_kbps == 0
    assert cfg.privacy.mode == "off"
    assert cfg.privacy.paused is False
    assert cfg.privacy.store_images is True
    assert cfg.privacy.app_blacklist == []
    assert cfg.capture.min_capture_interval_s == 2.0
    assert cfg.capture.max_capture_interval_s == 30.0
    assert cfg.capture.idle_threshold_s == 180.0
    assert cfg.capture.capture_mode == "active_window"


def test_save_then_load_roundtrips_all_fields(tmp_path):
    path = tmp_path / "client.toml"
    original = ClientConfig()
    original.server.url = "https://server.test:9000"
    original.server.auth_token = "tt_live_abc123"
    original.device.id = "device-uuid-xyz"
    original.device.name = "Yuki-Laptop"
    original.device.description = "日常开发与写作"
    original.outbox.root_dir = tmp_path / "ob"
    original.upload.max_kbps = 256
    original.storage.data_dir = tmp_path / "data"
    original.capture.min_capture_interval_s = 1.5
    original.capture.max_capture_interval_s = 60.0
    original.capture.idle_threshold_s = 240.0
    original.capture.switch_capture_delay_s = 2.0
    original.capture.capture_mode = "fullscreen"
    original.privacy.mode = "text_only"
    original.privacy.paused = True
    original.privacy.store_images = False
    original.privacy.app_blacklist = ["KeePassXC.exe", "Bitwarden.exe"]
    original.privacy.title_keywords = ["password", "secret"]
    original.save(path)

    loaded = ClientConfig.load_or_default(path)
    assert loaded.server.url == "https://server.test:9000"
    assert loaded.server.auth_token == "tt_live_abc123"
    assert loaded.device.id == "device-uuid-xyz"
    assert loaded.device.name == "Yuki-Laptop"
    assert loaded.device.description == "日常开发与写作"
    assert loaded.outbox.root_dir == tmp_path / "ob"
    assert loaded.upload.max_kbps == 256
    assert loaded.storage.data_dir == tmp_path / "data"
    assert loaded.capture.min_capture_interval_s == 1.5
    assert loaded.capture.max_capture_interval_s == 60.0
    assert loaded.capture.idle_threshold_s == 240.0
    assert loaded.capture.switch_capture_delay_s == 2.0
    assert loaded.capture.capture_mode == "fullscreen"
    assert loaded.privacy.mode == "text_only"
    assert loaded.privacy.paused is True
    assert loaded.privacy.store_images is False
    assert loaded.privacy.app_blacklist == ["KeePassXC.exe", "Bitwarden.exe"]
    assert loaded.privacy.title_keywords == ["password", "secret"]


def test_partial_toml_falls_back_to_defaults_per_section(tmp_path):
    path = tmp_path / "client.toml"
    path.write_text(
        '[server]\nurl = "http://only-url"\n',
        encoding="utf-8",
    )
    cfg = ClientConfig.load_or_default(path)
    assert cfg.server.url == "http://only-url"
    assert cfg.server.auth_token == ""  # defaulted
    assert cfg.device.id == ""  # whole section defaulted
    assert cfg.upload.max_kbps == 0
    assert cfg.privacy.mode == "off"
    assert cfg.capture.min_capture_interval_s == 2.0


def test_ensure_device_id_mints_when_empty():
    cfg = ClientConfig()
    assert cfg.device.id == ""
    minted = cfg.ensure_device_id()
    assert minted
    assert cfg.device.id == minted


def test_ensure_device_id_is_idempotent():
    cfg = ClientConfig()
    a = cfg.ensure_device_id()
    b = cfg.ensure_device_id()
    assert a == b


def test_save_creates_parent_directory(tmp_path):
    target = tmp_path / "deep" / "nested" / "client.toml"
    ClientConfig().save(target)
    assert target.exists()


def test_save_quotes_with_embedded_quote(tmp_path):
    """Strings carrying quotes must roundtrip without breaking the TOML parser."""
    cfg = ClientConfig()
    cfg.device.description = 'has a "quote" inside'
    path = tmp_path / "c.toml"
    cfg.save(path)
    loaded = ClientConfig.load_or_default(path)
    assert loaded.device.description == 'has a "quote" inside'


def test_save_quotes_with_embedded_backslash(tmp_path):
    cfg = ClientConfig()
    cfg.outbox.root_dir = Path(r"C:\Users\Yuki\TimeTraceData\outbox")
    path = tmp_path / "c.toml"
    cfg.save(path)
    loaded = ClientConfig.load_or_default(path)
    assert loaded.outbox.root_dir == Path(r"C:\Users\Yuki\TimeTraceData\outbox")


def test_apply_env_overrides_overlays_on_loaded(tmp_path, monkeypatch):
    path = tmp_path / "client.toml"
    cfg_file = ClientConfig()
    cfg_file.server.url = "http://from-file"
    cfg_file.server.auth_token = "tt_live_from_file"
    cfg_file.upload.max_kbps = 100
    cfg_file.save(path)

    monkeypatch.setenv("TIMETRACE_SERVER_URL", "http://from-env")
    monkeypatch.setenv("TIMETRACE_AUTH_TOKEN", "tt_live_from_env")
    monkeypatch.setenv("TIMETRACE_UPLOAD_MAX_KBPS", "512")
    monkeypatch.setenv("TIMETRACE_DATA_DIR", str(tmp_path / "envdata"))
    monkeypatch.setenv("TIMETRACE_PRIVACY_MODE", "full")

    cfg = ClientConfig.load_or_default(path).apply_env_overrides()
    assert cfg.server.url == "http://from-env"
    assert cfg.server.auth_token == "tt_live_from_env"
    assert cfg.upload.max_kbps == 512
    assert cfg.storage.data_dir == tmp_path / "envdata"
    assert cfg.privacy.mode == "full"


def test_apply_env_overrides_is_no_op_when_unset(monkeypatch):
    # Strip any env vars that test runner might have inherited.
    for key in (
        "TIMETRACE_SERVER_URL",
        "TIMETRACE_AUTH_TOKEN",
        "TIMETRACE_DEVICE_ID",
        "TIMETRACE_DEVICE_NAME",
        "TIMETRACE_DEVICE_DESC",
        "TIMETRACE_OUTBOX_DIR",
        "TIMETRACE_UPLOAD_MAX_KBPS",
        "TIMETRACE_DATA_DIR",
        "TIMETRACE_PRIVACY_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = ClientConfig().apply_env_overrides()
    assert cfg.server.url == "http://127.0.0.1:8765"


def test_apply_env_overrides_returns_self_for_chain(monkeypatch):
    monkeypatch.setenv("TIMETRACE_SERVER_URL", "http://chain")
    cfg = ClientConfig()
    out = cfg.apply_env_overrides()
    assert out is cfg


def test_invalid_max_kbps_env_raises(monkeypatch):
    """Garbled TIMETRACE_UPLOAD_MAX_KBPS surfaces as ValueError, not silent 0."""
    monkeypatch.setenv("TIMETRACE_UPLOAD_MAX_KBPS", "not-a-number")
    with pytest.raises(ValueError):
        ClientConfig().apply_env_overrides()


def test_app_blacklist_with_special_chars_roundtrips(tmp_path):
    """Backslash + embedded quote in list items must survive the writer escape."""
    cfg = ClientConfig()
    cfg.privacy.app_blacklist = ["C:\\Program Files\\App.exe", 'has"quote.exe']
    path = tmp_path / "c.toml"
    cfg.save(path)
    loaded = ClientConfig.load_or_default(path)
    assert loaded.privacy.app_blacklist == [
        "C:\\Program Files\\App.exe",
        'has"quote.exe',
    ]
