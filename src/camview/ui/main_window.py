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
from time import monotonic

from PySide6.QtCore import QByteArray, QSignalBlocker, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDockWidget,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
    QTreeWidgetItem,
    QVBoxLayout,
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
from camview.models.settings import AppSettings
from camview.services.credentials import (
    CredentialsError,
    delete_nvr_password,
    get_nvr_password,
    set_nvr_password,
)
from camview.services.hikvision import DiscoveredChannel, channel_online_status
from camview.services.rtsp import build_channel_url, generate_missing_channel_cameras
from camview.services.settings import (
    load_settings,
    playback_options_for,
    save_settings,
)
from camview.services.stream_manager import (
    VlcUnavailableError,
    bytes_received,
    get_vlc_instance,
)
from camview.ui.dialogs.layout_dialog import LayoutManagerDialog
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.dialogs.settings_dialog import SettingsDialog
from camview.ui.widgets.device_tree import CAMERA_ID_ROLE, NVR_ID_ROLE, DeviceTree
from camview.ui.widgets.status_panel import STATS_INTERVAL_MS, StatusPanel
from camview.ui.widgets.video_grid import GRID_SHAPES, VideoGrid, smallest_shape_for
from camview.ui.widgets.video_tile import ZOOM_STEP, ConnectionStatus, VideoTile

logger = logging.getLogger(__name__)

DEFAULT_GRID_SHAPE = "2x2"

#: ``settings`` keys used to bring the previous session back on startup.
SETTING_WINDOW_GEOMETRY = "window/geometry"
SETTING_WINDOW_STATE = "window/state"
SETTING_GRID_SHAPE = "mosaic/grid_shape"
SETTING_LAST_LAYOUT_ID = "mosaic/last_layout_id"

#: Minimum gap between "is this channel online?" queries to one device.
STATUS_QUERY_INTERVAL_S = 120.0
#: Budget for one such query; also how long closing waits for it.
STATUS_QUERY_TIMEOUT_S = 2.5


