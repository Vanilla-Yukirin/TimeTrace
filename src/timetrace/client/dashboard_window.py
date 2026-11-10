"""Open the loopback dashboard in its own Windows WebView2 window."""

import subprocess
import sys
import urllib.request
import webbrowser
from urllib.parse import urlsplit

from timetrace.client.windows_runtime import APP_USER_MODEL_ID, ClientInstance

WINDOW_TITLE = "TimeTrace 客户端"


def _is_loopback(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "http"
            and parts.hostname == "127.0.0.1"
            and parts.username is None
            and parts.port is not None
            and parts.path in ("", "/")
            and not parts.query
            and not parts.fragment
        )
    except ValueError:
        return False


def open_dashboard(url: str) -> bool:
    if not _is_loopback(url):
        return False
    if sys.platform != "win32":
        return bool(webbrowser.open(url))
    prefix = [sys.executable]
    if not getattr(sys, "frozen", False):
        prefix.extend(("-m", "timetrace.client.cli"))
    subprocess.Popen([*prefix, "--window", url], creationflags=subprocess.CREATE_NO_WINDOW)
    return True


def open_running_dashboard(port: int) -> bool:
    url = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/healthz", timeout=1) as response:
            if response.headers.get("X-TimeTrace-Control") != "client":
                return False
    except OSError:
        return False
    return open_dashboard(url)


def show_dashboard(url: str) -> None:
    if sys.platform != "win32" or not _is_loopback(url):
        raise SystemExit(2)
    import webview
    import win32con
    import win32gui

    with ClientInstance.acquire(rf"Local\{APP_USER_MODEL_ID}.Dashboard") as instance:
        if not instance.acquired:
            hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
            if hwnd:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass
            return
        try:
            webview.create_window(
                WINDOW_TITLE, url, width=1100, height=780, min_size=(680, 520), text_select=True
            )
            webview.start(gui="edgechromium", private_mode=True)
        except Exception:
            webbrowser.open(url)
