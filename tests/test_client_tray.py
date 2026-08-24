from __future__ import annotations

from types import SimpleNamespace

from timetrace.client.tray import TrayIcon
from timetrace.common.config import PrivacyConfig


class _Menu:
    SEPARATOR = object()

    def __init__(self, *items) -> None:  # noqa: ANN002
        self.items = list(items)


class _MenuItem:
    def __init__(self, text, action, **kwargs) -> None:  # noqa: ANN001
        self.text = text
        self.action = action
        self.enabled = kwargs.get("enabled")


class _Pystray:
    Menu = _Menu
    MenuItem = _MenuItem


class _Controller:
    def __init__(self) -> None:
        self.paused = False
        self.pause_requests: list[bool] = []
        self.open_requests = 0
        self.shutdown_requests = 0

    def cached_snapshot(self):  # noqa: ANN201
        return SimpleNamespace(
            paused=self.paused,
            endpoints=(),
            control_url="http://127.0.0.1:8764",
        )

    def set_paused_from_thread(self, paused: bool) -> None:
        self.pause_requests.append(paused)

    def open_control_panel_from_thread(self) -> None:
        self.open_requests += 1

    def request_shutdown_from_thread(self) -> None:
        self.shutdown_requests += 1


def test_tray_actions_route_through_controller_without_mutating_privacy():
    controller = _Controller()
    privacy = PrivacyConfig(paused=False)
    legacy_quit: list[bool] = []
    tray = TrayIcon(
        privacy,
        lambda: legacy_quit.append(True),
        controller=controller,  # type: ignore[arg-type]
    )
    menu = tray._build_menu(_Pystray)  # noqa: SLF001
    icon = SimpleNamespace(title="TimeTrace", stop=lambda: None)

    menu.items[0].action(icon, None)
    menu.items[1].action(icon, None)
    menu.items[-1].action(icon, None)

    assert controller.pause_requests == [True]
    assert controller.open_requests == 1
    assert controller.shutdown_requests == 1
    assert privacy.paused is False
    assert legacy_quit == []
