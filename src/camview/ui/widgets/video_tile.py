"""VideoTile — a single mosaic cell: one libVLC player, embedded and auto-reconnecting.

libVLC event callbacks (``MediaPlayerPlaying`` etc.) run on a libvlc-internal
thread, never the Qt main thread. Those callbacks only ever do one thing —
``emit()`` a Qt signal — which is safe to call from any thread: Qt detects
the receiver (this widget) lives on a different thread and automatically
queues the slot invocation there. All actual state changes happen in the
connected slots, on the GUI thread.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import Enum
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QActionGroup,
    QContextMenuEvent,
    QResizeEvent,
    QDrag,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedLayout,
    QStyle,
    QStyleOption,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from camview.models.camera import StreamType
from camview.services.reconnect import DEFAULT_BACKOFF_SCHEDULE_S, ReconnectBackoff
from camview.services.stream_manager import (
    PlaybackOptions,
    VlcUnavailableError,
    displayed_picture_count,
    get_vlc_instance,
)

if TYPE_CHECKING:
    import vlc

logger = logging.getLogger(__name__)


class ConnectionStatus(Enum):
    CONNECTING = "connecting"
    PLAYING = "playing"
    ERROR = "error"


_STATUS_COLORS: dict[ConnectionStatus, str] = {
    ConnectionStatus.CONNECTING: "#d69e2e",
    ConnectionStatus.PLAYING: "#38a169",
    ConnectionStatus.ERROR: "#e53e3e",
}

#: Mime type used when dragging an already-placed tile to another grid cell.
GRID_POSITION_MIME_TYPE = "application/x-camview-grid-position"

#: How often to ask libVLC whether new frames reached the screen.
STALL_CHECK_INTERVAL_MS = 2000
#: Seconds without a single new frame before a cell counts as stalled.
#: Generous on purpose: NVR substreams are sometimes configured as low as
#: 1 fps, and a slow cell must never be mistaken for a dead one.
STALL_TIMEOUT_S = 10.0
#: How long a stream must keep playing before its reconnect backoff resets.
#: Without this, a cell that stalls every few seconds would reconnect at
#: the shortest delay forever instead of backing off.
HEALTHY_PLAYBACK_S = 30.0

#: Consecutive failures before the cell stops blaming the network and
#: starts pointing at credentials.
CREDENTIAL_HINT_AFTER_FAILURES = 5

#: Consecutive failures before asking the device whether this channel is
#: even transmitting. Deliberately early: the answer costs one read-only
#: HTTP request and can stop a pointless retry loop.
STATUS_CHECK_AFTER_FAILURES = 2

#: How long to wait before retrying a channel the device itself reports as
#: offline. Long, but never "never": cameras come back.
OFFLINE_RETRY_S = 300

#: Digital zoom limits. 1.0 is the whole picture; 8x is where a 640x360
#: substream is already a handful of pixels.
MIN_ZOOM = 1.0
MAX_ZOOM = 8.0
#: Multiplier per wheel notch (or per zoom-in shortcut press).
ZOOM_STEP = 1.25

#: Shown once a cell has failed repeatedly without ever playing. Wrong
#: credentials look exactly like an unreachable device from libVLC's side,
#: and Hikvision NVRs lock out the source IP after enough failed logins —
#: so the guess worth surfacing is the one with consequences.
CREDENTIAL_HINT = (
    "Verifique usuário e senha deste NVR.\n"
    "Tentativas repetidas com senha errada podem bloquear o acesso "
    "deste computador no equipamento."
)


class _VideoArea(QWidget):
    """Clipping frame for the video widget.

    Exists only so the video widget can be made larger than what is on
    screen (that is the zoom) while the parts outside stay hidden, and so
    the tile is told when the visible size changes.
    """

    def __init__(self, tile: "VideoTile") -> None:
        super().__init__()
        self._tile = tile
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), Qt.GlobalColor.black)
        self.setPalette(palette)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._tile._apply_zoom()


class VideoTile(QWidget):
    """A single mosaic cell: video area, header (status/name/close), auto-reconnect."""

    closeRequested = Signal()
    doubleClicked = Signal()
    clicked = Signal()
    #: The user picked a stream for this cell from its context menu.
    streamTypeRequested = Signal(object)
    #: This cell has failed enough times to be worth asking the device
    #: whether the channel is transmitting at all.
    repeatedFailures = Signal()

    _playingSignal = Signal()
    _errorSignal = Signal(str)
    _endedSignal = Signal()

    def __init__(
        self,
        title: str,
        stream_urls: Mapping[StreamType, str],
        stream_type: StreamType = StreamType.SUB,
        playback_options: PlaybackOptions | None = None,
        camera_id: int | None = None,
        reconnect_enabled: bool = True,
        backoff_schedule: tuple[int, ...] = DEFAULT_BACKOFF_SCHEDULE_S,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if stream_type not in stream_urls:
            raise ValueError(f"no URL provided for stream type {stream_type}")
        self.title = title
        self._stream_urls = dict(stream_urls)
        self.stream_type = stream_type
        self.camera_id = camera_id
        #: Set by VideoGrid when the tile is placed; ``None`` when unplaced.
        self.grid_position: int | None = None
        self._playback_options = playback_options or PlaybackOptions()
        self._reconnect_enabled = reconnect_enabled
        self._backoff = ReconnectBackoff(backoff_schedule)
        #: Failures since the stream last played, for the credential hint.
        self._consecutive_failures = 0
        #: Parked: the device says this channel has no signal, so the cell
        #: waits on a long retry instead of the usual backoff.
        self._parked = False
        #: Digital zoom: how many times larger the video widget is than
        #: its visible area, and where its top-left sits inside it.
        self._zoom = MIN_ZOOM
        self._offset = (0.0, 0.0)
        self._player: vlc.MediaPlayer | None = None
        self.status = ConnectionStatus.CONNECTING
        self._selected = False
        self._drag_origin: QPoint | None = None

        self._build_ui()

        self._playingSignal.connect(self._on_playing)
        self._errorSignal.connect(self._on_error)
        self._endedSignal.connect(self._on_ended)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._connect)

        # Watchdog: a stalled RTSP stream stays "playing" and reports no
        # error — the picture just stops. Only the frame counter reveals it.
        self._last_picture_count: int | None = None
        self._last_progress_at: float = monotonic()
        self._stall_timer = QTimer(self)
        self._stall_timer.setInterval(STALL_CHECK_INTERVAL_MS)
        self._stall_timer.timeout.connect(self._check_for_stall)

        self._healthy_timer = QTimer(self)
        self._healthy_timer.setSingleShot(True)
        self._healthy_timer.timeout.connect(self._backoff.reset)

        # Deferred so the video widget is realized/mapped on screen before
        # libVLC is asked to embed into its window id. Owned by the tile
        # rather than fired by QTimer.singleShot: a cell closed before this
        # runs would otherwise still open a stream, leaving a player nobody
        # holds a reference to.
        self._initial_connect_timer = QTimer(self)
        self._initial_connect_timer.setSingleShot(True)
        self._initial_connect_timer.timeout.connect(self._connect)
        self._initial_connect_timer.start(0)

    def _build_ui(self) -> None:
        self._status_dot = QLabel()
        self._status_dot.setFixedSize(10, 10)

        name_label = QLabel(self.title)
        name_label.setStyleSheet("font-weight: 600;")
        name_label.setToolTip(self.title)

        # Which stream a cell is on is not obvious from the picture, and it
        # is exactly what a user asks about when one cell looks choppier
        # than the rest.
        self._stream_badge = QLabel()
        self._stream_badge.setStyleSheet(
            "color: palette(mid); font-size: 10px; letter-spacing: 1px;"
        )
        self._update_stream_badge()

        close_button = QToolButton()
        close_icon = QIcon.fromTheme("window-close")
        if close_icon.isNull():
            close_button.setText("×")
        else:
            close_button.setIcon(close_icon)
        close_button.setAutoRaise(True)
        close_button.setToolTip("Fechar")
        close_button.clicked.connect(lambda: self.closeRequested.emit())
        self._close_button = close_button

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 4, 4)
        header_layout.setSpacing(8)
        header_layout.addWidget(self._status_dot)
        header_layout.addWidget(name_label)
        header_layout.addStretch()
        header_layout.addWidget(self._stream_badge)
        header_layout.addWidget(close_button)

        # The video area is a plain container with no layout: the video
        # widget's geometry is set by hand so zooming can enlarge and shift
        # it, with the container clipping whatever falls outside. Doing the
        # zoom this way keeps it exact — libVLC's own crop filter stretched
        # the picture and ignored where the pointer actually was.
        self._video_area = _VideoArea(self)
        self.video_widget = QWidget(self._video_area)
        self.video_widget.setAutoFillBackground(True)
        palette = self.video_widget.palette()
        palette.setColor(self.video_widget.backgroundRole(), Qt.GlobalColor.black)
        self.video_widget.setPalette(palette)

        self._message_label = QLabel("")
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.setStyleSheet("color: white; background-color: black;")

        self._stack = QStackedLayout()
        # StackAll keeps every page mapped on screen instead of hiding all but
        # the current one. This matters: libVLC renders into the video
        # widget's X11 window, and creating that video output fails outright
        # if the window is unmapped — which is exactly the state a plain
        # QStackedLayout would leave it in while the "Conectando..." message
        # is showing. The message label is opaque, so it still visually
        # covers the video when it is the current (topmost) page.
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._stack.addWidget(self._video_area)
        self._stack.addWidget(self._message_label)

        stack_container = QWidget()
        stack_container.setLayout(self._stack)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        outer.addWidget(stack_container, stretch=1)

        self.set_selected(False)
        self._set_status(ConnectionStatus.CONNECTING, "Conectando...")

    def _update_stream_badge(self) -> None:
        label = "PRINCIPAL" if self.stream_type is StreamType.MAIN else "SUB"
        self._stream_badge.setText(label)
        self._stream_badge.setToolTip(
            "Stream principal (mais nitidez)"
            if self.stream_type is StreamType.MAIN
            else "Substream (menos banda e CPU) — botão direito para trocar"
        )

    def _set_status(self, status: ConnectionStatus, message: str = "") -> None:
        self.status = status
        self._status_dot.setStyleSheet(
            f"background-color: {_STATUS_COLORS[status]}; border-radius: 5px;"
        )
        if status == ConnectionStatus.PLAYING:
            self._message_label.hide()
            self._stack.setCurrentWidget(self._video_area)
        else:
            self._message_label.setText(message)
            self._message_label.show()
            self._stack.setCurrentWidget(self._message_label)

    def _connect(self) -> None:
        self._set_status(ConnectionStatus.CONNECTING, "Conectando...")
        try:
            instance = get_vlc_instance()
        except VlcUnavailableError as exc:
            logger.error("VLC unavailable for tile '%s': %s", self.title, exc)
            self._set_status(ConnectionStatus.ERROR, str(exc))
            return

        import vlc  # already loaded by get_vlc_instance(); cheap local bind

        if self._player is None:
            self._player = instance.media_player_new()
            event_manager = self._player.event_manager()
            event_manager.event_attach(
                vlc.EventType.MediaPlayerPlaying, self._handle_vlc_playing
            )
            event_manager.event_attach(
                vlc.EventType.MediaPlayerEncounteredError, self._handle_vlc_error
            )
            event_manager.event_attach(
                vlc.EventType.MediaPlayerEndReached, self._handle_vlc_ended
            )
            # libVLC otherwise grabs mouse/keyboard on its own video window,
            # swallowing the events Qt needs for double-click-to-maximize,
            # drag-to-reposition and Esc. Turning both off lets them reach us.
            self._player.video_set_mouse_input(False)
            self._player.video_set_key_input(False)
            self._player.set_xwindow(int(self.video_widget.winId()))

        self._parked = False
        media = instance.media_new(self.url, *self._playback_options.to_media_options())
        self._player.set_media(media)
        # The player holds its own reference now, so drop ours. Without this
        # every reconnect leaks a libvlc media object — invisible in one
        # session, but a cell retrying every 30s does it ~2900 times a day.
        release = getattr(media, "release", None)
        if callable(release):
            release()
        self._player.play()
        # New media, new counters: the watchdog's clock restarts here, so the
        # connection attempt itself gets the full grace period.
        self._last_picture_count = None
        self._last_progress_at = monotonic()

    def _handle_vlc_playing(self, event: object) -> None:
        self._playingSignal.emit()

    def _handle_vlc_error(self, event: object) -> None:
        self._errorSignal.emit("Falha ao reproduzir o stream.")

    def _handle_vlc_ended(self, event: object) -> None:
        self._endedSignal.emit()

    def _on_playing(self) -> None:
        self._set_status(ConnectionStatus.PLAYING)
        self._consecutive_failures = 0
        # A stream switch can resize the video widget under us; put the
        # zoom geometry back the way the user left it.
        self._apply_zoom()
        self._last_picture_count = None
        self._last_progress_at = monotonic()
        self._stall_timer.start()
        # Only a stream that keeps playing counts as a recovered one.
        self._healthy_timer.start(int(HEALTHY_PLAYBACK_S * 1000))

    def _check_for_stall(self) -> None:
        """Reconnect a cell whose picture stopped updating.

        libVLC reports no error for this: the player stays in the playing
        state while the NVR quietly stops sending. Users saw it as "some
        cells freeze and only come back if I open that channel" — opening
        it maximized happened to force a reconnect, which is exactly what
        this does automatically.
        """
        if self.status != ConnectionStatus.PLAYING:
            return
        # A hidden tile (another cell is maximized) may legitimately stop
        # rendering; judging it stalled would reconnect the whole mosaic.
        if not self.isVisible():
            self._last_progress_at = monotonic()
            return

        count = displayed_picture_count(self._player)
        if count is None:
            return  # Unknown is not the same as stalled.

        if self._last_picture_count is None or count > self._last_picture_count:
            self._last_picture_count = count
            self._last_progress_at = monotonic()
            return

        if monotonic() - self._last_progress_at < STALL_TIMEOUT_S:
            return

        logger.warning(
            "Tile '%s' stalled: no new frames for %.0fs, reconnecting",
            self.title,
            STALL_TIMEOUT_S,
        )
        self._schedule_reconnect("Stream travado (sem novos quadros).")

    def _on_error(self, message: str) -> None:
        self._schedule_reconnect(message)

    def _on_ended(self) -> None:
        self._schedule_reconnect("Stream encerrado pelo dispositivo.")

    def _schedule_reconnect(self, message: str) -> None:
        self._stall_timer.stop()
        self._healthy_timer.stop()

        if self._parked:
            # A failure still in flight when the cell was parked must not
            # drag it back to the fast retry loop — the device already said
            # there is nothing to receive.
            logger.debug("Tile '%s' is parked; ignoring '%s'", self.title, message)
            return

        self._consecutive_failures += 1
        # Every failure past the threshold, not just the one that crosses
        # it: the first query may be throttled or come back empty, and a
        # channel that stays dark deserves another ask.
        if self._consecutive_failures >= STATUS_CHECK_AFTER_FAILURES:
            self.repeatedFailures.emit()
        if self._consecutive_failures >= CREDENTIAL_HINT_AFTER_FAILURES:
            message = f"{message}\n\n{CREDENTIAL_HINT}"

        if not self._reconnect_enabled:
            logger.warning(
                "Tile '%s' disconnected (%s); automatic reconnect is off",
                self.title,
                message,
            )
            self._set_status(ConnectionStatus.ERROR, message)
            return

        delay = self._backoff.next_delay_seconds()
        logger.warning(
            "Tile '%s' disconnected (%s); reconnecting in %ds",
            self.title,
            message,
            delay,
        )
        self._set_status(
            ConnectionStatus.ERROR, f"{message}\nReconectando em {delay}s..."
        )
        self._reconnect_timer.start(delay * 1000)

    @property
    def is_parked(self) -> bool:
        """Waiting on the long retry because the device reported no signal."""
        return self._parked

    def mark_channel_unavailable(self, reason: str) -> None:
        """Stop hammering a channel the device says is not transmitting.

        Retrying every few seconds cannot help — the recorder has no
        picture to give — so the cell says why and tries again in
        :data:`OFFLINE_RETRY_S`, which is what makes it come back on its
        own once the camera is repaired.
        """
        logger.info("Tile '%s' parked: %s", self.title, reason)
        self._parked = True
        self._stall_timer.stop()
        self._healthy_timer.stop()
        self._set_status(
            ConnectionStatus.ERROR,
            f"{reason}\n\nNova tentativa em {OFFLINE_RETRY_S // 60} min.",
        )
        self._reconnect_timer.start(OFFLINE_RETRY_S * 1000)

    @property
    def url(self) -> str:
        """RTSP URL currently being played."""
        return self._stream_urls[self.stream_type]

    def set_stream_type(self, stream_type: StreamType) -> None:
        """Switch between main and sub stream, restarting playback.

        Used when a cell is maximized: the substream that looks fine in a
        small mosaic cell is visibly soft filling the window, so the tile
        moves up to the main stream and back down again on restore.

        Silently ignores stream types this tile has no URL for.
        """
        if stream_type == self.stream_type or stream_type not in self._stream_urls:
            return

        logger.debug(
            "Tile '%s' switching from %s to %s stream",
            self.title,
            self.stream_type.value,
            stream_type.value,
        )
        self.stream_type = stream_type
        self._update_stream_badge()
        self._backoff.reset()
        self._reconnect_timer.stop()
        self._connect()

    # ------------------------------------------------------------------
    # Digital zoom
    # ------------------------------------------------------------------

    @property
    def zoom(self) -> float:
        return self._zoom

    def zoom_by(self, factor: float, anchor: QPoint | None = None) -> None:
        """Multiply the zoom, keeping ``anchor`` pinned where it already is.

        ``anchor`` is a point in the visible video area. Pinning it is what
        makes the wheel feel right — whatever is under the pointer stays
        under the pointer — and it is exact here because the zoom is pure
        widget geometry, with no picture coordinates to guess at.
        """
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * factor))
        if new_zoom == self._zoom:
            return

        area = self._video_area.size()
        if anchor is not None and area.width() and area.height():
            current = self.video_widget.geometry()
            # Where the anchor sits within the (scaled) video widget.
            u = (anchor.x() - current.x()) / max(1, current.width())
            v = (anchor.y() - current.y()) / max(1, current.height())
            self._offset = (
                anchor.x() - u * area.width() * new_zoom,
                anchor.y() - v * area.height() * new_zoom,
            )
        else:
            # No anchor: keep the middle of the view where it is.
            scale = new_zoom / self._zoom
            self._offset = (
                area.width() / 2 - (area.width() / 2 - self._offset[0]) * scale,
                area.height() / 2 - (area.height() / 2 - self._offset[1]) * scale,
            )

        self._zoom = new_zoom
        self._apply_zoom()

    def pan_by(self, dx: int, dy: int) -> None:
        """Drag the zoomed picture around. No effect at 1x — nothing hidden."""
        if self._zoom <= MIN_ZOOM:
            return
        self._offset = (self._offset[0] + dx, self._offset[1] + dy)
        self._apply_zoom()

    def reset_zoom(self) -> None:
        if self._zoom == MIN_ZOOM and self._offset == (0.0, 0.0):
            return
        self._zoom = MIN_ZOOM
        self._offset = (0.0, 0.0)
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        """Resize/move the video widget inside its clipping area.

        At 1x it fills the area exactly. Zoomed in, it is that many times
        larger and shifted, so the area shows one part of it — the offset
        is clamped so the picture can never be dragged off the edge.
        """
        area = self._video_area.size()
        if not area.width() or not area.height():
            return

        width = int(area.width() * self._zoom)
        height = int(area.height() * self._zoom)
        left = min(0.0, max(float(area.width() - width), self._offset[0]))
        top = min(0.0, max(float(area.height() - height), self._offset[1]))
        self._offset = (left, top)
        self.video_widget.setGeometry(int(left), int(top), width, height)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Wheel over the picture zooms in and out, pinned to the cursor."""
        steps = event.angleDelta().y() / 120.0
        if not steps:
            super().wheelEvent(event)
            return

        anchor = self._video_area.mapFrom(self, event.position().toPoint())
        self.zoom_by(ZOOM_STEP**steps, anchor)
        event.accept()

    def set_selected(self, selected: bool) -> None:
        """Draw (or clear) the discreet border marking the focused tile.

        The unselected state keeps a transparent border of the same width so
        selecting a tile never shifts the layout by a pixel.
        """
        self._selected = selected
        border = "1px solid palette(highlight)" if selected else "1px solid transparent"
        self.setStyleSheet(f"VideoTile {{ border: {border}; }}")

    def is_selected(self) -> bool:
        return self._selected

    def paintEvent(self, event: QPaintEvent) -> None:
        # Qt stylesheets are ignored on plain QWidget subclasses unless the
        # widget explicitly paints itself through the style system. Without
        # this, set_selected()'s border would never appear.
        option = QStyleOption()
        option.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_Widget, option, painter, self
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self.clicked.emit()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.build_context_menu().exec(event.globalPos())
        event.accept()

    def build_context_menu(self) -> QMenu:
        """Right-click menu for picking this cell's stream.

        Per cell rather than global because the reason to switch is
        per camera: many NVRs ship substreams at 10 fps against 25 on the
        main stream, so one choppy camera shouldn't force every other cell
        onto a full-resolution stream.

        Built separately from showing it, so its contents can be inspected
        without opening a modal that would hang a test run.
        """
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)

        for stream_type, label in (
            (StreamType.MAIN, "Stream principal"),
            (StreamType.SUB, "Substream"),
        ):
            if stream_type not in self._stream_urls:
                continue
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(stream_type == self.stream_type)
            action.triggered.connect(
                partial(self.streamTypeRequested.emit, stream_type)
            )
            group.addAction(action)

        menu.addSeparator()
        close_action = menu.addAction("Fechar célula")
        close_action.triggered.connect(self.closeRequested.emit)
        return menu

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is None or not (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            super().mouseMoveEvent(event)
            return

        if self._zoom > MIN_ZOOM:
            # Zoomed in, dragging moves the picture rather than the cell:
            # there is hidden image to reach, and the cell can still be
            # moved from the mosaic view.
            delta = event.position().toPoint() - self._drag_origin
            self._drag_origin = event.position().toPoint()
            self.pan_by(delta.x(), delta.y())
            event.accept()
            return

        if self.grid_position is None:
            super().mouseMoveEvent(event)
            return

        distance = (event.position().toPoint() - self._drag_origin).manhattanLength()
        if distance < QApplication.startDragDistance():
            return

        mime = QMimeData()
        mime.setData(
            GRID_POSITION_MIME_TYPE, str(self.grid_position).encode("ascii")
        )
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_origin = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)

    def close_stream(self) -> None:
        """Stop and release the player.

        Callers must invoke this before discarding a tile (e.g. removing it
        from a grid, or replacing it as the central widget) — Qt does not
        call ``closeEvent`` for non-top-level widgets, so this cleanup
        cannot happen automatically.
        """
        self._initial_connect_timer.stop()
        self._reconnect_timer.stop()
        self._stall_timer.stop()
        self._healthy_timer.stop()
        if self._player is not None:
            self._player.stop()
            self._player.release()
            self._player = None
