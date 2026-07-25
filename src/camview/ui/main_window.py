"""CamView's main window: sidebar, mosaic area, toolbar, status bar.

Owns the wiring between the device tree, the mosaic and the database:
turning a camera id into an RTSP URL, opening a whole NVR at once, and
saving/restoring named layouts.
"""

from __future__ import annotations

import base64
import binascii
import logging
import sqlite3
from functools import partial

from PySide6.QtCore import QByteArray, QSignalBlocker, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
    QTreeWidgetItem,
    QWidget,
)

from camview.database.repositories import (
    CameraRepository,
    LayoutRepository,
    NvrRepository,
    SettingsRepository,
)
from camview.models.camera import Camera, StreamType
from camview.models.layout import Layout, LayoutItem
from camview.models.nvr import Nvr
from camview.services.credentials import (
    CredentialsError,
    delete_nvr_password,
    get_nvr_password,
    set_nvr_password,
)
from camview.services.hikvision import DiscoveredChannel
from camview.services.rtsp import build_channel_url, generate_missing_channel_cameras
from camview.ui.dialogs.layout_dialog import LayoutManagerDialog
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.widgets.device_tree import CAMERA_ID_ROLE, NVR_ID_ROLE, DeviceTree
from camview.ui.widgets.video_grid import GRID_SHAPES, VideoGrid, smallest_shape_for
from camview.ui.widgets.video_tile import VideoTile

logger = logging.getLogger(__name__)

DEFAULT_GRID_SHAPE = "2x2"

#: ``settings`` keys used to bring the previous session back on startup.
SETTING_WINDOW_GEOMETRY = "window/geometry"
SETTING_WINDOW_STATE = "window/state"
SETTING_GRID_SHAPE = "mosaic/grid_shape"
SETTING_LAST_LAYOUT_ID = "mosaic/last_layout_id"


def _encode(blob: QByteArray) -> str:
    """Qt geometry/state blobs are binary; ``settings`` stores text."""
    return base64.b64encode(bytes(blob)).decode("ascii")


