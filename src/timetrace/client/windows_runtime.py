"""Windows desktop runtime helpers shared by source and packaged clients."""

from __future__ import annotations

import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog
from PIL import Image, ImageDraw

APP_USER_MODEL_ID = "VanillaYukirin.TimeTrace.Client"
_INSTANCE_MUTEX = rf"Local\{APP_USER_MODEL_ID}"
_ERROR_ALREADY_EXISTS = 183


def create_app_icon(size: int = 256) -> Image.Image:
    """Return a high-contrast blue clock that remains legible in the tray."""
    if size < 16:
        raise ValueError("icon size must be at least 16 pixels")
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, round(size * 0.06))
    border = max(1, round(size * 0.045))
    hand = max(1, round(size * 0.065))
    draw.ellipse(
        (margin, margin, size - margin - 1, size - margin - 1),
        fill=(37, 99, 235, 255),
        outline=(147, 197, 253, 255),
        width=border,
    )
    center = size // 2
    draw.line(
        (center, center, center, round(size * 0.25)),
        fill=(255, 255, 255, 255),
        width=hand,
    )
    draw.line(
        (center, center, round(size * 0.72), round(size * 0.58)),
        fill=(255, 255, 255, 255),
        width=hand,
    )
    dot = max(1, round(size * 0.055))
    draw.ellipse(
        (center - dot, center - dot, center + dot, center + dot),
        fill=(255, 255, 255, 255),
    )
    return image


def configure_windows_app_identity() -> None:
    """Give the process a stable notification/shortcut identity on Windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        # Older Windows/Wine environments can omit this API; the client remains usable.
        return


def configure_client_logging(logs_dir: Path) -> Path:
    """Log to a rotating file, plus stderr when a source console is present."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "client.log"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(message)s", force=True)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return log_path


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
