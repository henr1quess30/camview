"""Integration tests for restoring the previous session (Phase 6).

A new MainWindow over the same database stands in for restarting the
app: everything that survives a restart lives in the ``settings`` table.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from camview.database.repositories import SettingsRepository
from camview.models.nvr import Nvr
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.ui.main_window import (
    SETTING_GRID_SHAPE,
    SETTING_LAST_LAYOUT_ID,
    SETTING_WINDOW_GEOMETRY,
    MainWindow,
)

TEST_PASSWORD = "test-password"


@pytest.fixture
def db(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    fake_instance: FakeInstance,
) -> sqlite3.Connection:
    """A database with one NVR and four cameras, ready to open windows on."""
    window = MainWindow(connection=db_connection)
    nvr = window._nvr_repository.create(
        Nvr(name="NVR", host="192.0.2.10", username="admin", channel_count=4)
    )
    set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
    for camera in generate_missing_channel_cameras(nvr.id, 4):  # type: ignore[arg-type]
        window._camera_repository.create(camera)
    window.video_grid.clear()
    return db_connection


def open_window(connection: sqlite3.Connection) -> MainWindow:
    window = MainWindow(connection=connection)
    window.device_tree.refresh()
    return window


def camera_ids(window: MainWindow) -> list[int]:
    nvr = window._nvr_repository.list_all()[0]
    return [c.id for c in window._camera_repository.list_by_nvr(nvr.id)]  # type: ignore[arg-type,misc]


def save_layout(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, name: str
) -> int:
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True))
    )
    window._save_layout_as()
    saved = window._layout_repository.get_by_name(name)
    assert saved is not None
    return saved.id  # type: ignore[return-value]


class TestGridShape:
    def test_grid_shape_survives_a_restart(self, db: sqlite3.Connection) -> None:
        first = open_window(db)
        first.layout_selector.setCurrentText("3x3")
        first.close()

        second = open_window(db)

        assert (second.video_grid.rows, second.video_grid.columns) == (3, 3)
        assert second.layout_selector.currentText() == "3x3"

    def test_first_run_uses_the_default_shape(self, db: sqlite3.Connection) -> None:
        window = open_window(db)
        assert (window.video_grid.rows, window.video_grid.columns) == (2, 2)

    def test_unknown_stored_shape_is_ignored(self, db: sqlite3.Connection) -> None:
        SettingsRepository(db).set(SETTING_GRID_SHAPE, "7x7")

        window = open_window(db)

        assert (window.video_grid.rows, window.video_grid.columns) == (2, 2)


class TestWindowGeometry:
    def test_geometry_is_stored_on_close(self, db: sqlite3.Connection) -> None:
        window = open_window(db)
        window.close()

        stored = SettingsRepository(db).get(SETTING_WINDOW_GEOMETRY)
        assert stored, "geometry must be persisted for the next run"

    def test_stored_geometry_is_applied_on_startup(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = open_window(db)
        first.resize(900, 700)
        first.close()

        restored: list[object] = []
        monkeypatch.setattr(
            MainWindow,
            "restoreGeometry",
            lambda self, blob: restored.append(blob) or True,
        )
        open_window(db)

        assert len(restored) == 1

    def test_corrupt_geometry_does_not_break_startup(
        self, db: sqlite3.Connection
    ) -> None:
        SettingsRepository(db).set(SETTING_WINDOW_GEOMETRY, "not base64 at all!")

        window = open_window(db)  # must not raise

        assert window.windowTitle() == "CamView"


class TestLastLayout:
    def test_last_layout_reopens_with_its_cameras(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = open_window(db)
        first.layout_selector.setCurrentText("3x3")
        first._open_camera_at(camera_ids(first)[0], 0)
        first._open_camera_at(camera_ids(first)[1], 5)
        save_layout(first, monkeypatch, "Fábrica")
        first.close()

        second = open_window(db)

        assert (second.video_grid.rows, second.video_grid.columns) == (3, 3)
        assert sorted(second.video_grid.tiles()) == [0, 5]
        assert second.windowTitle() == "CamView — Fábrica"
        second.video_grid.clear()

    def test_no_saved_layout_starts_empty(self, db: sqlite3.Connection) -> None:
        first = open_window(db)
        first._open_camera_at(camera_ids(first)[0], 0)
        first.close()

        second = open_window(db)

        assert second.video_grid.tiles() == {}, (
            "an unsaved mosaic is not restored — only named layouts are"
        )

    def test_deleted_layout_is_ignored(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = open_window(db)
        first._open_camera_at(camera_ids(first)[0], 0)
        layout_id = save_layout(first, monkeypatch, "Fábrica")
        first.close()
        first._layout_repository.delete(layout_id)

        second = open_window(db)

        assert second.video_grid.tiles() == {}
        assert second.windowTitle() == "CamView"

    def test_invalid_stored_layout_id_is_ignored(
        self, db: sqlite3.Connection
    ) -> None:
        SettingsRepository(db).set(SETTING_LAST_LAYOUT_ID, "não é número")

        window = open_window(db)  # must not raise

        assert window.video_grid.tiles() == {}

    def test_restoring_never_shows_a_modal(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A device missing its password must not block startup with a dialog."""
        first = open_window(db)
        first._open_camera_at(camera_ids(first)[0], 0)
        save_layout(first, monkeypatch, "Fábrica")
        first.close()

        from camview.ui import main_window as mw

        monkeypatch.setattr(mw, "get_nvr_password", lambda _id: None)
        dialogs: list[object] = []
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: dialogs.append(a))
        )
        monkeypatch.setattr(
            QMessageBox, "critical", staticmethod(lambda *a, **k: dialogs.append(a))
        )

        second = open_window(db)

        assert dialogs == []
        assert second.video_grid.tiles() == {}

    def test_opening_an_nvr_clears_what_gets_restored(
        self, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = open_window(db)
        first._open_camera_at(camera_ids(first)[0], 0)
        save_layout(first, monkeypatch, "Fábrica")
        nvr = first._nvr_repository.list_all()[0]
        first._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]
        first.close()

        second = open_window(db)

        assert second.video_grid.tiles() == {}, (
            "the layout was replaced by an ad-hoc NVR view before closing"
        )