def _decode(value: str | None) -> QByteArray | None:
    if not value:
        return None
    try:
        return QByteArray(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError):
        logger.warning("Ignoring corrupt stored window state")
        return None


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
        self._layout_repository = LayoutRepository(connection)
        self._settings_repository = SettingsRepository(connection)
        #: Saved layout currently on screen, if any — the target of "Salvar".
        self._current_layout_id: int | None = None

        self._build_sidebar()
        self._build_central_widget()
        self._build_toolbar()
        self._build_menu()
        self._build_statusbar()
        self._restore_session()

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
        rows, columns = GRID_SHAPES[DEFAULT_GRID_SHAPE]
        self.video_grid = VideoGrid(rows=rows, columns=columns)
        self.video_grid.cameraDropped.connect(self._on_camera_dropped)
        self.setCentralWidget(self.video_grid)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)

        self.layout_selector = QComboBox()
        self.layout_selector.addItems(list(GRID_SHAPES))
        self.layout_selector.setCurrentText(DEFAULT_GRID_SHAPE)
        self.layout_selector.setToolTip("Formato do mosaico")
        self.layout_selector.currentTextChanged.connect(self._on_grid_shape_changed)
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

        self.layouts_menu = self.menuBar().addMenu("&Layouts")
        # Rebuilt on open: saved layouts change from the manager dialog and
        # from "Salvar como", so a menu built once would go stale.
        self.layouts_menu.aboutToShow.connect(self._rebuild_layouts_menu)
        self._rebuild_layouts_menu()

    def _build_statusbar(self) -> None:
        self.statusBar().showMessage("Ready")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_session()
        # Release every libVLC player before the widgets are torn down.
        self.video_grid.clear()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Session state (window geometry, grid, last layout)
    # ------------------------------------------------------------------

    def _save_session(self) -> None:
        """Remember where the window was and what it was showing.

        Never allowed to block closing the app: a settings write that
        fails is logged and swallowed.
        """
        try:
            self._settings_repository.set(
                SETTING_WINDOW_GEOMETRY, _encode(self.saveGeometry())
            )
            self._settings_repository.set(
                SETTING_WINDOW_STATE, _encode(self.saveState())
            )
            self._settings_repository.set(
                SETTING_GRID_SHAPE,
                f"{self.video_grid.rows}x{self.video_grid.columns}",
            )
            if self._current_layout_id is None:
                self._settings_repository.delete(SETTING_LAST_LAYOUT_ID)
            else:
                self._settings_repository.set(
                    SETTING_LAST_LAYOUT_ID, str(self._current_layout_id)
                )
        except sqlite3.Error as exc:
            logger.warning("Could not save session state: %s", exc)

    def _restore_session(self) -> None:
        """Put the window back where it was, showing what it was showing.

        Each part is restored independently: a corrupt geometry blob must
        not cost the user their last layout, and vice versa.
        """
        try:
            settings = self._settings_repository.get_all()
        except sqlite3.Error as exc:
            logger.warning("Could not read session state: %s", exc)
            return

        geometry = _decode(settings.get(SETTING_WINDOW_GEOMETRY))
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = _decode(settings.get(SETTING_WINDOW_STATE))
        if state is not None:
            self.restoreState(state)

        shape = GRID_SHAPES.get(settings.get(SETTING_GRID_SHAPE, ""))
        if shape is not None:
            self._apply_grid_shape(*shape)

        self._restore_last_layout(settings.get(SETTING_LAST_LAYOUT_ID))

    def _restore_last_layout(self, raw_layout_id: str | None) -> None:
        """Reopen the layout from last time, if it is still there."""
        if not raw_layout_id:
            return
        try:
            layout_id = int(raw_layout_id)
        except ValueError:
            logger.warning("Ignoring invalid stored layout id %r", raw_layout_id)
            return

        if self._layout_repository.get(layout_id) is None:
            logger.info("Last layout %d no longer exists; starting empty", layout_id)
            return

        # Quiet: a device missing its password must not greet the user with
        # a modal dialog before the window is even on screen.
        self._load_layout(layout_id, quiet=True)

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
            self._create_cameras(created, dialog.discovered_channels)
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
            self._create_cameras(updated, dialog.discovered_channels)
        except (CredentialsError, sqlite3.Error) as exc:
            logger.error("Failed to update NVR %d: %s", nvr_id, exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return

        self.device_tree.refresh()
        self.statusBar().showMessage(f"NVR '{updated.name}' atualizado.", 5000)

    def _create_cameras(
        self, nvr: Nvr, discovered: list[DiscoveredChannel] | None
    ) -> None:
        """Create the camera rows this NVR is missing.

        Prefers the channel list the device itself reported: real NVRs have
        gaps (a 16-slot recorder with nothing on channel 12), and they know
        each camera's configured name. Without discovery, falls back to a
        plain ``1..channel_count`` sequence.

        Existing channels are never touched, so editing an NVR only ever
        adds what is new.
        """
        existing = {
            camera.channel_number
            for camera in self._camera_repository.list_by_nvr(nvr.id)  # type: ignore[arg-type]
        }

        if discovered:
            new_cameras = [
                Camera(
                    nvr_id=nvr.id,  # type: ignore[arg-type]
                    channel_number=channel.channel_number,
                    name=channel.name,
                )
                for channel in discovered
                if channel.channel_number not in existing
            ]
        else:
            new_cameras = generate_missing_channel_cameras(
                nvr.id,  # type: ignore[arg-type]
                nvr.channel_count,
                existing,
            )

        for camera in new_cameras:
            self._camera_repository.create(camera)

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

    # ------------------------------------------------------------------
    # Saved layouts
    # ------------------------------------------------------------------

    def _rebuild_layouts_menu(self) -> None:
        """Rebuild the Layouts menu, including one entry per saved layout."""
        menu = self.layouts_menu
        menu.clear()

        save_action = menu.addAction("&Salvar layout")
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_layout)

        save_as_action = menu.addAction("Salvar &como...")
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self._save_layout_as)

        manage_action = menu.addAction("&Gerenciar layouts...")
        manage_action.triggered.connect(self._manage_layouts)

        layouts = self._layout_repository.list_all()
        if not layouts:
            return

        menu.addSeparator()
        for layout in layouts:
            action = menu.addAction(layout.name)
            action.setCheckable(True)
            action.setChecked(layout.id == self._current_layout_id)
            action.triggered.connect(partial(self._load_layout, layout.id))

    def _set_current_layout(self, layout: Layout | None) -> None:
        """Track which saved layout is on screen and show it in the title."""
        self._current_layout_id = layout.id if layout is not None else None
        title = "CamView" if layout is None else f"CamView — {layout.name}"
        self.setWindowTitle(title)

    def _capture_layout_items(self, layout_id: int) -> list[LayoutItem]:
        """Snapshot the mosaic as layout items, one per occupied cell."""
        items: list[LayoutItem] = []
        for position, tile in sorted(self.video_grid.tiles().items()):
            if tile.camera_id is None:
                continue
            stream_type = (
                self.video_grid.mosaic_stream_type(position) or tile.stream_type
            )
            items.append(
                LayoutItem(
                    layout_id=layout_id,
                    camera_id=tile.camera_id,
                    position=position,
                    stream_type=stream_type,
                )
            )
        return items

    def _save_layout(self) -> None:
        """Overwrite the layout on screen, or ask for a name if there is none."""
        layout = (
            None
            if self._current_layout_id is None
            else self._layout_repository.get(self._current_layout_id)
        )
        if layout is None:
            self._save_layout_as()
            return
        self._write_layout(layout.name, existing=layout)

    def _save_layout_as(self) -> None:
        if not self.video_grid.tiles():
            self.statusBar().showMessage(
                "Nada para salvar — o mosaico está vazio.", 5000
            )
            return

        name, confirmed = QInputDialog.getText(
            self, "Salvar layout", "Nome do layout:"
        )
        name = name.strip()
        if not confirmed or not name:
            return

        existing = self._layout_repository.get_by_name(name)
        if existing is not None:
            overwrite = QMessageBox.question(
                self,
                "CamView",
                f"Já existe um layout chamado '{name}'. Sobrescrever?",
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return

        self._write_layout(name, existing=existing)

    def _write_layout(self, name: str, existing: Layout | None) -> None:
        """Persist the current mosaic under ``name``, creating or replacing."""
        if not self.video_grid.tiles():
            self.statusBar().showMessage(
                "Nada para salvar — o mosaico está vazio.", 5000
            )
            return

        rows, columns = self.video_grid.rows, self.video_grid.columns
        try:
            if existing is None:
                layout = self._layout_repository.create(
                    Layout(name=name, rows=rows, columns=columns)
                )
            else:
                layout = existing
                self._layout_repository.update_shape(layout.id, rows, columns)  # type: ignore[arg-type]
                layout.rows, layout.columns = rows, columns
            self._layout_repository.set_items(
                layout.id,  # type: ignore[arg-type]
                self._capture_layout_items(layout.id),  # type: ignore[arg-type]
            )
        except sqlite3.Error as exc:
            logger.error("Failed to save layout '%s': %s", name, exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return

        self._set_current_layout(layout)
        self.statusBar().showMessage(f"Layout '{layout.name}' salvo.", 5000)

    def _load_layout(self, layout_id: int, quiet: bool = False) -> None:
        """Replace the mosaic with a saved layout's grid and cameras.

        ``quiet`` keeps password problems out of modal dialogs; see
        :meth:`_nvr_password_or_warn`.
        """
        layout = self._layout_repository.get(layout_id)
        if layout is None:
            self.statusBar().showMessage("Esse layout não existe mais.", 5000)
            return

        items = self._layout_repository.get_items(layout_id)
        self.video_grid.clear()
        self._apply_grid_shape(layout.rows, layout.columns)

        # One password lookup per NVR, not per cell: a device with no stored
        # password must warn once, and every cell would hit the keyring.
        passwords: dict[int, str | None] = {}
        opened = 0
        skipped = 0
        for item in items:
            if item.position >= self.video_grid.cell_count:
                skipped += 1
                continue
            camera = self._camera_repository.get(item.camera_id)
            if camera is None:
                skipped += 1
                continue
            nvr = self._nvr_repository.get(camera.nvr_id)
            if nvr is None:
                skipped += 1
                continue
            if nvr.id not in passwords:
                passwords[nvr.id] = self._nvr_password_or_warn(nvr, quiet=quiet)  # type: ignore[index]
            password = passwords[nvr.id]  # type: ignore[index]
            if password is None:
                skipped += 1
                continue
            self._place_camera(camera, nvr, password, item.position, item.stream_type)
            opened += 1

        self._set_current_layout(layout)
        message = f"Layout '{layout.name}': {opened} câmera(s)."
        if skipped:
            message += f" {skipped} não pôde(puderam) ser aberta(s)."
        self.statusBar().showMessage(message, 5000)

    def _manage_layouts(self) -> None:
        dialog = LayoutManagerDialog(self._layout_repository, parent=self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted

        # The dialog may have renamed or deleted the layout on screen.
        if self._current_layout_id is not None:
            self._set_current_layout(
                self._layout_repository.get(self._current_layout_id)
            )

        if accepted and dialog.selected_layout_id is not None:
            self._load_layout(dialog.selected_layout_id)

    def _on_device_tree_item_double_clicked(
        self, item: QTreeWidgetItem, column: int
    ) -> None:
        camera_id = item.data(0, CAMERA_ID_ROLE)
        if camera_id is not None:
            position = self.video_grid.first_free_position()
            if position is None:
                self.statusBar().showMessage(
                    "Mosaico cheio — remova uma câmera ou escolha uma grade maior.",
                    5000,
                )
                return
            self._open_camera_at(camera_id, position)
            return

        nvr_id = item.data(0, NVR_ID_ROLE)
        if nvr_id is not None:
            self._open_nvr_mosaic(nvr_id)

    def _nvr_password_or_warn(self, nvr: Nvr, quiet: bool = False) -> str | None:
        """Fetch the NVR's password, reporting to the user if unusable.

        Returns ``None`` when the stream must not be attempted. Callers
        opening several cameras at once should call this once, not per
        camera, so a missing password produces one message rather than one
        per cell.

        ``quiet`` suppresses the dialogs (used while restoring the previous
        session at startup, where the caller summarises in the status bar
        instead of stacking modals over a window that isn't shown yet).
        """
        try:
            password = get_nvr_password(nvr.id) or ""  # type: ignore[arg-type]
        except CredentialsError as exc:
            logger.error("Credentials unavailable for NVR %s: %s", nvr.name, exc)
            if not quiet:
                QMessageBox.critical(self, "CamView", str(exc))
            return None

        # Never attempt RTSP with an empty password. NVRs (Hikvision in
        # particular) lock out the source IP after a few failed logins, so a
        # missing keyring entry must fail loudly here instead of burning
        # authentication attempts against the device.
        if not password:
            logger.warning("No stored password for NVR '%s'", nvr.name)
            if not quiet:
                QMessageBox.warning(
                    self,
                    "CamView",
                    f"Nenhuma senha armazenada para o NVR '{nvr.name}'.\n\n"
                    "Edite o NVR e informe a senha antes de abrir o stream.",
                )
            return None
        return password

    def _place_camera(
        self,
        camera: Camera,
        nvr: Nvr,
        password: str,
        position: int,
        stream_type: StreamType,
    ) -> None:
        # Both URLs are built up front so the tile can switch streams on
        # maximize without another keyring lookup.
        stream_urls = {
            candidate: build_channel_url(
                host=nvr.host,
                port=nvr.rtsp_port,
                username=nvr.username,
                password=password,
                channel_number=camera.channel_number,
                stream_type=candidate,
            )
            for candidate in StreamType
        }
        tile = VideoTile(
            title=camera.name,
            stream_urls=stream_urls,
            stream_type=stream_type,
            camera_id=camera.id,
        )
        self.video_grid.place_tile(position, tile)

    def _open_camera_at(self, camera_id: int, position: int) -> None:
        """Build the RTSP URL for ``camera_id`` and open it in cell ``position``."""
        camera = self._camera_repository.get(camera_id)
        if camera is None:
            return
        nvr = self._nvr_repository.get(camera.nvr_id)
        if nvr is None:
            return

        password = self._nvr_password_or_warn(nvr)
        if password is None:
            return

        self._place_camera(
            camera,
            nvr,
            password,
            position,
            self._mosaic_stream_type(nvr.default_stream),
        )
        self.statusBar().showMessage(f"Conectando a '{camera.name}'...", 5000)

    def _open_nvr_mosaic(self, nvr_id: int) -> None:
        """Open every channel of one NVR, resizing the mosaic to fit.

        Replaces whatever is currently on screen — double-clicking an NVR
        means "show me this device", so a partial mix with other NVRs would
        be surprising.
        """
        nvr = self._nvr_repository.get(nvr_id)
        if nvr is None:
            return

        cameras = [
            camera
            for camera in self._camera_repository.list_by_nvr(nvr_id)
            if camera.enabled
        ]
        if not cameras:
            self.statusBar().showMessage(
                f"O NVR '{nvr.name}' não tem câmeras cadastradas.", 5000
            )
            return

        password = self._nvr_password_or_warn(nvr)
        if password is None:
            return

        rows, columns = smallest_shape_for(len(cameras))
        self.video_grid.clear()
        self._apply_grid_shape(rows, columns)
        # This wholesale replaces the screen, so it is no longer the saved
        # layout that was loaded — keep the window title honest.
        self._set_current_layout(None)

        visible = cameras[: rows * columns]
        stream_type = self._mosaic_stream_type(nvr.default_stream)
        for position, camera in enumerate(visible):
            self._place_camera(camera, nvr, password, position, stream_type)

        message = f"Abrindo {len(visible)} câmeras de '{nvr.name}'..."
        if len(cameras) > len(visible):
            message += f" ({len(cameras) - len(visible)} não cabem no mosaico.)"
        self.statusBar().showMessage(message, 5000)

    def _apply_grid_shape(self, rows: int, columns: int) -> None:
        """Reshape the grid and keep the toolbar selector in sync."""
        self.video_grid.set_grid_shape(rows, columns)
        with QSignalBlocker(self.layout_selector):
            self.layout_selector.setCurrentText(f"{rows}x{columns}")

    def _mosaic_stream_type(self, nvr_default: StreamType) -> StreamType:
        """Which stream to use for a mosaic cell.

        Substream for real mosaics: 16 simultaneous main streams would be
        both needless bandwidth and far more decoding than a cell-sized
        viewport can show. A 1x1 grid is effectively single-camera view, so
        there the NVR's own default is honoured. Phase 5 makes this
        per-cell and persists it with the layout.
        """
        if self.video_grid.cell_count == 1:
            return nvr_default
        return StreamType.SUB

    def _on_camera_dropped(self, camera_id: int, position: int) -> None:
        self._open_camera_at(camera_id, position)

    def _on_grid_shape_changed(self, label: str) -> None:
        shape = GRID_SHAPES.get(label)
        if shape is None:
            return
        self.video_grid.set_grid_shape(*shape)
        self.statusBar().showMessage(f"Mosaico {label}.", 3000)
