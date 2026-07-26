"""Background workers for the device queries the UI makes.

Every one of these talks to a recorder over HTTP, which must never happen
on the GUI thread: an unreachable device would freeze the whole wall for
the length of a timeout.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from camview.services.connectivity import check_tcp_connection
from camview.services.hikvision import (
    DiscoveryError,
    channel_online_status,
    discover_channels,
)
from camview.services.updates import find_update

logger = logging.getLogger(__name__)

#: Budget for a channel-status query; also how long closing waits for one.
STATUS_QUERY_TIMEOUT_S = 2.5


class ConnectionTestWorker(QThread):
    """Runs the TCP reachability check off the GUI thread."""

    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, host: str, port: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._port = port

    def run(self) -> None:
        try:
            check_tcp_connection(self._host, self._port)
        except OSError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()


class ChannelDiscoveryWorker(QThread):
    """Asks a device which channels it has and what they are called."""

    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        nvr_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._username = username
        self._password = password
        #: Carried through so a caller handling several devices can tell
        #: which one answered.
        self.nvr_id = nvr_id

    def run(self) -> None:
        try:
            channels = discover_channels(self._host, self._username, self._password)
        except DiscoveryError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(channels)


class UpdateCheckWorker(QThread):
    """Looks for a newer release without holding up the window."""

    #: ``object`` so ``None`` (no update) crosses the thread unchanged.
    finished_with = Signal(object)

    def __init__(self, current_version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_version = current_version

    def run(self) -> None:
        try:
            release = find_update(self._current_version)
        except Exception as exc:  # noqa: BLE001 - never break startup over this
            logger.info("Update check failed: %s", exc)
            release = None
        self.finished_with.emit(release)


class ChannelStatusWorker(QThread):
    """Asks a recorder which of its channels are online."""

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
