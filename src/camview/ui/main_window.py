"""CamView's main window: sidebar, mosaic area, toolbar, status bar.

The mosaic grid and layout selector are still placeholders — wired up
for real in Phase 4. NVR registration (sidebar tree, add/edit/remove)
is real as of Phase 2.
"""

from __future__ import annotations

import logging
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from camview.database.repositories import CameraRepository, NvrRepository
from camview.services.credentials import (
    CredentialsError,
    delete_nvr_password,
    get_nvr_password,
    set_nvr_password,
)
from camview.services.rtsp import build_channel_url, generate_missing_channel_cameras
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.widgets.device_tree import CAMERA_ID_ROLE, NVR_ID_ROLE, DeviceTree
from camview.ui.widgets.video_tile import VideoTile

logger = logging.getLogger(__name__)

_MOSAIC_LAYOUTS = ["1x1", "2x2", "3x3", "4x4"]


class MainWindow(QMainWindow):
    """CamView's top-level window."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CamView")
        self.resize(1280, 800)
        self._connection = connection
        self._nvr_repository = NvrRepository(connection)
        self._camera_repository = CameraRepository(connection)
        self._video_tile: VideoTile | None = None

        self._build_sidebar()
        self._build_central_widget()
        self._build_toolbar()
        self._build_menu()
        self._build_statusbar()

        logger.debug("MainWindow constructed")

    def _build_sidebar(self) -> None:
        self.device_tree = DeviceTree(self._nvr_repository, self._camera_repository)
        self.device_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.device_tree.customContextMenuRequested.connect(
            self._show_device_tree_context_menu
        )
        self.device_tree.itemDoubleClicked.connect(
            self._on_device_tree_item_double_clicked
        )

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

        nvr_menu = self.menuBar().addMenu("&NVR")

        add_nvr_action = QAction("&Adicionar NVR...", self)
        add_nvr_action.triggered.connect(self._add_nvr)
        nvr_menu.addAction(add_nvr_action)

    def _build_statusbar(self) -> None:
        self.statusBar().showMessage("Ready")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._video_tile is not None:
            self._video_tile.close_stream()
        super().closeEvent(event)

    def _show_device_tree_context_menu(self, position: object) -> None:
        item = self.device_tree.itemAt(position)  # type: ignore[arg-type]
        if item is None or item.parent() is not None:
            return  # Only top-level (NVR) items have a context menu for now.

        nvr_id = item.data(0, NVR_ID_ROLE)

        menu = QMenu(self)
        edit_action = menu.addAction("Editar...")
        remove_action = menu.addAction("Remover")
        chosen = menu.exec(self.device_tree.viewport().mapToGlobal(position))  # type: ignore[arg-type]
        if chosen is edit_action:
            self._edit_nvr(nvr_id)
        elif chosen is remove_action:
            self._remove_nvr(nvr_id)

    def _add_nvr(self) -> None:
        dialog = NvrDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        nvr = dialog.result_nvr()
        password = dialog.result_password()
        try:
            created = self._nvr_repository.create(nvr)
            set_nvr_password(created.id, password)  # type: ignore[arg-type]
            for camera in generate_missing_channel_cameras(
                created.id,  # type: ignore[arg-type]
                created.channel_count,
            ):
                self._camera_repository.create(camera)
        except (CredentialsError, sqlite3.Error) as exc:
            logger.error("Failed to add NVR: %s", exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return

        self.device_tree.refresh()
        self.statusBar().showMessage(f"NVR '{created.name}' adicionado.", 5000)

    def _edit_nvr(self, nvr_id: int) -> None:
        existing = self._nvr_repository.get(nvr_id)
        if existing is None:
            return

        try:
            password = get_nvr_password(nvr_id) or ""
        except CredentialsError as exc:
            QMessageBox.warning(self, "CamView", str(exc))
            password = ""

        dialog = NvrDialog(nvr=existing, password=password, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        updated = dialog.result_nvr(existing=existing)
        try:
            self._nvr_repository.update(updated)
            set_nvr_password(nvr_id, dialog.result_password())
            existing_channels = {
                camera.channel_number
                for camera in self._camera_repository.list_by_nvr(nvr_id)
            }
            for camera in generate_missing_channel_cameras(
                nvr_id, updated.channel_count, existing_channels
            ):
                self._camera_repository.create(camera)
        except (CredentialsError, sqlite3.Error) as exc:
            logger.error("Failed to update NVR %d: %s", nvr_id, exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return

        self.device_tree.refresh()
        self.statusBar().showMessage(f"NVR '{updated.name}' atualizado.", 5000)

    def _remove_nvr(self, nvr_id: int) -> None:
        nvr = self._nvr_repository.get(nvr_id)
        if nvr is None:
            return

        confirm = QMessageBox.question(
            self,
            "CamView",
            f"Remover o NVR '{nvr.name}' e todas as suas câmeras?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            self._nvr_repository.delete(nvr_id)
            delete_nvr_password(nvr_id)
        except (CredentialsError, sqlite3.Error) as exc:
            logger.error("Failed to remove NVR %d: %s", nvr_id, exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return

        self.device_tree.refresh()
        self.statusBar().showMessage(f"NVR '{nvr.name}' removido.", 5000)

    def _on_device_tree_item_double_clicked(
        self, item: QTreeWidgetItem, column: int
    ) -> None:
        camera_id = item.data(0, CAMERA_ID_ROLE)
        if camera_id is None:
            return  # An NVR row was double-clicked; nothing to play yet.
        self._show_camera_stream(camera_id)

    def _show_camera_stream(self, camera_id: int) -> None:
        camera = self._camera_repository.get(camera_id)
        if camera is None:
            return
        nvr = self._nvr_repository.get(camera.nvr_id)
        if nvr is None:
            return

        try:
            password = get_nvr_password(nvr.id) or ""  # type: ignore[arg-type]
        except CredentialsError as exc:
            QMessageBox.critical(self, "CamView", str(exc))
            return

        # Never attempt RTSP with an empty password. NVRs (Hikvision in
        # particular) lock out the source IP after a few failed logins, so a
        # missing keyring entry must fail loudly here instead of burning
        # authentication attempts against the device.
        if not password:
            QMessageBox.warning(
                self,
                "CamView",
                f"Nenhuma senha armazenada para o NVR '{nvr.name}'.\n\n"
                "Edite o NVR e informe a senha antes de abrir o stream.",
            )
            return

        url = build_channel_url(
            host=nvr.host,
            port=nvr.rtsp_port,
            username=nvr.username,
            password=password,
            channel_number=camera.channel_number,
            stream_type=nvr.default_stream,
        )

        self._close_current_tile()
        tile = VideoTile(title=camera.name, url=url, parent=self)
        tile.closeRequested.connect(self._close_current_tile)
        self._video_tile = tile
        self.setCentralWidget(tile)
        self.statusBar().showMessage(f"Conectando a '{camera.name}'...", 5000)

    def _close_current_tile(self) -> None:
        if self._video_tile is not None:
            self._video_tile.close_stream()
            self._video_tile = None
        self._build_central_widget()
