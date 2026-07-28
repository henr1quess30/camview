"""Tests for the device manager: the one place devices are managed.

The dialog is exercised directly — no modal is ever shown — and the
``MainWindow`` wiring is checked through the signals the dialog emits.
Every password here is invented and every address is from the RFC 5737
documentation range.
"""

from __future__ import annotations

import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from camview.database.repositories import CameraRepository, NvrRepository
from camview.models.camera import Camera
from camview.models.nvr import DeviceType, Nvr
from camview.services.credentials import get_nvr_password, set_nvr_password
from camview.ui.dialogs.device_manager_dialog import (
    COLUMN_CHECK,
    COLUMN_CHANNELS,
    COLUMN_KIND,
    COLUMN_NAME,
    COLUMN_PASSWORD,
    DeviceManagerDialog,
)
from camview.ui.main_window import MainWindow


def _make_device(
    connection: sqlite3.Connection,
    name: str,
    host: str,
    *,
    channels: int = 2,
    is_camera: bool = False,
    password: str | None = "test-password",
) -> Nvr:
    """Register a device with its channels, as the app would."""
    nvrs = NvrRepository(connection)
    cameras = CameraRepository(connection)
    device = nvrs.create(
        Nvr(
            name=name,
            host=host,
            username="operador",
            channel_count=channels,
            device_type=DeviceType.CAMERA if is_camera else DeviceType.NVR,
        )
    )
    for number in range(1, channels + 1):
        cameras.create(
            Camera(nvr_id=device.id, channel_number=number, name=f"Canal {number}")
        )
    if password is not None:
        set_nvr_password(device.id, password)
    return device


@pytest.fixture
def dialog(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
) -> DeviceManagerDialog:
    _make_device(db_connection, "Portaria", "192.0.2.10", channels=3)
    _make_device(db_connection, "Doca Norte", "192.0.2.11", channels=2)
    _make_device(
        db_connection, "Cam Estoque", "192.0.2.20", channels=1, is_camera=True
    )
    return DeviceManagerDialog(
        NvrRepository(db_connection), CameraRepository(db_connection)
    )


def _check_row(dialog: DeviceManagerDialog, row: int) -> None:
    dialog.table.item(row, COLUMN_CHECK).setCheckState(Qt.CheckState.Checked)


def _row_named(dialog: DeviceManagerDialog, name: str) -> int:
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, COLUMN_NAME).text() == name:
            return row
    raise AssertionError(f"No row named {name!r}")


class TestContents:
    def test_every_device_gets_a_row(self, dialog: DeviceManagerDialog) -> None:
        assert dialog.table.rowCount() == 3

    def test_channels_are_counted_from_the_database(
        self, dialog: DeviceManagerDialog
    ) -> None:
        row = _row_named(dialog, "Portaria")

        assert dialog.table.item(row, COLUMN_CHANNELS).text() == "3"

    def test_a_standalone_camera_is_labelled_as_one(
        self, dialog: DeviceManagerDialog
    ) -> None:
        row = _row_named(dialog, "Cam Estoque")

        assert dialog.table.item(row, COLUMN_KIND).text() == "Câmera"
        assert dialog.table.item(_row_named(dialog, "Portaria"), COLUMN_KIND).text() == "NVR"

    def test_a_device_without_a_password_is_flagged(
        self,
        qapp: QApplication,
        db_connection: sqlite3.Connection,
        fake_keyring: object,
    ) -> None:
        """The app refuses to open a stream without one, so it is shown."""
        _make_device(db_connection, "Sem senha", "192.0.2.30", password=None)
        manager = DeviceManagerDialog(
            NvrRepository(db_connection), CameraRepository(db_connection)
        )

        row = _row_named(manager, "Sem senha")
        assert manager.table.item(row, COLUMN_PASSWORD).text() == "FALTA"


class TestSelection:
    def test_nothing_is_checked_initially(
        self, dialog: DeviceManagerDialog
    ) -> None:
        assert dialog.checked_ids() == []
        assert not dialog.delete_button.isEnabled()

    def test_editing_needs_exactly_one(self, dialog: DeviceManagerDialog) -> None:
        _check_row(dialog, 0)
        assert dialog.edit_button.isEnabled()

        _check_row(dialog, 1)
        assert not dialog.edit_button.isEnabled()

    def test_syncing_is_refused_when_a_camera_is_checked(
        self, dialog: DeviceManagerDialog
    ) -> None:
        """A standalone camera has no channel list to ask about."""
        _check_row(dialog, _row_named(dialog, "Portaria"))
        assert dialog.sync_button.isEnabled()

        _check_row(dialog, _row_named(dialog, "Cam Estoque"))
        assert not dialog.sync_button.isEnabled()

    def test_toggle_all_checks_then_clears(
        self, dialog: DeviceManagerDialog
    ) -> None:
        dialog._toggle_all()
        assert len(dialog.checked_ids()) == 3

        dialog._toggle_all()
        assert dialog.checked_ids() == []


class TestFiltering:
    def test_filtering_by_name_hides_the_rest(
        self, dialog: DeviceManagerDialog
    ) -> None:
        dialog.filter_edit.setText("doca")

        visible = [
            dialog.table.item(row, COLUMN_NAME).text()
            for row in range(dialog.table.rowCount())
            if not dialog.table.isRowHidden(row)
        ]
        assert visible == ["Doca Norte"]

    def test_filtering_by_address_works_too(
        self, dialog: DeviceManagerDialog
    ) -> None:
        dialog.filter_edit.setText("192.0.2.20")

        visible = [
            dialog.table.item(row, COLUMN_NAME).text()
            for row in range(dialog.table.rowCount())
            if not dialog.table.isRowHidden(row)
        ]
        assert visible == ["Cam Estoque"]

    def test_a_row_the_filter_hides_is_unchecked(
        self, dialog: DeviceManagerDialog
    ) -> None:
        """Otherwise "excluir 1" could quietly take a hidden second one."""
        _check_row(dialog, _row_named(dialog, "Portaria"))
        assert len(dialog.checked_ids()) == 1

        dialog.filter_edit.setText("doca")

        assert dialog.checked_ids() == []

    def test_toggle_all_only_takes_visible_rows(
        self, dialog: DeviceManagerDialog
    ) -> None:
        dialog.filter_edit.setText("cam")
        dialog._toggle_all()

        assert len(dialog.checked_ids()) == 1


