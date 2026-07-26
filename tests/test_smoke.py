"""Smoke test: the app imports and the main window can be constructed."""

from __future__ import annotations

import sqlite3

from PySide6.QtWidgets import QApplication

from camview.ui.main_window import MainWindow
from camview.ui.widgets.grid_shapes import GRID_SHAPES


def test_main_window_constructs(
    qapp: QApplication, db_connection: sqlite3.Connection
) -> None:
    window = MainWindow(connection=db_connection)
    try:
        assert window.windowTitle() == "CamView"
        assert window.device_tree is not None
        assert window.layout_selector.count() == len(GRID_SHAPES)
        assert window.statusBar().currentMessage() == "Pronto"
    finally:
        window.close()
