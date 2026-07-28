"""Dialog for managing every registered device in one place.

Until this existed, devices were managed one at a time and from two
different places: the NVR menu to add, a right-click on the sidebar tree
to edit, sync or remove. Removing ten cameras meant ten right-clicks and
ten confirmations.

This dialog is the single place instead: a table of everything
registered, with check boxes, so add/edit/sync/remove all start here and
removal can take a whole batch at once.

Deleting is applied to the database from inside the dialog — it is a
self-contained edit. Adding, editing and syncing are not: they open
further dialogs or hit the network off the GUI thread, both of which
``MainWindow`` already knows how to do, so the dialog only *asks* via
signals and lets the window answer.
"""

from __future__ import annotations

import logging
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from camview.database.repositories import CameraRepository, NvrRepository
from camview.models.nvr import Nvr
from camview.services.credentials import (
    CredentialsError,
    delete_nvr_password,
    get_nvr_password,
)

logger = logging.getLogger(__name__)

NVR_ID_ROLE = Qt.ItemDataRole.UserRole
IS_CAMERA_ROLE = Qt.ItemDataRole.UserRole + 1

COLUMN_CHECK = 0
COLUMN_NAME = 1
COLUMN_ADDRESS = 2
COLUMN_KIND = 3
COLUMN_CHANNELS = 4
COLUMN_PASSWORD = 5

#: How many names a delete confirmation spells out before summarising.
MAX_NAMES_IN_CONFIRMATION = 10


