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
from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from camview.services.reconnect import ReconnectBackoff
from camview.services.stream_manager import (
    PlaybackOptions,
    VlcUnavailableError,
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


class VideoTile(QWidget):
    """A single mosaic cell: video area, header (status/name/close), auto-reconnect."""

    closeRequested = Signal()

    _playingSignal = Signal()
    _errorSignal = Signal(str)
    _endedSignal = Signal()

    def __init__(
        self,
        title: str,
        url: str,
        playback_options: PlaybackOptions | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.url = url
        self._playback_options = playback_options or PlaybackOptions()
        self._backoff = ReconnectBackoff()
        self._player: vlc.MediaPlayer | None = None
        self.status = ConnectionStatus.CONNECTING

        self._build_ui()

        self._playingSignal.connect(self._on_playing)
        self._errorSignal.connect(self._on_error)
        self._endedSignal.connect(self._on_ended)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._connect)

        # Deferred so the video widget is realized/mapped on screen before
        # libVLC is asked to embed into its window id.
        QTimer.singleShot(0, self._connect)

    def _build_ui(self) -> None:
        self._status_dot = QLabel()
        self._status_dot.setFixedSize(10, 10)

        name_label = QLabel(self.title)
        name_label.setStyleSheet("font-weight: 600;")

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
        header_layout.setContentsMargins(6, 4, 4, 4)
        header_layout.addWidget(self._status_dot)
        header_layout.addWidget(name_label)
        header_layout.addStretch()
        header_layout.addWidget(close_button)

        self.video_widget = QWidget()
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
        self._stack.addWidget(self.video_widget)
        self._stack.addWidget(self._message_label)

        stack_container = QWidget()
        stack_container.setLayout(self._stack)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        outer.addWidget(stack_container, stretch=1)

        self._set_status(ConnectionStatus.CONNECTING, "Conectando...")

    def _set_status(self, status: ConnectionStatus, message: str = "") -> None:
        self.status = status
        self._status_dot.setStyleSheet(
            f"background-color: {_STATUS_COLORS[status]}; border-radius: 5px;"
        )
        if status == ConnectionStatus.PLAYING:
            self._message_label.hide()
            self._stack.setCurrentWidget(self.video_widget)
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
            self._player.set_xwindow(int(self.video_widget.winId()))

        media = instance.media_new(self.url, *self._playback_options.to_media_options())
        self._player.set_media(media)
        self._player.play()

    def _handle_vlc_playing(self, event: object) -> None:
        self._playingSignal.emit()

    def _handle_vlc_error(self, event: object) -> None:
        self._errorSignal.emit("Falha ao reproduzir o stream.")

    def _handle_vlc_ended(self, event: object) -> None:
        self._endedSignal.emit()

    def _on_playing(self) -> None:
        self._backoff.reset()
        self._set_status(ConnectionStatus.PLAYING)

    def _on_error(self, message: str) -> None:
        self._schedule_reconnect(message)

    def _on_ended(self) -> None:
        self._schedule_reconnect("Stream encerrado pelo dispositivo.")

    def _schedule_reconnect(self, message: str) -> None:
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

    def close_stream(self) -> None:
        """Stop and release the player.

        Callers must invoke this before discarding a tile (e.g. removing it
        from a grid, or replacing it as the central widget) — Qt does not
        call ``closeEvent`` for non-top-level widgets, so this cleanup
        cannot happen automatically.
        """
        self._reconnect_timer.stop()
        if self._player is not None:
            self._player.stop()
            self._player.release()
            self._player = None
