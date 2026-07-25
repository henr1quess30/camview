"""Dialog for managing saved layouts: load, rename and delete.

Renaming and deleting are applied to the database from inside the dialog —
they are self-contained edits with no effect on what is on screen. Loading
is not: it replaces the whole mosaic, so the dialog only *reports* the
chosen layout (via :attr:`selected_layout_id` on accept) and lets
``MainWindow`` do it.
"""

from __future__ import annotations

import logging
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from camview.database.repositories import LayoutRepository

logger = logging.getLogger(__name__)

LAYOUT_ID_ROLE = Qt.ItemDataRole.UserRole


class LayoutManagerDialog(QDialog):
    """List of saved layouts with load/rename/delete actions."""

    def __init__(
        self,
        layout_repository: LayoutRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Layouts salvos")
        self.resize(420, 320)
        self._layout_repository = layout_repository
        #: Layout the user chose to load; ``None`` unless the dialog was accepted.
        self.selected_layout_id: int | None = None

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._load())
        self.list_widget.currentItemChanged.connect(
            lambda *_: self._update_button_state()
        )
        root.addWidget(self.list_widget)

        buttons = QHBoxLayout()
        self.load_button = QPushButton("Carregar")
        self.load_button.setDefault(True)
        self.load_button.clicked.connect(self._load)
        self.rename_button = QPushButton("Renomear...")
        self.rename_button.clicked.connect(self._rename)
        self.delete_button = QPushButton("Excluir")
        self.delete_button.clicked.connect(self._delete)
        close_button = QPushButton("Fechar")
        close_button.clicked.connect(self.reject)

        buttons.addWidget(self.load_button)
        buttons.addWidget(self.rename_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    def refresh(self) -> None:
        self.list_widget.clear()
        for layout in self._layout_repository.list_all():
            cameras = len(self._layout_repository.get_items(layout.id))  # type: ignore[arg-type]
            item = QListWidgetItem(
                f"{layout.name}  —  {layout.rows}x{layout.columns}, "
                f"{cameras} câmera(s)"
            )
            item.setData(LAYOUT_ID_ROLE, layout.id)
            self.list_widget.addItem(item)

        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._update_button_state()

    def _update_button_state(self) -> None:
        has_selection = self.list_widget.currentItem() is not None
        self.load_button.setEnabled(has_selection)
        self.rename_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _current_layout_id(self) -> int | None:
        item = self.list_widget.currentItem()
        return None if item is None else item.data(LAYOUT_ID_ROLE)

    def _load(self) -> None:
        layout_id = self._current_layout_id()
        if layout_id is None:
            return
        self.selected_layout_id = layout_id
        self.accept()

    def _rename(self) -> None:
        layout_id = self._current_layout_id()
        if layout_id is None:
            return
        layout = self._layout_repository.get(layout_id)
        if layout is None:
            return

        new_name, confirmed = QInputDialog.getText(
            self, "Renomear layout", "Novo nome:", text=layout.name
        )
        new_name = new_name.strip()
        if not confirmed or not new_name or new_name == layout.name:
            return

        existing = self._layout_repository.get_by_name(new_name)
        if existing is not None:
            QMessageBox.warning(
                self, "CamView", f"Já existe um layout chamado '{new_name}'."
            )
            return

        try:
            self._layout_repository.rename(layout_id, new_name)
        except sqlite3.Error as exc:
            logger.error("Failed to rename layout %d: %s", layout_id, exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return
        self.refresh()

    def _delete(self) -> None:
        layout_id = self._current_layout_id()
        if layout_id is None:
            return
        layout = self._layout_repository.get(layout_id)
        if layout is None:
            return

        confirm = QMessageBox.question(
            self, "CamView", f"Excluir o layout '{layout.name}'?"
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            self._layout_repository.delete(layout_id)
        except sqlite3.Error as exc:
            logger.error("Failed to delete layout %d: %s", layout_id, exc)
            QMessageBox.critical(self, "CamView", str(exc))
            return
        self.refresh()
