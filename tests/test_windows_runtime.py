from __future__ import annotations

import logging
import sys
import uuid

import pytest

from timetrace.client.windows_runtime import (
    ClientInstance,
    configure_client_logging,
    create_app_icon,
)


def test_app_icon_is_high_contrast_rgba():
    icon = create_app_icon(64)
    assert icon.mode == "RGBA"
    assert icon.size == (64, 64)
    assert icon.getpixel((32, 32))[3] == 255
    assert icon.getpixel((0, 0))[3] == 0


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
