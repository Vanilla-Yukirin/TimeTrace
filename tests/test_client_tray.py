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
        # Match pystray's strict callback contract: default parameters still
        # count toward co_argcount, so closure factories are required.
        for callback, max_args in (
            (text, 1),
            (action, 2),
            (kwargs.get("enabled"), 1),
            (kwargs.get("checked"), 1),
        ):
            if callable(callback) and hasattr(callback, "__code__"):
                assert callback.__code__.co_argcount <= max_args
        self.text = text
        self.action = action
        self.enabled = kwargs.get("enabled")
        self.checked = kwargs.get("checked")


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

    def set_endpoint_enabled_from_thread(self, index: int, enabled: bool) -> None:
        self.endpoint_request = (index, enabled)


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


def test_controller_endpoint_menu_callbacks_match_pystray_arity():
    controller = _Controller()
    controller.cached_snapshot = lambda: SimpleNamespace(
        paused=False,
        control_url="http://127.0.0.1:8764",
        endpoints=(
            SimpleNamespace(
                index=0,
                name="public",
                url="https://timetrace.example",
                enabled=True,
                active=True,
                healthy=True,
            ),
        ),
    )
    tray = TrayIcon(
        PrivacyConfig(paused=False),
        lambda: None,
        controller=controller,  # type: ignore[arg-type]
    )

    menu = tray._build_menu(_Pystray)  # noqa: SLF001
    endpoint_item = menu.items[2].action.items[0]
    assert endpoint_item.text(None) == "● public  (https://timetrace.example)"
    assert endpoint_item.checked(None) is True
    endpoint_item.action(None, None)
    assert controller.endpoint_request == (0, False)
