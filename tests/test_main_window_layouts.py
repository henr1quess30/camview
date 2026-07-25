"""Integration tests for saved layouts (Phase 5).

Covers the round trip the repository tests can't see: capturing the live
mosaic into layout items, and rebuilding the mosaic — grid shape, cells and
per-cell stream — from a saved layout.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from camview.models.camera import StreamType
from camview.models.layout import Layout, LayoutItem
from camview.models.nvr import Nvr
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.ui.dialogs.layout_dialog import LAYOUT_ID_ROLE, LayoutManagerDialog
from camview.ui.main_window import MainWindow

TEST_PASSWORD = "test-password"


@pytest.fixture
def window(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    fake_instance: FakeInstance,
) -> MainWindow:
    win = MainWindow(connection=db_connection)
    nvr = win._nvr_repository.create(
        Nvr(name="NVR", host="192.0.2.10", username="admin", channel_count=4)
    )
    set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
    for camera in generate_missing_channel_cameras(nvr.id, 4):  # type: ignore[arg-type]
        win._camera_repository.create(camera)
    win.device_tree.refresh()
    return win


def camera_ids(window: MainWindow) -> list[int]:
    nvr = window._nvr_repository.list_all()[0]
    return [c.id for c in window._camera_repository.list_by_nvr(nvr.id)]  # type: ignore[arg-type,misc]


def accept_name(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make the 'name this layout' prompt answer with ``name``."""
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True))
    )


