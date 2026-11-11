from __future__ import annotations

import logging
import sys
import uuid

import pytest

from timetrace.client.windows_runtime import (
    APP_ICON_PATH,
    APP_USER_MODEL_ID,
    ClientInstance,
    configure_client_logging,
    configure_windows_window_identity,
    create_app_icon,
)


def test_app_icon_has_transparent_background():
    icon = create_app_icon(64)
    assert icon.mode == "RGBA"
    assert icon.size == (64, 64)
    assert icon.getpixel((32, 32))[3] >= 250
    assert icon.getpixel((0, 0))[3] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskbar properties")
def test_window_taskbar_uses_bundled_icon():
    import win32con
    import win32gui
    from win32com.propsys import propsys, pscon

    hwnd = win32gui.CreateWindowEx(
        0, "STATIC", "TimeTrace icon test", win32con.WS_POPUP, 0, 0, 64, 64, 0, 0, 0, None
    )
    try:
        configure_windows_window_identity(hwnd)
        store = propsys.SHGetPropertyStoreForWindow(hwnd)
        assert store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue() == APP_USER_MODEL_ID
        assert (
            store.GetValue(pscon.PKEY_AppUserModel_RelaunchIconResource).GetValue()
            == f"{APP_ICON_PATH},0"
        )
        assert store.GetValue(pscon.PKEY_AppUserModel_RelaunchCommand).GetValue().endswith("--gui")
    finally:
        win32gui.DestroyWindow(hwnd)


def test_client_logging_writes_rotating_file(tmp_path):
    path = configure_client_logging(tmp_path)
    logging.getLogger("timetrace.install.test").warning("installed-client-log-smoke")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "installed-client-log-smoke" in path.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")
def test_named_mutex_rejects_second_client_instance():
    name = rf"Local\VanillaYukirin.TimeTrace.Client.Test.{uuid.uuid4()}"
    first = ClientInstance.acquire(name)
    second = ClientInstance.acquire(name)
    try:
        assert first.acquired is True
        assert second.acquired is False
    finally:
        second.close()
        first.close()

    third = ClientInstance.acquire(name)
    try:
        assert third.acquired is True
    finally:
        third.close()
