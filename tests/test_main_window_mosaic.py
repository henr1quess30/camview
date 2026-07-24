"""Integration tests for the mosaic wiring in MainWindow.

Covers what the grid alone cannot: turning a camera id into an RTSP URL,
choosing main vs sub stream, and routing double-clicks/drops into cells.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication, QMessageBox

from camview.models.camera import StreamType
from camview.models.nvr import Nvr
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
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


class TestDoubleClickToAdd:
    def test_uses_the_first_free_cell(self, window: MainWindow) -> None:
        tree_nvr = window.device_tree.topLevelItem(0)

        window._on_device_tree_item_double_clicked(tree_nvr.child(0), 0)
        window._on_device_tree_item_double_clicked(tree_nvr.child(1), 0)

        assert window.video_grid.tile_at(0).title == "Canal 1"  # type: ignore[union-attr]
        assert window.video_grid.tile_at(1).title == "Canal 2"  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_full_grid_reports_instead_of_replacing(self, window: MainWindow) -> None:
        window.video_grid.set_grid_shape(1, 1)
        tree_nvr = window.device_tree.topLevelItem(0)

        window._on_device_tree_item_double_clicked(tree_nvr.child(0), 0)
        window._on_device_tree_item_double_clicked(tree_nvr.child(1), 0)

        assert window.video_grid.tile_at(0).title == "Canal 1"  # type: ignore[union-attr]
        assert "cheio" in window.statusBar().currentMessage().lower()
        window.video_grid.clear()


class TestDoubleClickNvrOpensWholeMosaic:
    def test_opens_every_channel_and_resizes_the_grid(
        self, window: MainWindow
    ) -> None:
        window._on_device_tree_item_double_clicked(window.device_tree.topLevelItem(0), 0)

        # 4 cameras -> smallest grid that fits is 2x2.
        assert (window.video_grid.rows, window.video_grid.columns) == (2, 2)
        titles = [t.title for _, t in sorted(window.video_grid.tiles().items())]
        assert titles == ["Canal 1", "Canal 2", "Canal 3", "Canal 4"]
        window.video_grid.clear()

    def test_keeps_the_toolbar_selector_in_sync(self, window: MainWindow) -> None:
        window.layout_selector.setCurrentText("4x4")

        window._on_device_tree_item_double_clicked(window.device_tree.topLevelItem(0), 0)

        assert window.layout_selector.currentText() == "2x2"
        window.video_grid.clear()

    def test_replaces_cameras_already_on_screen(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        window.layout_selector.setCurrentText("3x3")
        window._open_camera_at(camera_ids(window)[3], 8)
        window.video_grid.tile_at(8)._connect()  # type: ignore[union-attr]
        replaced = fake_instance.players[-1]

        window._on_device_tree_item_double_clicked(window.device_tree.topLevelItem(0), 0)

        assert replaced.released is True, "old tiles must be released, not leaked"
        assert len(window.video_grid.tiles()) == 4
        window.video_grid.clear()

    def test_nvr_without_cameras_reports_instead_of_clearing(
        self, window: MainWindow
    ) -> None:
        empty = window._nvr_repository.create(
            Nvr(name="Vazio", host="192.0.2.99", username="admin", channel_count=1)
        )
        set_nvr_password(empty.id, TEST_PASSWORD)  # type: ignore[arg-type]
        window.device_tree.refresh()

        nvr_item = next(
            window.device_tree.topLevelItem(i)
            for i in range(window.device_tree.topLevelItemCount())
            if window.device_tree.topLevelItem(i).text(0) == "Vazio"
        )
        window._on_device_tree_item_double_clicked(nvr_item, 0)

        assert window.video_grid.tiles() == {}
        assert "não tem câmeras" in window.statusBar().currentMessage()

    def test_missing_password_warns_once_not_once_per_camera(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nvr = window._nvr_repository.list_all()[0]
        from camview.services import credentials

        monkeypatch.setattr(credentials, "delete_nvr_password", lambda _id: None)
        from camview.ui import main_window as mw

        monkeypatch.setattr(mw, "get_nvr_password", lambda _id: None)

        warnings: list[object] = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]

        assert len(warnings) == 1, "one warning for the NVR, not one per channel"
        assert window.video_grid.tiles() == {}

    @pytest.mark.parametrize(
        ("channel_count", "expected_shape"),
        [(1, (1, 1)), (3, (2, 2)), (4, (2, 2)), (5, (3, 3)), (9, (3, 3)), (12, (4, 4))],
    )
    def test_grid_shape_matches_channel_count(
        self,
        window: MainWindow,
        channel_count: int,
        expected_shape: tuple[int, int],
    ) -> None:
        nvr = window._nvr_repository.create(
            Nvr(
                name="Outro",
                host="192.0.2.50",
                username="admin",
                channel_count=channel_count,
            )
        )
        set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
        for camera in generate_missing_channel_cameras(nvr.id, channel_count):  # type: ignore[arg-type]
            window._camera_repository.create(camera)

        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]

        assert (window.video_grid.rows, window.video_grid.columns) == expected_shape
        assert len(window.video_grid.tiles()) == channel_count
        window.video_grid.clear()

    def test_more_cameras_than_cells_opens_the_first_sixteen(
        self, window: MainWindow
    ) -> None:
        nvr = window._nvr_repository.create(
            Nvr(name="Grande", host="192.0.2.60", username="admin", channel_count=20)
        )
        set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
        for camera in generate_missing_channel_cameras(nvr.id, 20):  # type: ignore[arg-type]
            window._camera_repository.create(camera)

        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]

        assert len(window.video_grid.tiles()) == 16
        assert "não cabem" in window.statusBar().currentMessage()
        window.video_grid.clear()


class TestStreamSelection:
    def test_mosaic_cells_use_the_substream(self, window: MainWindow) -> None:
        """Sub stream keeps 16 cells affordable in bandwidth and CPU."""
        window.video_grid.set_grid_shape(2, 2)
        window._open_camera_at(camera_ids(window)[0], 0)

        tile = window.video_grid.tile_at(0)
        assert tile is not None
        assert tile.url.endswith("/Streaming/Channels/102")  # channel 1, sub
        window.video_grid.clear()

    def test_single_cell_grid_honours_the_nvr_default(self, window: MainWindow) -> None:
        window.video_grid.set_grid_shape(1, 1)
        window._open_camera_at(camera_ids(window)[0], 0)

        tile = window.video_grid.tile_at(0)
        assert tile is not None
        assert tile.url.endswith("/Streaming/Channels/101")  # channel 1, main
        window.video_grid.clear()

    def test_single_cell_grid_honours_a_sub_default(self, window: MainWindow) -> None:
        nvr = window._nvr_repository.list_all()[0]
        nvr.default_stream = StreamType.SUB
        window._nvr_repository.update(nvr)

        window.video_grid.set_grid_shape(1, 1)
        window._open_camera_at(camera_ids(window)[0], 0)

        tile = window.video_grid.tile_at(0)
        assert tile is not None
        assert tile.url.endswith("/Streaming/Channels/102")
        window.video_grid.clear()

    def test_url_never_leaks_into_the_tile_title(self, window: MainWindow) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        tile = window.video_grid.tile_at(0)
        assert tile is not None
        assert TEST_PASSWORD not in tile.title
        window.video_grid.clear()


class TestGridShapeSelector:
    def test_toolbar_selection_reshapes_the_grid(self, window: MainWindow) -> None:
        window.layout_selector.setCurrentText("3x3")
        assert (window.video_grid.rows, window.video_grid.columns) == (3, 3)
        assert window.video_grid.cell_count == 9

    def test_shrinking_closes_streams_outside_the_new_shape(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        window.layout_selector.setCurrentText("3x3")
        window._open_camera_at(camera_ids(window)[0], 8)
        window.video_grid.tile_at(8)._connect()  # type: ignore[union-attr]
        player = fake_instance.players[-1]

        window.layout_selector.setCurrentText("2x2")

        assert window.video_grid.tile_at(8) is None
        assert player.released is True


class TestDropRouting:
    def test_dropped_camera_opens_in_the_target_cell(self, window: MainWindow) -> None:
        window.video_grid.cameraDropped.emit(camera_ids(window)[2], 3)

        tile = window.video_grid.tile_at(3)
        assert tile is not None
        assert tile.title == "Canal 3"
        assert tile.camera_id == camera_ids(window)[2]
        window.video_grid.clear()


class TestShutdown:
    def test_closing_the_window_releases_every_player(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        for position, camera_id in enumerate(camera_ids(window)[:2]):
            window._open_camera_at(camera_id, position)
            window.video_grid.tile_at(position)._connect()  # type: ignore[union-attr]

        window.close()

        assert len(fake_instance.players) == 2
        assert all(p.released for p in fake_instance.players)