class _ChannelStatusWorker(QThread):
    """Asks a recorder which of its channels are online, off the GUI thread."""

    #: ``object``, not ``dict``: a ``dict`` signal argument is marshalled as
    #: a QVariantMap, which only takes string keys — channel numbers are
    #: ints, so the mapping arrived empty on the other side of the thread.
    finished_with = Signal(int, object)

    def __init__(
        self,
        nvr_id: int,
        host: str,
        username: str,
        password: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._nvr_id = nvr_id
        self._host = host
        self._username = username
        self._password = password

    def run(self) -> None:
        try:
            status = channel_online_status(
                self._host,
                self._username,
                self._password,
                timeout=STATUS_QUERY_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - a diagnostic must not crash
            logger.warning("Channel status query failed for %s: %s", self._host, exc)
            status = {}
        self.finished_with.emit(self._nvr_id, status)


def _icon(*names: str) -> QIcon:
    """First icon the desktop theme actually has, or an empty one.

    Names differ between themes (KDE ships ``configure``, others only
    ``preferences-system``), and an empty QIcon simply renders as no icon
    rather than a broken image — so nothing here can fail visibly.
    """
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return QIcon()


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
        try:
            self._settings: AppSettings = load_settings(self._settings_repository)
        except sqlite3.Error as exc:
            # Unreadable settings must not cost the user the whole window;
            # defaults are exactly the behaviour before settings existed.
            logger.warning("Could not read settings, using defaults: %s", exc)
            self._settings = AppSettings()
        #: Saved layout currently on screen, if any — the target of "Salvar".
        self._current_layout_id: int | None = None
        #: libVLC availability, probed lazily and reported at most once.
        self._vlc_checked = False
        self._vlc_available = True
        #: Throttling and ownership for the channel-status queries.
        self._status_checked_at: dict[int, float] = {}
        self._status_workers: set[_ChannelStatusWorker] = set()

        self._build_sidebar()
        self._build_central_widget()
        self._build_toolbar()
        self._build_menu()
        self._build_shortcuts()
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

        self.status_panel = StatusPanel()
        self.status_panel.setVisible(self._settings.show_status_panel)

        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_layout.setSpacing(6)
        sidebar_layout.addWidget(self.status_panel)
        sidebar_layout.addWidget(self.device_tree, stretch=1)

        # Traffic is a rate, so it needs two readings; the panel is fed on
        # the same beat it refreshes the machine figures on.
        self._stream_bytes: int = 0
        self._stream_sampled_at: float = monotonic()
        self._stream_timer = QTimer(self)
        self._stream_timer.timeout.connect(self._refresh_stream_stats)
        self._stream_timer.start(STATS_INTERVAL_MS)

        dock = QDockWidget("Devices", self)
        dock.setObjectName("devicesDock")
        dock.setWidget(sidebar)
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
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        add_nvr_action = QAction(_icon("list-add"), "Adicionar NVR", self)
        add_nvr_action.setToolTip("Cadastrar um novo NVR")
        add_nvr_action.triggered.connect(self._add_nvr)
        toolbar.addAction(add_nvr_action)
        toolbar.addSeparator()

        shape_label = QLabel("Mosaico: ")
        toolbar.addWidget(shape_label)

        self.layout_selector = QComboBox()
        self.layout_selector.addItems(list(GRID_SHAPES))
        self.layout_selector.setCurrentText(DEFAULT_GRID_SHAPE)
        self.layout_selector.setToolTip("Formato do mosaico")
        self.layout_selector.currentTextChanged.connect(self._on_grid_shape_changed)
        toolbar.addWidget(self.layout_selector)

        toolbar.addSeparator()
        save_layout_action = QAction(
            _icon("document-save"), "Salvar layout", self
        )
        save_layout_action.setToolTip("Salvar a composição atual (Ctrl+S)")
        save_layout_action.triggered.connect(self._save_layout)
        toolbar.addAction(save_layout_action)

        self.addToolBar(toolbar)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        settings_action = QAction(
            _icon("configure", "preferences-system"), "&Configurações...", self
        )
        settings_action.setShortcut(QKeySequence.StandardKey.Preferences)
        settings_action.triggered.connect(self._edit_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()

        quit_action = QAction(_icon("application-exit"), "&Sair", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        nvr_menu = self.menuBar().addMenu("&NVR")

        add_nvr_action = QAction(_icon("list-add"), "&Adicionar NVR...", self)
        add_nvr_action.triggered.connect(self._add_nvr)
        nvr_menu.addAction(add_nvr_action)

        self.layouts_menu = self.menuBar().addMenu("&Layouts")
        # Rebuilt on open: saved layouts change from the manager dialog and
        # from "Salvar como", so a menu built once would go stale.
        self.layouts_menu.aboutToShow.connect(self._rebuild_layouts_menu)
        self._rebuild_layouts_menu()

    def _build_shortcuts(self) -> None:
        """(Re)create the configurable shortcuts from the current settings.

        Rebuilt whenever settings change, so a new key takes effect without
        restarting. Actions live on the window rather than on a cell: they
        must work no matter which widget has focus.
        """
        for action in getattr(self, "_shortcut_actions", []):
            self.removeAction(action)

        bindings = (
            (self._settings.shortcut_next_camera, lambda: self._step_camera(1)),
            (self._settings.shortcut_previous_camera, lambda: self._step_camera(-1)),
            (self._settings.shortcut_zoom_in, lambda: self.video_grid.zoom_focused(ZOOM_STEP)),
            (
                self._settings.shortcut_zoom_out,
                lambda: self.video_grid.zoom_focused(1 / ZOOM_STEP),
            ),
            (self._settings.shortcut_zoom_reset, self.video_grid.reset_focused_zoom),
        )

        self._shortcut_actions: list[QAction] = []
        for sequence, slot in bindings:
            key = QKeySequence(sequence)
            if key.isEmpty():
                logger.warning("Ignoring unusable shortcut %r", sequence)
                continue
            action = QAction(self)
            action.setShortcut(key)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(slot)
            self.addAction(action)
            self._shortcut_actions.append(action)

    def _step_camera(self, offset: int) -> None:
        """Move to the next/previous camera, reporting where we landed."""
        self.video_grid.step(offset)
        tile = self.video_grid.focused_tile()
        if tile is not None:
            self.statusBar().showMessage(tile.title, 3000)

    def _build_statusbar(self) -> None:
        # Permanent widget (right-hand side): survives the transient
        # showMessage() texts, so the count is always readable.
        self.cell_count_label = QLabel()
        self.cell_count_label.setStyleSheet("color: palette(mid); margin-right: 6px;")
        self.statusBar().addPermanentWidget(self.cell_count_label)
        # Connected here, not in _build_central_widget: the grid emits while
        # rebuilding, and the label it updates must already exist.
        self.video_grid.contentsChanged.connect(self._update_cell_count)
        self._update_cell_count()
        self.statusBar().showMessage("Pronto")

    def _refresh_stream_stats(self) -> None:
        """Feed the status panel: cameras actually playing, and traffic."""
        tiles = self.video_grid.tiles().values()
        playing = sum(
            1 for tile in tiles if tile.status is ConnectionStatus.PLAYING
        )
        total_bytes = 0
        for tile in tiles:
            received = bytes_received(tile._player) if tile._player else None
            if received is not None:
                total_bytes += received

        now = monotonic()
        elapsed = now - self._stream_sampled_at
        # A counter that went backwards means a stream restarted; report
        # nothing rather than a negative rate.
        delta = total_bytes - self._stream_bytes
        rate = delta / elapsed if elapsed > 0 and delta >= 0 else 0.0
        self._stream_bytes = total_bytes
        self._stream_sampled_at = now

        self.status_panel.apply_streams(playing, len(tiles), rate)

    def _update_cell_count(self) -> None:
        used = len(self.video_grid.tiles())
        total = self.video_grid.cell_count
        self.cell_count_label.setText(f"{used}/{total} células")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_session()
        # A QThread destroyed while still running aborts the process, so
        # give the status queries their (short) budget to come back.
        for worker in list(self._status_workers):
            worker.wait(int(STATUS_QUERY_TIMEOUT_S * 1000) + 1000)
        # Release every libVLC player before the widgets are torn down.
        self.video_grid.clear()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _edit_settings(self) -> None:
        dialog = SettingsDialog(self._settings, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        updated = dialog.result_settings()
        try:
            save_settings(self._settings_repository, updated)
        except sqlite3.Error as exc:
            logger.error("Failed to save settings: %s", exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return

        self._settings = updated
        self._build_shortcuts()
        self.status_panel.setVisible(updated.show_status_panel)
        # Playback settings reach libVLC through media options, which are
        # read when a stream starts — so they apply to cells opened or
        # reconnected from now on, not to the ones already running.
        self.statusBar().showMessage(
            "Configurações salvas. Valem para as próximas conexões.", 5000
        )

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

        if self._settings.start_maximized:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

        shape = GRID_SHAPES.get(settings.get(SETTING_GRID_SHAPE, ""))
        if shape is not None:
            self._apply_grid_shape(*shape)

        if self._settings.restore_last_layout:
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

    # ------------------------------------------------------------------
    # Failure reporting
    # ------------------------------------------------------------------

    def _report_error(self, context: str, exc: Exception) -> None:
        """Log the technical detail, show the user something they can act on.

        Every database or credential failure that reaches the UI goes
        through here, so the log always has the exception and the user
        always gets a sentence naming what failed.
        """
        logger.error("%s: %s", context, exc, exc_info=True)
        QMessageBox.critical(self, "CamView", f"{context}.\n\nDetalhe: {exc}")

    def _refresh_device_tree(self) -> None:
        """Reload the sidebar, reporting instead of crashing on a DB error."""
        try:
            self.device_tree.refresh()
        except sqlite3.Error as exc:
            self._report_error("Não foi possível ler os NVRs cadastrados", exc)

    def _vlc_is_available(self) -> bool:
        """Check libVLC once, so a missing install warns once, not per cell."""
        if self._vlc_checked:
            return self._vlc_available
        self._vlc_checked = True
        try:
            get_vlc_instance()
        except VlcUnavailableError as exc:
            self._vlc_available = False
            logger.critical("libVLC unavailable: %s", exc)
            QMessageBox.critical(self, "CamView", str(exc))
        else:
            self._vlc_available = True
        return self._vlc_available

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

        self._refresh_device_tree()
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

        self._refresh_device_tree()
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

        # A standalone camera is one channel by definition; whatever the
        # device reported about channel lists does not apply to it.
        if nvr.is_camera:
            if existing:
                return
            self._camera_repository.create(
                Camera(nvr_id=nvr.id, channel_number=1, name=nvr.name)  # type: ignore[arg-type]
            )
            return

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

        self._refresh_device_tree()
        self.statusBar().showMessage(f"NVR '{nvr.name}' removido.", 5000)

    # ------------------------------------------------------------------
    # Saved layouts
    # ------------------------------------------------------------------

    def _rebuild_layouts_menu(self) -> None:
        """Rebuild the Layouts menu, including one entry per saved layout."""
        menu = self.layouts_menu
        menu.clear()

        new_action = menu.addAction(_icon("document-new"), "&Novo layout")
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.setStatusTip("Esvaziar o mosaico para montar uma composição nova")
        new_action.triggered.connect(self._new_layout)
        menu.addSeparator()

        save_action = menu.addAction(_icon("document-save"), "&Salvar layout")
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_layout)

        save_as_action = menu.addAction(
            _icon("document-save-as"), "Salvar &como..."
        )
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self._save_layout_as)

        manage_action = menu.addAction(
            _icon("view-list-details", "document-open"), "&Gerenciar layouts..."
        )
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

    def _new_layout(self) -> None:
        """Empty the mosaic to start composing from scratch.

        Asks first when there are cameras on screen: the composition may
        be one the user never saved, and closing every stream by accident
        is not something a menu click should be able to do silently.
        """
        if self.video_grid.tiles():
            confirm = QMessageBox.question(
                self,
                "CamView",
                "Fechar todas as câmeras e começar um layout em branco?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self.video_grid.clear()
        self._set_current_layout(None)
        self.statusBar().showMessage(
            "Layout em branco. Monte a composição e salve com Ctrl+Shift+S.", 5000
        )

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
        try:
            layout = self._layout_repository.get(layout_id)
            if layout is None:
                self.statusBar().showMessage("Esse layout não existe mais.", 5000)
                return
            items = self._layout_repository.get_items(layout_id)
            # Resolved up front so a database failure is reported before the
            # mosaic on screen is torn down.
            cameras = {
                item.position: self._camera_repository.get(item.camera_id)
                for item in items
            }
            nvrs = {
                camera.nvr_id: self._nvr_repository.get(camera.nvr_id)
                for camera in cameras.values()
                if camera is not None
            }
        except sqlite3.Error as exc:
            self._report_error("Não foi possível carregar o layout", exc)
            return
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
            camera = cameras.get(item.position)
            if camera is None:
                skipped += 1
                continue
            nvr = nvrs.get(camera.nvr_id)
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

        if not accepted:
            return
        if dialog.blank_requested:
            self._new_layout()
        elif dialog.selected_layout_id is not None:
            self._load_layout(dialog.selected_layout_id)

    def _on_device_tree_item_double_clicked(
        self, item: QTreeWidgetItem, column: int
    ) -> None:
        camera_id = item.data(0, CAMERA_ID_ROLE)
        if camera_id is not None:
            if self._is_standalone_camera(item.data(0, NVR_ID_ROLE)):
                # There is nothing to build a mosaic out of, so a single
                # camera takes the whole window instead of one small cell.
                self._open_camera_fullscreen(camera_id)
                return
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

    # ------------------------------------------------------------------
    # "Is this channel even transmitting?"
    # ------------------------------------------------------------------

    def _check_channel_status(self, nvr_id: int, password: str) -> None:
        """Ask the recorder which channels are online, at most now and then.

        A cell that keeps failing may be pointed at a slot with no camera
        in it. The recorder knows; asking it turns an endless retry loop
        into an honest "sem transmissão" and a slow retry. Throttled per
        device because several cells fail at once.
        """
        now = monotonic()
        last = self._status_checked_at.get(nvr_id, 0.0)
        if now - last < STATUS_QUERY_INTERVAL_S:
            return
        self._status_checked_at[nvr_id] = now

        nvr = self._nvr_repository.get(nvr_id)
        if nvr is None or nvr.is_camera:
            return  # A standalone camera has no channel list to consult.

        worker = _ChannelStatusWorker(nvr_id, nvr.host, nvr.username, password, self)
        worker.finished_with.connect(self._on_channel_status)
        worker.finished.connect(lambda: self._status_workers.discard(worker))
        self._status_workers.add(worker)
        worker.start()

    def _on_channel_status(self, nvr_id: int, status: dict[int, bool]) -> None:
        """Park the cells whose channel the device reports as not transmitting."""
        if not status:
            return  # No information is not the same as "all offline".

        for position, tile in self.video_grid.tiles().items():
            # Anything that is not playing is fair game: by the time the
            # device answers, a failing cell has usually flipped back to
            # "connecting" for its next doomed attempt.
            if tile.camera_id is None or tile.status is ConnectionStatus.PLAYING:
                continue
            camera = self._camera_repository.get(tile.camera_id)
            if camera is None or camera.nvr_id != nvr_id:
                continue

            online = status.get(camera.channel_number)
            if online is True:
                continue
            reason = (
                "O NVR informa que este canal está sem transmissão."
                if online is False
                else "Este canal não existe neste NVR."
            )
            logger.info(
                "Cell %d (%s, channel %d): %s",
                position,
                camera.name,
                camera.channel_number,
                reason,
            )
            tile.mark_channel_unavailable(reason)

    def _is_standalone_camera(self, nvr_id: int | None) -> bool:
        if nvr_id is None:
            return False
        try:
            nvr = self._nvr_repository.get(nvr_id)
        except sqlite3.Error:
            return False
        return nvr is not None and nvr.is_camera

    def _open_camera_fullscreen(self, camera_id: int) -> None:
        """Show one camera on its own, filling the window."""
        self.video_grid.clear()
        self._apply_grid_shape(1, 1)
        self._set_current_layout(None)
        self._open_camera_at(camera_id, 0)

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
            playback_options=playback_options_for(self._settings),
            reconnect_enabled=self._settings.reconnect_enabled,
            backoff_schedule=self._settings.backoff_schedule(),
        )
        tile.repeatedFailures.connect(
            partial(self._check_channel_status, nvr.id, password)
        )
        self.video_grid.place_tile(position, tile)

    def _open_camera_at(self, camera_id: int, position: int) -> None:
        """Build the RTSP URL for ``camera_id`` and open it in cell ``position``."""
        if not self._vlc_is_available():
            return
        try:
            camera = self._camera_repository.get(camera_id)
            nvr = None if camera is None else self._nvr_repository.get(camera.nvr_id)
        except sqlite3.Error as exc:
            self._report_error("Não foi possível ler os dados da câmera", exc)
            return
        if camera is None or nvr is None:
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
        if not self._vlc_is_available():
            return
        try:
            nvr = self._nvr_repository.get(nvr_id)
            if nvr is None:
                return
            cameras = [
                camera
                for camera in self._camera_repository.list_by_nvr(nvr_id)
                if camera.enabled
            ]
        except sqlite3.Error as exc:
            self._report_error("Não foi possível ler as câmeras do NVR", exc)
            return
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
        """Which stream a newly opened mosaic cell should use.

        Defaults to the substream: 16 simultaneous main streams would be
        both needless bandwidth and far more decoding than a cell-sized
        viewport can show. A 1x1 grid is effectively single-camera view, so
        there the NVR's own default is honoured.

        Both are overridable — globally in the settings dialog (some NVRs
        ship 10 fps substreams, which look choppy) and per cell from its
        right-click menu, which is what a saved layout records.
        """
        if self.video_grid.cell_count == 1:
            return nvr_default
        return self._settings.mosaic_stream.resolve(nvr_default)

    def _on_camera_dropped(self, camera_id: int, position: int) -> None:
        self._open_camera_at(camera_id, position)

    def _on_grid_shape_changed(self, label: str) -> None:
        shape = GRID_SHAPES.get(label)
        if shape is None:
            return
        self.video_grid.set_grid_shape(*shape)
        self.statusBar().showMessage(f"Mosaico {label}.", 3000)
