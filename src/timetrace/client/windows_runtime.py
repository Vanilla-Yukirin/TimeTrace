"""Windows desktop runtime helpers shared by source and packaged clients."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import uuid
from pathlib import Path

import structlog
from PIL import Image

from timetrace.common.logging import configure_logging

APP_USER_MODEL_ID = "VanillaYukirin.TimeTrace.Client"
APP_ICON_PATH = Path(__file__).with_name("dashboard") / "timetrace-kuro.ico"
_INSTANCE_MUTEX = rf"Local\{APP_USER_MODEL_ID}"
_ERROR_ALREADY_EXISTS = 183
logger = structlog.get_logger(__name__)


def create_app_icon(size: int = 256) -> Image.Image:
    """Load the bundled mascot at the requested tray icon size."""
    if size < 16:
        raise ValueError("icon size must be at least 16 pixels")
    with Image.open(APP_ICON_PATH) as icon:
        return icon.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def configure_windows_app_identity() -> None:
    """Give the process a stable notification/shortcut identity on Windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        # Older Windows/Wine environments can omit this API; the client remains usable.
        return


def configure_windows_window_identity(hwnd: int) -> None:
    """Give the taskbar the same icon and launch command as the Start shortcut."""
    import pythoncom
    from win32com.propsys import propsys, pscon

    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend(("-m", "timetrace.client.cli"))
    command.append("--gui")
    try:
        store = propsys.SHGetPropertyStoreForWindow(hwnd)
        for key, value in (
            (pscon.PKEY_AppUserModel_RelaunchCommand, subprocess.list2cmdline(command)),
            (pscon.PKEY_AppUserModel_RelaunchDisplayNameResource, "TimeTrace Client"),
            (pscon.PKEY_AppUserModel_RelaunchIconResource, f"{APP_ICON_PATH},0"),
            (pscon.PKEY_AppUserModel_ID, APP_USER_MODEL_ID),
        ):
            store.SetValue(key, propsys.PROPVARIANTType(value))
    except pythoncom.com_error:
        logger.warning("client.taskbar_identity_failed", exc_info=True)


def configure_client_logging(logs_dir: Path) -> Path:
    """Log to a rotating file, plus stderr when a source console is present."""
    path = configure_logging("client", logs_dir=logs_dir)
    assert path is not None
    return path


class ClientInstance:
    """Lifetime guard for the one allowed Windows capture client process."""

    def __init__(self, handle: int | None, *, acquired: bool) -> None:
        self._handle = handle
        self.acquired = acquired

    @classmethod
    def acquire(cls, name: str = _INSTANCE_MUTEX) -> ClientInstance:
        if sys.platform != "win32":
            return cls(None, acquired=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        create_mutex.restype = ctypes.c_void_p
        handle = create_mutex(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return cls(None, acquired=False)
        return cls(int(handle), acquired=True)

    def close(self) -> None:
        if self._handle is None or sys.platform != "win32":
            return
        ctypes.windll.kernel32.CloseHandle(self._handle)
        self._handle = None

    def __enter__(self) -> ClientInstance:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.close()


def notify_already_running() -> None:
    """Explain a rejected second launch without requiring a console window."""
    if sys.platform != "win32":
        return
    ctypes.windll.user32.MessageBoxW(
        None,
        "TimeTrace 已经在运行。可通过托盘图标或 http://127.0.0.1:8764 打开控制面板。",
        "TimeTrace Client",
        0x40,
    )


def _startup_shortcut() -> Path | None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    from win32com.shell import shell, shellcon

    return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_STARTUP, None, 0)) / "TimeTrace Client.lnk"


def autostart_enabled() -> bool | None:
    shortcut = _startup_shortcut()
    return shortcut.is_file() if shortcut is not None else None


def set_autostart(enabled: bool) -> bool:
    shortcut = _startup_shortcut()
    if shortcut is None:
        raise ValueError("自启动仅适用于 Windows 安装包")
    if not enabled:
        shortcut.unlink(missing_ok=True)
        return False

    import pythoncom
    from win32com.client import Dispatch

    executable = Path(sys.executable).resolve()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    temp = shortcut.with_name(f".TimeTrace-{uuid.uuid4().hex}.lnk")
    link = None
    pythoncom.CoInitialize()
    try:
        link = Dispatch("WScript.Shell").CreateShortcut(str(temp))
        link.TargetPath = str(executable)
        link.WorkingDirectory = str(executable.parent)
        link.IconLocation = f"{APP_ICON_PATH},0"
        link.Description = "Start TimeTrace when this user signs in"
        link.Save()
        os.replace(temp, shortcut)
    finally:
        link = None
        pythoncom.CoUninitialize()
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return True
