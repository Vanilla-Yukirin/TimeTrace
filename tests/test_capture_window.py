"""Tests for the Windows-specific window introspection.

These run only on Windows because they use pywin32 directly. On other
platforms the entire test module is skipped at collection time.
"""

from __future__ import annotations

import os
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only", allow_module_level=True)


from timetrace.client.capture.window import _get_process_info  # noqa: E402


def test_get_process_info_returns_real_name_for_own_pid():
    """For the test runner's own PID we MUST get a real .exe name back, not
    the 'unknown.exe' / 'Unknown' fallback. Historically the code used
    PROCESS_QUERY_LIMITED_INFORMATION + GetModuleFileNameEx — the latter
    requires PROCESS_QUERY_INFORMATION + PROCESS_VM_READ, so OpenProcess
    succeeded but the read failed → every window logged as Unknown.
    """
    process_name, app_name = _get_process_info(os.getpid())
    assert process_name != "unknown.exe", (
        "_get_process_info fell back to unknown.exe for own PID — likely a "
        "PROCESS_QUERY_LIMITED_INFORMATION vs GetModuleFileNameEx mismatch"
    )
    assert app_name != "Unknown"
    # Most reasonable: own PID resolves to python.exe / pytest.exe / similar.
    assert process_name.lower().endswith(".exe")
