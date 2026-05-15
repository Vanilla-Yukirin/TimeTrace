"""Windows active-window introspection via pywin32."""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass

import structlog
import win32gui
import win32process

logger = structlog.get_logger(__name__)


# QueryFullProcessImageNameW is the modern API (Vista+) that pairs cleanly with
# PROCESS_QUERY_LIMITED_INFORMATION — it returns the regular Win32 path of the
# target process's main executable using only the minimum-privilege handle.
# pywin32 doesn't expose it (as of 306+), so call kernel32 directly via ctypes.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_QueryFullProcessImageNameW = _kernel32.QueryFullProcessImageNameW
_QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_QueryFullProcessImageNameW.restype = wintypes.BOOL


def _query_full_process_image_name(handle: int) -> str:
    """Wrapper around kernel32!QueryFullProcessImageNameW.

    ``handle`` must be a HANDLE int (pywin32 PyHANDLE coerces via __int__).
    Returns the full Win32 path (e.g. ``C:\\Windows\\System32\\notepad.exe``)
    or raises OSError.
    """
    buf_size = wintypes.DWORD(1024)
    buf = ctypes.create_unicode_buffer(buf_size.value)
    if not _QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_size)):
        raise OSError(ctypes.get_last_error(), "QueryFullProcessImageNameW failed")
    return buf.value


# Known browser process names → we'll try to extract URL via UI Automation later.
_BROWSER_PROCESSES = frozenset(
    {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
)


@dataclass
class WindowInfo:
    app_name: str  # Friendly name (e.g. "Visual Studio Code")
    process_name: str  # Executable name (e.g. "Code.exe")
    window_title: str  # Full window title
    hwnd: int  # Window handle
    pid: int  # Process ID
    url: str | None = None


def get_active_window() -> WindowInfo | None:
    """Return info about the currently-focused foreground window.

    Returns None if no window is focused or an error occurs.
    """
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None

        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name, app_name = _get_process_info(pid)

        return WindowInfo(
            app_name=app_name,
            process_name=process_name,
            window_title=title,
            hwnd=hwnd,
            pid=pid,
        )
    except Exception:
        logger.debug("capture.window.error", exc_info=True)
        return None


def _get_process_info(pid: int) -> tuple[str, str]:
    """Return (process_name, app_name) for the given PID.

    Uses :func:`_query_full_process_image_name` (kernel32 ctypes call) —
    the modern Win32 API designed to pair with
    ``PROCESS_QUERY_LIMITED_INFORMATION``, the minimum-privilege handle a
    normal-user process can obtain on most other processes (including
    elevated ones in the same session).

    Historical note: the original implementation used pywin32's
    ``GetModuleFileNameEx`` which requires ``PROCESS_QUERY_INFORMATION +
    VM_READ`` — a higher privilege than the LIMITED handle being passed in.
    That mismatch made nearly every cross-user call fail to except, leaving
    24213 records in the 2026-05-04 backup tagged "Unknown". A short-lived
    intermediate fix tried ``win32process.QueryFullProcessImageName`` as
    fallback, but pywin32 doesn't expose that name — so the fallback always
    AttributeError'd and we still hit the unknown path. ctypes is the only
    reliable route until pywin32 ships the binding.
    """
    try:
        import win32api  # noqa: PLC0415
        import win32con  # noqa: PLC0415

        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            exe_path = _query_full_process_image_name(int(handle))
        finally:
            win32api.CloseHandle(handle)

        import os  # noqa: PLC0415

        process_name = os.path.basename(exe_path)
        app_name = _friendly_name(process_name, exe_path)
        return process_name, app_name
    except Exception:
        logger.debug("capture.process_info_failed", pid=pid, exc_info=True)
        return "unknown.exe", "Unknown"


def _friendly_name(process_name: str, exe_path: str) -> str:
    """Map process filename to a human-readable app name.

    Falls back to stripping the .exe suffix if no explicit mapping exists.
    """
    _MAP = {
        "code.exe": "Visual Studio Code",
        "code - insiders.exe": "VS Code Insiders",
        "cursor.exe": "Cursor",
        "windsurf.exe": "Windsurf",
        "chrome.exe": "Google Chrome",
        "msedge.exe": "Microsoft Edge",
        "firefox.exe": "Firefox",
        "brave.exe": "Brave",
        "explorer.exe": "Windows Explorer",
        "wechat.exe": "WeChat",
        "slack.exe": "Slack",
        "discord.exe": "Discord",
        "notion.exe": "Notion",
        "obsidian.exe": "Obsidian",
        "wezterm-gui.exe": "WezTerm",
        "windowsterminal.exe": "Windows Terminal",
        "powershell.exe": "PowerShell",
        "cmd.exe": "Command Prompt",
        "python.exe": "Python",
        "pythonw.exe": "Python",
    }
    key = process_name.lower()
    if key in _MAP:
        return _MAP[key]

    # Try to read FileDescription from version info embedded in the exe.
    try:
        import win32api as _wa

        info = _wa.GetFileVersionInfo(exe_path, "\\StringFileInfo\\040904B0\\FileDescription")
        if info:
            return str(info).strip()
    except Exception:
        pass

    # Fallback: strip extension.
    return re.sub(r"\.exe$", "", process_name, flags=re.IGNORECASE)
