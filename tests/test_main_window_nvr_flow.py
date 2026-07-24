"""Integration tests for the NVR add/edit/remove flow wired into MainWindow.

``NvrDialog.exec`` is monkeypatched to fill in form fields and return
``Accepted`` without showing a real modal dialog — this exercises the
same orchestration code (repositories, keyring, tree refresh) that a
real user interaction would trigger, without needing GUI automation.
"""

from __future__ import annotations

import sqlite3

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from camview.models.nvr import Nvr
from camview.services.credentials import get_nvr_password, set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.main_window import MainWindow


def test_add_nvr_creates_camera_rows_stores_password_and_refreshes_tree(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_exec(self: NvrDialog) -> QDialog.DialogCode:
        self.name_edit.setText("Garagem")
        self.host_edit.setText("192.0.2.10")
        self.username_edit.setText("admin")
        self.password_edit.setText("test-password")
        self.channel_count_spin.setValue(2)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(NvrDialog, "exec", fake_exec)

    window = MainWindow(connection=db_connection)
    try:
        window._add_nvr()

        nvrs = window._nvr_repository.list_all()
        assert len(nvrs) == 1
        assert nvrs[0].name == "Garagem"
        assert nvrs[0].channel_count == 2

        cameras = window._camera_repository.list_by_nvr(nvrs[0].id)  # type: ignore[arg-type]
        assert [c.channel_number for c in cameras] == [1, 2]
        assert [c.name for c in cameras] == ["Canal 1", "Canal 2"]

        assert get_nvr_password(nvrs[0].id) == "test-password"  # type: ignore[arg-type]

        assert window.device_tree.topLevelItemCount() == 1
        nvr_item = window.device_tree.topLevelItem(0)
        assert nvr_item.text(0) == "Garagem"
        assert nvr_item.childCount() == 2
    finally:
        window.close()


def test_edit_nvr_adds_new_channels_without_removing_existing(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow(connection=db_connection)
    try:
        nvr = window._nvr_repository.create(
            Nvr(name="Original", host="192.0.2.10", username="admin", channel_count=2)
        )
        set_nvr_password(nvr.id, "old-password")  # type: ignore[arg-type]
        for camera in generate_missing_channel_cameras(nvr.id, 2):  # type: ignore[arg-type]
            window._camera_repository.create(camera)
        window.device_tree.refresh()

        def fake_exec(self: NvrDialog) -> QDialog.DialogCode:
            self.name_edit.setText("Renomeado")
            self.channel_count_spin.setValue(4)
            self.password_edit.setText("new-password")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(NvrDialog, "exec", fake_exec)

        window._edit_nvr(nvr.id)  # type: ignore[arg-type]

        updated = window._nvr_repository.get(nvr.id)  # type: ignore[arg-type]
        assert updated is not None
        assert updated.name == "Renomeado"
        assert updated.channel_count == 4
        assert get_nvr_password(nvr.id) == "new-password"  # type: ignore[arg-type]

        cameras = window._camera_repository.list_by_nvr(nvr.id)  # type: ignore[arg-type]
        assert [c.channel_number for c in cameras] == [1, 2, 3, 4]
    finally:
        window.close()


def test_remove_nvr_deletes_row_password_and_tree_entry(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow(connection=db_connection)
    try:
        nvr = window._nvr_repository.create(
            Nvr(name="ParaRemover", host="192.0.2.20", username="admin", channel_count=1)
        )
        set_nvr_password(nvr.id, "some-password")  # type: ignore[arg-type]
        window.device_tree.refresh()

        monkeypatch.setattr(
            QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
        )

        window._remove_nvr(nvr.id)  # type: ignore[arg-type]

        assert window._nvr_repository.get(nvr.id) is None  # type: ignore[arg-type]
        assert get_nvr_password(nvr.id) is None  # type: ignore[arg-type]
        assert window.device_tree.topLevelItemCount() == 0
    finally:
        window.close()


def test_empty_password_blocks_stream_instead_of_attempting_rtsp(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing keyring entry must not burn auth attempts against the NVR.

    Hikvision devices lock out the source IP after a few failed logins,
    so an empty password has to fail in the UI, before any RTSP connect.
    """
    window = MainWindow(connection=db_connection)
    try:
        nvr = window._nvr_repository.create(
            Nvr(name="SemSenha", host="192.0.2.30", username="admin", channel_count=1)
        )
        for camera in generate_missing_channel_cameras(nvr.id, 1):  # type: ignore[arg-type]
            window._camera_repository.create(camera)
        # deliberately no set_nvr_password() call

        warned: list[str] = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: warned.append(a[2])
        )

        cameras = window._camera_repository.list_by_nvr(nvr.id)  # type: ignore[arg-type]
        window._show_camera_stream(cameras[0].id)  # type: ignore[arg-type]

        assert window._video_tile is None, "no stream may be opened without a password"
        assert warned and "senha" in warned[0].lower()
    finally:
        window.close()