class TestBatchDelete:
    def test_two_devices_go_in_one_confirmation(
        self,
        dialog: DeviceManagerDialog,
        db_connection: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        asked: list[str] = []

        def fake_question(_parent, _title, text, *args, **kwargs):
            asked.append(text)
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", fake_question)

        _check_row(dialog, _row_named(dialog, "Portaria"))
        _check_row(dialog, _row_named(dialog, "Doca Norte"))
        dialog._delete_checked()

        assert len(asked) == 1, "one confirmation for the whole batch"
        assert "Portaria" in asked[0] and "Doca Norte" in asked[0]
        remaining = [n.name for n in NvrRepository(db_connection).list_all()]
        assert remaining == ["Cam Estoque"]

    def test_declining_keeps_everything(
        self,
        dialog: DeviceManagerDialog,
        db_connection: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
        )

        dialog._toggle_all()
        dialog._delete_checked()

        assert len(NvrRepository(db_connection).list_all()) == 3

    def test_the_password_goes_with_the_device(
        self,
        dialog: DeviceManagerDialog,
        db_connection: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        device_id = NvrRepository(db_connection).list_all()[0].id

        _check_row(dialog, 0)
        dialog._delete_checked()

        assert get_nvr_password(device_id) is None

    def test_one_failure_does_not_abandon_the_batch(
        self,
        dialog: DeviceManagerDialog,
        db_connection: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        warned: list[str] = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda _p, _t, text, *a, **k: warned.append(text),
        )

        first_id = dialog.table.item(0, COLUMN_CHECK).data(Qt.ItemDataRole.UserRole)
        original_delete = NvrRepository.delete

        def flaky_delete(self: NvrRepository, nvr_id: int) -> None:
            if nvr_id == first_id:
                raise sqlite3.OperationalError("database is locked")
            original_delete(self, nvr_id)

        monkeypatch.setattr(NvrRepository, "delete", flaky_delete)

        dialog._toggle_all()
        dialog._delete_checked()

        remaining = [n.id for n in NvrRepository(db_connection).list_all()]
        assert remaining == [first_id], "only the failing one survives"
        assert warned and "database is locked" in warned[0]

    def test_the_table_reloads_after_deleting(
        self,
        dialog: DeviceManagerDialog,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        changed: list[bool] = []
        dialog.devicesChanged.connect(lambda: changed.append(True))

        _check_row(dialog, 0)
        dialog._delete_checked()

        assert dialog.table.rowCount() == 2
        assert changed == [True], "the sidebar must be told"


class TestActionsAreDelegated:
    def test_edit_asks_the_window_instead_of_editing_here(
        self, dialog: DeviceManagerDialog
    ) -> None:
        """Editing opens NvrDialog, which only MainWindow knows how to wire."""
        requested: list[int] = []
        dialog.editRequested.connect(requested.append)

        _check_row(dialog, 0)
        dialog._edit_checked()

        assert requested == [dialog.checked_ids()[0]]

    def test_sync_is_requested_once_per_checked_nvr(
        self, dialog: DeviceManagerDialog
    ) -> None:
        requested: list[int] = []
        dialog.syncRequested.connect(requested.append)

        _check_row(dialog, _row_named(dialog, "Portaria"))
        _check_row(dialog, _row_named(dialog, "Doca Norte"))
        dialog._sync_checked()

        assert len(requested) == 2

    def test_double_clicking_a_row_edits_it(
        self, dialog: DeviceManagerDialog
    ) -> None:
        requested: list[int] = []
        dialog.editRequested.connect(requested.append)

        row = _row_named(dialog, "Doca Norte")
        dialog._on_item_double_clicked(dialog.table.item(row, COLUMN_NAME))

        assert len(requested) == 1


class TestMainWindowWiring:
    def test_the_manager_refreshes_when_a_sync_lands(
        self,
        qapp: QApplication,
        db_connection: sqlite3.Connection,
        fake_keyring: object,
    ) -> None:
        """A channel sync finishing must not leave the open table stale."""
        _make_device(db_connection, "Portaria", "192.0.2.10", channels=1)
        window = MainWindow(connection=db_connection)
        try:
            manager = DeviceManagerDialog(
                window._nvr_repository, window._camera_repository
            )
            window._device_manager = manager
            assert manager.table.item(0, COLUMN_CHANNELS).text() == "1"

            window._camera_repository.create(
                Camera(
                    nvr_id=window._nvr_repository.list_all()[0].id,
                    channel_number=2,
                    name="Canal 2",
                )
            )
            window._refresh_device_tree()

            assert manager.table.item(0, COLUMN_CHANNELS).text() == "2"
        finally:
            window._device_manager = None
            window.close()

    def test_the_reference_is_cleared_after_the_dialog_closes(
        self,
        qapp: QApplication,
        db_connection: sqlite3.Connection,
        fake_keyring: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Otherwise a later refresh would touch a dead widget."""
        monkeypatch.setattr(DeviceManagerDialog, "exec", lambda self: 0)

        window = MainWindow(connection=db_connection)
        try:
            window._manage_devices()
            assert window._device_manager is None
        finally:
            window.close()