class TestSaveLayout:
    def test_saves_grid_shape_and_every_occupied_cell(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window.layout_selector.setCurrentText("3x3")
        window._open_camera_at(camera_ids(window)[0], 0)
        window._open_camera_at(camera_ids(window)[1], 4)
        accept_name(monkeypatch, "Fábrica")

        window._save_layout_as()

        saved = window._layout_repository.get_by_name("Fábrica")
        assert saved is not None
        assert (saved.rows, saved.columns) == (3, 3)
        items = window._layout_repository.get_items(saved.id)  # type: ignore[arg-type]
        assert [(i.position, i.camera_id) for i in items] == [
            (0, camera_ids(window)[0]),
            (4, camera_ids(window)[1]),
        ]
        window.video_grid.clear()

    def test_records_the_mosaic_stream_not_the_maximized_one(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Maximizing bumps a cell to the main stream; that is viewing state."""
        window.layout_selector.setCurrentText("2x2")
        window._open_camera_at(camera_ids(window)[0], 0)
        window.video_grid.maximize(0)
        assert window.video_grid.tile_at(0).stream_type is StreamType.MAIN  # type: ignore[union-attr]
        accept_name(monkeypatch, "Maximizado")

        window._save_layout_as()

        saved = window._layout_repository.get_by_name("Maximizado")
        items = window._layout_repository.get_items(saved.id)  # type: ignore[arg-type,union-attr]
        assert items[0].stream_type is StreamType.SUB
        window.video_grid.clear()

    def test_empty_mosaic_is_not_saved(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        accept_name(monkeypatch, "Vazio")

        window._save_layout_as()

        assert window._layout_repository.list_all() == []
        assert "vazio" in window.statusBar().currentMessage().lower()

    def test_cancelling_the_prompt_saves_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("Fábrica", False))
        )

        window._save_layout_as()

        assert window._layout_repository.list_all() == []
        window.video_grid.clear()

    def test_blank_name_saves_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "   ")

        window._save_layout_as()

        assert window._layout_repository.list_all() == []
        window.video_grid.clear()

    def test_duplicate_name_asks_before_overwriting(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Portaria")
        window._save_layout_as()
        first = window._layout_repository.get_by_name("Portaria")

        window._open_camera_at(camera_ids(window)[1], 1)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        window._save_layout_as()

        layouts = window._layout_repository.list_all()
        assert len(layouts) == 1, "overwrite must reuse the row, not duplicate it"
        items = window._layout_repository.get_items(first.id)  # type: ignore[arg-type,union-attr]
        assert len(items) == 2
        window.video_grid.clear()

    def test_declining_the_overwrite_keeps_the_old_layout(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Portaria")
        window._save_layout_as()
        saved = window._layout_repository.get_by_name("Portaria")

        window._open_camera_at(camera_ids(window)[1], 1)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
        )
        window._save_layout_as()

        items = window._layout_repository.get_items(saved.id)  # type: ignore[arg-type,union-attr]
        assert len(items) == 1
        window.video_grid.clear()

    def test_ctrl_s_overwrites_the_loaded_layout_without_prompting(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Fábrica")
        window._save_layout_as()

        def fail(*_args: object, **_kwargs: object) -> tuple[str, bool]:
            raise AssertionError("saving a named layout must not prompt again")

        monkeypatch.setattr(QInputDialog, "getText", staticmethod(fail))
        window._open_camera_at(camera_ids(window)[1], 1)
        window._save_layout()

        saved = window._layout_repository.get_by_name("Fábrica")
        assert len(window._layout_repository.get_items(saved.id)) == 2  # type: ignore[arg-type,union-attr]
        window.video_grid.clear()

    def test_save_falls_back_to_prompt_when_nothing_is_loaded(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Novo")

        window._save_layout()

        assert window._layout_repository.get_by_name("Novo") is not None
        window.video_grid.clear()


class TestLoadLayout:
    def _save(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> int:
        accept_name(monkeypatch, name)
        window._save_layout_as()
        saved = window._layout_repository.get_by_name(name)
        assert saved is not None
        return saved.id  # type: ignore[return-value]

    def test_round_trip_restores_shape_cells_and_streams(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window.layout_selector.setCurrentText("3x3")
        window._open_camera_at(camera_ids(window)[0], 0)
        window._open_camera_at(camera_ids(window)[2], 7)
        layout_id = self._save(window, monkeypatch, "Fábrica")

        window.video_grid.clear()
        window.layout_selector.setCurrentText("2x2")
        window._load_layout(layout_id)

        assert (window.video_grid.rows, window.video_grid.columns) == (3, 3)
        assert window.layout_selector.currentText() == "3x3"
        assert window.video_grid.tile_at(0).camera_id == camera_ids(window)[0]  # type: ignore[union-attr]
        assert window.video_grid.tile_at(7).camera_id == camera_ids(window)[2]  # type: ignore[union-attr]
        assert window.video_grid.tile_at(0).stream_type is StreamType.SUB  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_restores_a_main_stream_cell_as_main(self, window: MainWindow) -> None:
        layout = window._layout_repository.create(
            Layout(name="Principal", rows=1, columns=1)
        )
        window._layout_repository.set_items(
            layout.id,  # type: ignore[arg-type]
            [
                LayoutItem(
                    layout_id=layout.id,  # type: ignore[arg-type]
                    camera_id=camera_ids(window)[0],
                    position=0,
                    stream_type=StreamType.MAIN,
                )
            ],
        )

        window._load_layout(layout.id)  # type: ignore[arg-type]

        tile = window.video_grid.tile_at(0)
        assert tile is not None
        assert tile.stream_type is StreamType.MAIN
        assert tile.url.endswith("/Streaming/Channels/101")
        window.video_grid.clear()

    def test_replaces_whatever_was_on_screen(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        fake_instance: FakeInstance,
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        layout_id = self._save(window, monkeypatch, "Fábrica")

        window._open_camera_at(camera_ids(window)[3], 3)
        window.video_grid.tile_at(3)._connect()  # type: ignore[union-attr]
        replaced = fake_instance.players[-1]

        window._load_layout(layout_id)

        assert replaced.released is True, "old tiles must be released, not leaked"
        assert list(window.video_grid.tiles()) == [0]
        window.video_grid.clear()

    def test_skips_cameras_that_no_longer_exist(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        window._open_camera_at(camera_ids(window)[1], 1)
        layout_id = self._save(window, monkeypatch, "Fábrica")
        window.video_grid.clear()

        # Deleting the camera cascades its layout item away; a stale item is
        # simulated by pointing the layout at an id that is gone.
        removed = camera_ids(window)[1]
        window._camera_repository.delete(removed)

        window._load_layout(layout_id)

        assert list(window.video_grid.tiles()) == [0]
        window.video_grid.clear()

    def test_missing_password_warns_once_and_opens_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        window._open_camera_at(camera_ids(window)[1], 1)
        layout_id = self._save(window, monkeypatch, "Fábrica")
        window.video_grid.clear()

        from camview.ui import main_window as mw

        monkeypatch.setattr(mw, "get_nvr_password", lambda _id: None)
        warnings: list[object] = []
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
        )

        window._load_layout(layout_id)

        assert len(warnings) == 1, "one warning per NVR, not per cell"
        assert window.video_grid.tiles() == {}

    def test_deleted_layout_reports_instead_of_clearing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        layout_id = self._save(window, monkeypatch, "Fábrica")
        window._layout_repository.delete(layout_id)

        window._load_layout(layout_id)

        assert list(window.video_grid.tiles()) == [0], "mosaic must be left alone"
        assert "não existe mais" in window.statusBar().currentMessage()
        window.video_grid.clear()


class TestCurrentLayoutTracking:
    def test_window_title_shows_the_loaded_layout(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Fábrica")
        window._save_layout_as()

        assert window.windowTitle() == "CamView — Fábrica"
        window.video_grid.clear()

    def test_opening_a_whole_nvr_clears_the_current_layout(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Fábrica")
        window._save_layout_as()

        nvr = window._nvr_repository.list_all()[0]
        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]

        assert window.windowTitle() == "CamView"
        assert window._current_layout_id is None
        window.video_grid.clear()

    def test_menu_lists_saved_layouts(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Fábrica")
        window._save_layout_as()

        window._rebuild_layouts_menu()

        labels = [action.text() for action in window.layouts_menu.actions()]
        assert "Fábrica" in labels
        window.video_grid.clear()


class TestLayoutManagerDialog:
    @pytest.fixture
    def dialog(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> LayoutManagerDialog:
        window._open_camera_at(camera_ids(window)[0], 0)
        accept_name(monkeypatch, "Fábrica")
        window._save_layout_as()
        window.video_grid.clear()
        return LayoutManagerDialog(window._layout_repository, parent=window)

    def test_lists_saved_layouts(self, dialog: LayoutManagerDialog) -> None:
        assert dialog.list_widget.count() == 1
        assert "Fábrica" in dialog.list_widget.item(0).text()

    def test_load_reports_the_chosen_layout(
        self, dialog: LayoutManagerDialog
    ) -> None:
        expected = dialog.list_widget.item(0).data(LAYOUT_ID_ROLE)

        dialog._load()

        assert dialog.selected_layout_id == expected
        assert dialog.result() == LayoutManagerDialog.DialogCode.Accepted

    def test_rename_updates_the_list(
        self, dialog: LayoutManagerDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        accept_name(monkeypatch, "Portaria")

        dialog._rename()

        assert "Portaria" in dialog.list_widget.item(0).text()
        assert dialog._layout_repository.get_by_name("Fábrica") is None

    def test_rename_to_an_existing_name_is_refused(
        self, dialog: LayoutManagerDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dialog._layout_repository.create(Layout(name="Portaria", rows=1, columns=1))
        dialog.refresh()
        dialog.list_widget.setCurrentRow(0)  # "Fábrica" sorts first
        accept_name(monkeypatch, "Portaria")
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

        dialog._rename()

        assert dialog._layout_repository.get_by_name("Fábrica") is not None

    def test_delete_asks_first(
        self, dialog: LayoutManagerDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
        )
        dialog._delete()
        assert dialog.list_widget.count() == 1

        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        dialog._delete()
        assert dialog.list_widget.count() == 0

    def test_buttons_disabled_without_a_selection(
        self, window: MainWindow
    ) -> None:
        empty = LayoutManagerDialog(window._layout_repository, parent=window)
        assert empty.load_button.isEnabled() is False
        assert empty.rename_button.isEnabled() is False
        assert empty.delete_button.isEnabled() is False
