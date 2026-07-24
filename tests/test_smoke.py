"""Smoke test: the app imports and the main window can be constructed."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from camview.ui.main_window import MainWindow


def _get_application() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_constructs() -> None:
    _get_application()

    window = MainWindow()
    try:
        assert window.windowTitle() == "CamView"
        assert window.device_tree is not None
        assert window.layout_selector.count() == 4
        assert window.statusBar().currentMessage() == "Ready"
    finally:
        window.close()
