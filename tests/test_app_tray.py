"""Regression tests for restoring the desktop GUI from the system tray."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSystemTrayIcon

from src.app import KnowledgeBaseApp


class _FakeWindow:
    def __init__(self, state: Qt.WindowState):
        self._state = state
        self.calls: list[str] = []

    def windowState(self) -> Qt.WindowState:
        return self._state

    def setWindowState(self, state: Qt.WindowState) -> None:
        self._state = state
        self.calls.append("setWindowState")

    def show(self) -> None:
        self.calls.append("show")

    def raise_(self) -> None:
        self.calls.append("raise")

    def activateWindow(self) -> None:
        self.calls.append("activate")


def test_restore_main_window_unminimizes_and_activates() -> None:
    app = KnowledgeBaseApp.__new__(KnowledgeBaseApp)
    app.window = _FakeWindow(Qt.WindowState.WindowMinimized)

    app.restore_main_window()

    assert app.window.windowState() == Qt.WindowState.WindowNoState
    assert app.window.calls == ["setWindowState", "show", "raise", "activate"]


def test_tray_click_restores_but_context_menu_does_not() -> None:
    class TrayHost:
        restored = 0

        def restore_main_window(self) -> None:
            self.restored += 1

    host = TrayHost()

    KnowledgeBaseApp._on_tray_activated(host, QSystemTrayIcon.ActivationReason.Trigger)
    KnowledgeBaseApp._on_tray_activated(host, QSystemTrayIcon.ActivationReason.Context)

    assert host.restored == 1