class DeviceManagerDialog(QDialog):
    """Table of registered devices with batch selection and actions."""

    #: The user asked to register a new NVR.
    addNvrRequested = Signal()
    #: The user asked to register cameras by pasting RTSP URLs.
    pasteUrlsRequested = Signal()
    #: Edit this device (exactly one is checked).
    editRequested = Signal(int)
    #: Ask this device for its channels and names.
    syncRequested = Signal(int)
    #: Devices were removed here; the sidebar tree is now stale.
    devicesChanged = Signal()

    def __init__(
        self,
        nvr_repository: NvrRepository,
        camera_repository: CameraRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gerenciar dispositivos")
        self.resize(760, 460)
        self._nvr_repository = nvr_repository
        self._camera_repository = camera_repository

        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.add_nvr_button = QPushButton("Adicionar NVR...")
        self.add_nvr_button.clicked.connect(self.addNvrRequested.emit)
        self.paste_button = QPushButton("Adicionar por URL...")
        self.paste_button.setToolTip(
            "Colar uma lista de URLs RTSP, uma por linha"
        )
        self.paste_button.clicked.connect(self.pasteUrlsRequested.emit)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar por nome ou endereço...")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)

        top.addWidget(self.add_nvr_button)
        top.addWidget(self.paste_button)
        top.addStretch(1)
        top.addWidget(self.filter_edit, stretch=1)
        root.addLayout(top)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "Nome", "Endereço", "Tipo", "Canais", "Senha"]
        )
        self.table.verticalHeader().setVisible(False)
        # Rows are picked with the check boxes, so Qt's own selection would
        # be a second, conflicting notion of "what is chosen".
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COLUMN_CHECK, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COLUMN_CHECK, 32)
        header.setSectionResizeMode(COLUMN_NAME, QHeaderView.ResizeMode.Stretch)
        for column in (
            COLUMN_ADDRESS,
            COLUMN_KIND,
            COLUMN_CHANNELS,
            COLUMN_PASSWORD,
        ):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        root.addWidget(self.table, stretch=1)

        bottom = QHBoxLayout()
        self.check_all_button = QPushButton("Marcar todos")
        self.check_all_button.clicked.connect(self._toggle_all)
        self.summary_label = QLabel()
        self.edit_button = QPushButton("Editar...")
        self.edit_button.clicked.connect(self._edit_checked)
        self.sync_button = QPushButton("Atualizar canais")
        self.sync_button.setToolTip(
            "Pergunta ao equipamento quais canais existem e como se chamam"
        )
        self.sync_button.clicked.connect(self._sync_checked)
        self.delete_button = QPushButton("Excluir")
        self.delete_button.clicked.connect(self._delete_checked)
        close_button = QPushButton("Fechar")
        close_button.clicked.connect(self.reject)

        bottom.addWidget(self.check_all_button)
        bottom.addWidget(self.summary_label)
        bottom.addStretch(1)
        bottom.addWidget(self.edit_button)
        bottom.addWidget(self.sync_button)
        bottom.addWidget(self.delete_button)
        bottom.addWidget(close_button)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    # Contents
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload every device from the database, keeping what was checked.

        Checked ids survive because the actions that trigger a reload —
        syncing, editing — are ones the user is likely to repeat on the
        same batch.
        """
        checked_before = set(self.checked_ids())

        try:
            devices = self._nvr_repository.list_all()
        except sqlite3.Error as exc:
            logger.error("Could not list devices: %s", exc)
            QMessageBox.critical(
                self, "CamView", f"Não foi possível ler os dispositivos.\n\n{exc}"
            )
            return

        # Repopulating fires itemChanged once per cell; letting each one
        # recompute the footer would be quadratic noise.
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for nvr in devices:
            self._append_row(nvr, checked=nvr.id in checked_before)
        self.table.blockSignals(False)

        self._apply_filter(self.filter_edit.text())

    def _append_row(self, nvr: Nvr, checked: bool) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        try:
            channels = len(self._camera_repository.list_by_nvr(nvr.id))
        except sqlite3.Error as exc:
            logger.warning("Could not count channels of %d: %s", nvr.id, exc)
            channels = 0

        check_item = QTableWidgetItem()
        check_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
        )
        check_item.setCheckState(
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        check_item.setData(NVR_ID_ROLE, nvr.id)
        check_item.setData(IS_CAMERA_ROLE, nvr.is_camera)
        self.table.setItem(row, COLUMN_CHECK, check_item)

        cells = {
            COLUMN_NAME: nvr.name,
            COLUMN_ADDRESS: f"{nvr.host}:{nvr.rtsp_port}",
            COLUMN_KIND: "Câmera" if nvr.is_camera else "NVR",
            COLUMN_CHANNELS: str(channels),
            COLUMN_PASSWORD: "sim" if self._has_password(nvr.id) else "FALTA",
        }
        for column, text in cells.items():
            item = QTableWidgetItem(text)
            item.setData(NVR_ID_ROLE, nvr.id)
            if column == COLUMN_ADDRESS and nvr.has_custom_path:
                item.setToolTip(f"Caminho próprio: {nvr.stream_path}")
            if column == COLUMN_PASSWORD and text == "FALTA":
                # Not decoration: the app refuses to open a stream without a
                # stored password, so this is the reason a cell stays black.
                item.setToolTip(
                    "Sem senha no keyring — o CamView não abre o stream. "
                    "Use Editar para informá-la."
                )
            self.table.setItem(row, column, item)

    def _has_password(self, nvr_id: int | None) -> bool:
        """Is there a stored password for this device?

        A keyring that cannot be read is reported as "has one": the point
        of the column is to catch devices registered without a password,
        not to second-guess a broken keyring on every row.
        """
        if nvr_id is None:
            return False
        try:
            return bool(get_nvr_password(nvr_id))
        except CredentialsError as exc:
            logger.warning("Could not read the password of %d: %s", nvr_id, exc)
            return True

    def _apply_filter(self, text: str) -> None:
        """Hide rows that do not match, and uncheck what it hid.

        Unchecking keeps one invariant that matters for a destructive
        button: everything checked is on screen. Otherwise a filter could
        hide a checked row and "Excluir 3 dispositivos" would quietly take
        a fourth.
        """
        needle = text.strip().lower()
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            name = self.table.item(row, COLUMN_NAME).text().lower()
            address = self.table.item(row, COLUMN_ADDRESS).text().lower()
            matches = needle in name or needle in address
            self.table.setRowHidden(row, not matches)
            if not matches:
                self.table.item(row, COLUMN_CHECK).setCheckState(
                    Qt.CheckState.Unchecked
                )
        self.table.blockSignals(False)
        self._update_button_state()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def checked_ids(self) -> list[int]:
        """Device ids currently checked (always visible ones, by design)."""
        ids: list[int] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COLUMN_CHECK)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(NVR_ID_ROLE))
        return ids

    def _checked_rows(self) -> list[int]:
        return [
            row
            for row in range(self.table.rowCount())
            if self.table.item(row, COLUMN_CHECK).checkState()
            == Qt.CheckState.Checked
        ]

    def _visible_rows(self) -> list[int]:
        return [
            row
            for row in range(self.table.rowCount())
            if not self.table.isRowHidden(row)
        ]

    def _toggle_all(self) -> None:
        """Check every visible row, or clear them if all are checked."""
        visible = self._visible_rows()
        if not visible:
            return
        checked = set(self._checked_rows())
        target = (
            Qt.CheckState.Unchecked
            if all(row in checked for row in visible)
            else Qt.CheckState.Checked
        )
        self.table.blockSignals(True)
        for row in visible:
            self.table.item(row, COLUMN_CHECK).setCheckState(target)
        self.table.blockSignals(False)
        self._update_button_state()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == COLUMN_CHECK:
            self._update_button_state()

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        """Double-clicking a row edits it, whatever is checked."""
        nvr_id = item.data(NVR_ID_ROLE)
        if nvr_id is not None:
            self.editRequested.emit(nvr_id)

    def _update_button_state(self) -> None:
        rows = self._checked_rows()
        count = len(rows)
        only_nvrs = bool(rows) and not any(
            self.table.item(row, COLUMN_CHECK).data(IS_CAMERA_ROLE) for row in rows
        )

        self.edit_button.setEnabled(count == 1)
        # Channel discovery is an NVR question: a standalone camera has the
        # one channel it was registered with, and nothing to ask about it.
        self.sync_button.setEnabled(only_nvrs)
        self.sync_button.setToolTip(
            "Pergunta ao equipamento quais canais existem e como se chamam"
            if only_nvrs
            else "Disponível apenas para NVRs"
        )
        self.delete_button.setEnabled(count > 0)

        total = len(self._visible_rows())
        if count:
            self.summary_label.setText(f"{count} de {total} marcado(s)")
        else:
            self.summary_label.setText(f"{total} dispositivo(s)")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _edit_checked(self) -> None:
        ids = self.checked_ids()
        if len(ids) == 1:
            self.editRequested.emit(ids[0])

    def _sync_checked(self) -> None:
        """Ask every checked NVR for its channels.

        One request per device, fired together: each runs on its own
        worker thread in ``MainWindow``, which is what clicking them one
        by one would do anyway.
        """
        for nvr_id in self.checked_ids():
            self.syncRequested.emit(nvr_id)

    def _delete_checked(self) -> None:
        ids = self.checked_ids()
        if not ids:
            return

        names = [
            self.table.item(row, COLUMN_NAME).text() for row in self._checked_rows()
        ]
        shown = names[:MAX_NAMES_IN_CONFIRMATION]
        listing = "\n".join(f"  • {name}" for name in shown)
        if len(names) > len(shown):
            listing += f"\n  ... e mais {len(names) - len(shown)}"

        confirm = QMessageBox.question(
            self,
            "CamView",
            f"Excluir {len(ids)} dispositivo(s) e todas as suas câmeras?\n\n"
            f"{listing}",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        failures: list[str] = []
        for nvr_id, name in zip(ids, names):
            try:
                self._nvr_repository.delete(nvr_id)
            except sqlite3.Error as exc:
                # One failure must not abandon the rest of the batch.
                logger.error("Failed to remove device %d: %s", nvr_id, exc)
                failures.append(f"{name}: {exc}")
                continue
            deleted += 1
            try:
                delete_nvr_password(nvr_id)
            except CredentialsError as exc:
                # The record is already gone; a leftover keyring entry is
                # worth a log, not a failure the user must act on.
                logger.warning("Orphan keyring entry for %d: %s", nvr_id, exc)

        if failures:
            QMessageBox.warning(
                self,
                "CamView",
                "Não foi possível excluir:\n\n" + "\n".join(failures),
            )

        logger.info("Removed %d device(s) from the manager", deleted)
        self.refresh()
        if deleted:
            self.devicesChanged.emit()
