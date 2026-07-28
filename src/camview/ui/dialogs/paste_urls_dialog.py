"""Register cameras by pasting their RTSP URLs.

The device dialog asks for host, user, password and channel count, which
only makes sense for a recorder. A standalone camera from another maker
is usually documented as a single URL — often a list of them, one per
camera — and typing each one back into a form field by field is busywork.

Passwords parsed here go straight to the keyring; the URL itself is never
stored.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from camview.services.rtsp import ParsedStream, parse_rtsp_urls

logger = logging.getLogger(__name__)

PLACEHOLDER = (
    "rtsp://usuario:senha@192.168.0.10:554/live/main\n"
    "rtsp://usuario:senha@192.168.0.11:554/live/main\n"
    "..."
)


class PasteUrlsDialog(QDialog):
    """Paste a block of RTSP URLs; read the result from :meth:`result_streams`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Adicionar câmeras por URL")
        self.resize(720, 520)
        self._streams: list[ParsedStream] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(
            "Cole uma URL RTSP por linha. Linhas que não forem URLs são "
            "ignoradas, e cada câmera vira um dispositivo separado."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(PLACEHOLDER)
        self.text_edit.textChanged.connect(self._refresh_preview)
        root.addWidget(self.text_edit, stretch=1)

        self.summary_label = QLabel("Nenhuma URL reconhecida ainda.")
        self.summary_label.setStyleSheet("color: palette(mid);")
        root.addWidget(self.summary_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Nome", "Endereço", "Stream principal", "Substream"]
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, stretch=1)

        hint = QLabel(
            "O nome e o substream podem ser editados na tabela. O substream "
            "é um palpite a partir do caminho principal — apague se a câmera "
            "não tiver um."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")
        root.addWidget(hint)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        root.addWidget(self.buttons)

    def _refresh_preview(self) -> None:
        self._streams = parse_rtsp_urls(self.text_edit.toPlainText())
        self.table.setRowCount(len(self._streams))

        for row, stream in enumerate(self._streams):
            editable = QTableWidgetItem(stream.suggested_name)
            self.table.setItem(row, 0, editable)

            address = QTableWidgetItem(f"{stream.host}:{stream.port}")
            address.setFlags(address.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, address)

            main = QTableWidgetItem(stream.path)
            main.setFlags(main.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, main)

            self.table.setItem(row, 3, QTableWidgetItem(stream.suggested_sub_path))

        found = len(self._streams)
        without_password = sum(1 for s in self._streams if not s.password)
        message = f"{found} câmera(s) reconhecida(s)."
        if without_password:
            # The app refuses to open a stream with no stored password, so
            # say it here rather than after everything is registered.
            message += (
                f" {without_password} sem senha na URL — essas não abrirão "
                "até você informar a senha."
            )
        self.summary_label.setText(
            message if found else "Nenhuma URL reconhecida ainda."
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(found > 0)

    def result_streams(self) -> list[tuple[ParsedStream, str, str]]:
        """``(stream, name, sub_path)`` per row, with the table's edits applied."""
        results: list[tuple[ParsedStream, str, str]] = []
        for row, stream in enumerate(self._streams):
            name_item = self.table.item(row, 0)
            sub_item = self.table.item(row, 3)
            name = (name_item.text().strip() if name_item else "") or (
                stream.suggested_name
            )
            sub_path = sub_item.text().strip() if sub_item else ""
            results.append((stream, name, sub_path))
        return results
