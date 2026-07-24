"""CamView's main window: sidebar, mosaic area, toolbar, status bar.

This is the Phase 0 shell — the sidebar tree, mosaic grid, and layout
selector are wired up for real in later phases (2, 4).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QLabel,
    QMainWindow,
    QToolBar,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_MOSAIC_LAYOUTS = ["1x1", "2x2", "3x3", "4x4"]


class MainWindow(QMainWindow):
    """CamView's top-level window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CamView")
        self.resize(1280, 800)

        self._build_sidebar()
        self._build_central_widget()
        self._build_toolbar()
        self._build_menu()
        self._build_statusbar()

        logger.debug("MainWindow constructed")

    def _build_sidebar(self) -> None:
        self.device_tree = QTreeWidget()
        self.device_tree.setHeaderLabels(["NVRs / Cameras"])

        dock = QDockWidget("Devices", self)
        dock.setObjectName("devicesDock")
        dock.setWidget(self.device_tree)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_central_widget(self) -> None:
        placeholder = QLabel("Mosaic area — no cameras yet")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: palette(mid); font-size: 14pt;")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(placeholder)
        self.setCentralWidget(central)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)

        self.layout_selector = QComboBox()
        self.layout_selector.addItems(_MOSAIC_LAYOUTS)
        self.layout_selector.setEnabled(False)
        self.layout_selector.setToolTip("Mosaic layout (enabled in a later phase)")
        toolbar.addWidget(self.layout_selector)

        self.addToolBar(toolbar)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _build_statusbar(self) -> None:
        self.statusBar().showMessage("Ready")
