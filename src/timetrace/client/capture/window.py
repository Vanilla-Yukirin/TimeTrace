"""Windows active-window introspection via pywin32."""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog
import win32gui
import win32process

logger = structlog.get_logger(__name__)

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

    Uses ``QueryFullProcessImageName`` (Vista+) which is the only modern API
    that pairs cleanly with ``PROCESS_QUERY_LIMITED_INFORMATION`` — the
    minimum-privilege handle a normal-user process can obtain on most other
    processes (including elevated ones in the same session). The legacy
    ``GetModuleFileNameEx`` requires ``PROCESS_QUERY_INFORMATION + VM_READ``
    which usually fails, leaving every window logged as Unknown.
    """
    try:
        import win32api  # noqa: PLC0415
        import win32con  # noqa: PLC0415
        import win32process  # noqa: PLC0415

        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            exe_path: str = win32process.GetModuleFileNameEx(handle, 0)
        except Exception:
            # GetModuleFileNameEx wants more privilege; try the modern API
            # that's spec'd to work with the LIMITED handle we already have.
            exe_path = win32process.QueryFullProcessImageName(handle, 0)
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
