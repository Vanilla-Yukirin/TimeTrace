"""Idle detection via pynput: tracks last keyboard/mouse activity time."""

from __future__ import annotations

import threading
import time

from pynput import keyboard, mouse


class IdleDetector:
    """Thread-safe tracker of the last user input timestamp.

    Starts background listeners for keyboard and mouse events and
    exposes ``idle_seconds`` to query how long the user has been inactive.
    """

    def __init__(self) -> None:
        self._last_activity: float = time.monotonic()
        self._lock = threading.Lock()
        self._kb_listener: keyboard.Listener | None = None
        self._ms_listener: mouse.Listener | None = None

    def start(self) -> None:
        """Start background pynput listeners (non-blocking)."""
        self._kb_listener = keyboard.Listener(
            on_press=self._on_activity,
            on_release=self._on_activity,
            suppress=False,
        )
        self._ms_listener = mouse.Listener(
            on_move=self._on_activity,
            on_click=self._on_activity,
            on_scroll=self._on_activity,
            suppress=False,
        )
        # Mark as daemon so process exit is not blocked if stop() never runs.
        self._kb_listener.daemon = True
        self._ms_listener.daemon = True
        self._kb_listener.start()
        self._ms_listener.start()

    def stop(self) -> None:
        if self._kb_listener:
            self._kb_listener.stop()
        if self._ms_listener:
            self._ms_listener.stop()

    @property
    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_activity

    def _on_activity(self, *_args: object) -> None:
        with self._lock:
            self._last_activity = time.monotonic()
