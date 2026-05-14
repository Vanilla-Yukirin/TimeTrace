"""Tests for ClientConfig (load_or_default, save, ensure_device_id)."""

from __future__ import annotations

from pathlib import Path

from timetrace.client.core.config import ClientConfig


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = ClientConfig.load_or_default(tmp_path / "nope.toml")
    assert cfg.server.url == "http://127.0.0.1:8765"
    assert cfg.server.auth_token == ""
    assert cfg.device.id == ""
    assert cfg.upload.max_kbps == 0
    assert cfg.privacy.mode == "off"


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
    original.privacy.mode = "text_only"
    original.save(path)

    loaded = ClientConfig.load_or_default(path)
    assert loaded.server.url == "https://server.test:9000"
    assert loaded.server.auth_token == "tt_live_abc123"
    assert loaded.device.id == "device-uuid-xyz"
    assert loaded.device.name == "Yuki-Laptop"
    assert loaded.device.description == "日常开发与写作"
    assert loaded.outbox.root_dir == tmp_path / "ob"
    assert loaded.upload.max_kbps == 256
    assert loaded.privacy.mode == "text_only"


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
