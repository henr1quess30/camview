"""Tests for digital zoom and camera-to-camera navigation.

Both exist so a camera can be inspected without dismantling the mosaic:
zoom into a corner of the picture, and step to the next camera while one
still fills the window.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance, FakePlayer
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeySequence, QWheelEvent
from PySide6.QtWidgets import QApplication

from camview.database.repositories import SettingsRepository
from camview.models.camera import StreamType
from camview.models.nvr import Nvr
from camview.models.settings import AppSettings
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.services.settings import save_settings
from camview.ui.main_window import MainWindow
from camview.ui.widgets.video_grid import VideoGrid
from camview.ui.widgets.video_tile import (
    MAX_ZOOM,
    MIN_ZOOM,
    ZOOM_STEP,
    VideoTile,
)

TEST_PASSWORD = "test-password"
STREAM_URLS = {
    StreamType.SUB: "rtsp://192.0.2.10/Streaming/Channels/102",
    StreamType.MAIN: "rtsp://192.0.2.10/Streaming/Channels/101",
}


class ZoomablePlayer(FakePlayer):
    """Fake player that also reports a picture size and records crop calls."""

    def __init__(self, size: tuple[int, int] = (1920, 1080)) -> None:
        super().__init__()
        self._size = size
        self.crops: list[str] = []

    def video_get_size(self, _index: int) -> tuple[int, int]:
        return self._size

    def video_set_crop_geometry(self, geometry: str) -> None:
        self.crops.append(geometry)


def zoomable_tile(qapp: QApplication, size: tuple[int, int] = (1920, 1080)) -> VideoTile:
    tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
    tile._player = ZoomablePlayer(size)  # type: ignore[assignment]
    return tile


def parse_crop(geometry: str) -> tuple[int, int, int, int]:
    size, _, offset = geometry.partition("+")
    width, height = (int(part) for part in size.split("x"))
    left, top = (int(part) for part in offset.split("+"))
    return width, height, left, top


class TestZoom:
    def test_a_new_cell_is_not_zoomed(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        assert tile.zoom == MIN_ZOOM
        tile.close_stream()

    def test_zooming_in_crops_the_picture(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)

        tile.zoom_by(2.0)

        width, height, _, _ = parse_crop(tile._player.crops[-1])  # type: ignore[union-attr]
        assert (width, height) == (960, 540)
        tile.close_stream()

    def test_zoom_centres_on_the_requested_point(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """Zooming toward the cursor is what makes the wheel feel right."""
        tile = zoomable_tile(qapp)

        tile.zoom_by(2.0, center=(0.25, 0.25))

        width, height, left, top = parse_crop(tile._player.crops[-1])  # type: ignore[union-attr]
        assert (left, top) == (1920 // 4 - width // 2, 1080 // 4 - height // 2)
        tile.close_stream()

    def test_the_crop_never_leaves_the_picture(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)

        tile.zoom_by(2.0, center=(1.0, 1.0))

        width, height, left, top = parse_crop(tile._player.crops[-1])  # type: ignore[union-attr]
        assert left + width <= 1920
        assert top + height <= 1080
        tile.close_stream()

    def test_zoom_is_capped(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)

        for _ in range(50):
            tile.zoom_by(2.0)

        assert tile.zoom == MAX_ZOOM
        tile.close_stream()

    def test_zooming_out_stops_at_the_whole_picture(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.zoom_by(4.0)

        for _ in range(50):
            tile.zoom_by(0.5)

        assert tile.zoom == MIN_ZOOM
        assert tile._player.crops[-1] == "", "no crop means the full frame"  # type: ignore[union-attr]
        tile.close_stream()

    def test_reset_returns_to_the_full_picture(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.zoom_by(3.0, center=(0.2, 0.8))

        tile.reset_zoom()

        assert tile.zoom == MIN_ZOOM
        assert tile._player.crops[-1] == ""  # type: ignore[union-attr]
        tile.close_stream()

    def test_zoom_survives_a_reconnect(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """New media starts uncropped, so the zoom has to be reapplied."""
        tile = zoomable_tile(qapp)
        tile.zoom_by(2.0)
        tile._player.crops.clear()  # type: ignore[union-attr]

        tile._on_playing()

        assert tile._player.crops, "zoom was not restored after reconnect"  # type: ignore[union-attr]
        tile.close_stream()

    def test_a_player_without_a_picture_yet_is_left_alone(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp, size=(0, 0))

        tile.zoom_by(2.0)  # must not raise

        assert tile._player.crops == []  # type: ignore[union-attr]
        tile.close_stream()

    def test_a_player_that_cannot_report_its_size_is_left_alone(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """Zoom is a convenience; it must never break playback."""

        class BrokenPlayer(FakePlayer):
            def video_get_size(self, _index: int) -> tuple[int, int]:
                raise RuntimeError("no video track")

        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
        tile._player = BrokenPlayer()  # type: ignore[assignment]

        tile.zoom_by(2.0)  # must not raise

        assert tile.zoom > MIN_ZOOM
        tile.close_stream()


class TestWheelZoom:
    @staticmethod
    def _wheel(tile: VideoTile, notches: int, at: QPoint) -> QWheelEvent:
        return QWheelEvent(
            QPointF(at),
            QPointF(at),
            QPoint(0, 0),
            QPoint(0, 120 * notches),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

    def test_scrolling_up_zooms_in(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.resize(400, 300)

        tile.wheelEvent(self._wheel(tile, 1, QPoint(200, 150)))

        assert tile.zoom == pytest.approx(ZOOM_STEP)
        tile.close_stream()

    def test_scrolling_down_zooms_out(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.resize(400, 300)
        tile.zoom_by(4.0)

        tile.wheelEvent(self._wheel(tile, -1, QPoint(200, 150)))

        assert tile.zoom == pytest.approx(4.0 / ZOOM_STEP)
        tile.close_stream()


class TestGridNavigation:
    @pytest.fixture
    def grid(self, qapp: QApplication, fake_instance: FakeInstance) -> VideoGrid:
        widget = VideoGrid(rows=2, columns=2)
        for position in (0, 1, 3):
            widget.place_tile(
                position, VideoTile(title=f"Canal {position}", stream_urls=STREAM_URLS)
            )
        return widget

    def test_stepping_moves_the_selection_over_occupied_cells_only(
        self, grid: VideoGrid
    ) -> None:
        grid.select(0)

        grid.step(1)
        assert grid.selected_position == 1

        grid.step(1)
        assert grid.selected_position == 3, "cell 2 is empty and must be skipped"
        grid.clear()

    def test_stepping_wraps_around(self, grid: VideoGrid) -> None:
        grid.select(3)

        grid.step(1)

        assert grid.selected_position == 0
        grid.clear()

    def test_stepping_backwards(self, grid: VideoGrid) -> None:
        grid.select(0)

        grid.step(-1)

        assert grid.selected_position == 3
        grid.clear()

    def test_stepping_while_maximized_swaps_which_camera_fills_the_window(
        self, grid: VideoGrid
    ) -> None:
        grid.maximize(0)

        grid.step(1)

        assert grid.maximized_position == 1
        assert grid.is_maximized(), "must stay maximized, not drop back to the mosaic"
        grid.clear()

    def test_stepping_shows_the_next_camera_immediately(
        self, grid: VideoGrid
    ) -> None:
        """No stream switch on arrival: switching restarts playback, and a
        few seconds of black per camera makes stepping useless."""
        grid.maximize(0)

        grid.step(1)

        assert grid.tile_at(1).stream_type is StreamType.SUB  # type: ignore[union-attr]
        assert grid._upgrade_timer.isActive(), "the upgrade must still be pending"
        grid.clear()

    def test_settling_on_a_camera_raises_it_to_the_main_stream(
        self, grid: VideoGrid
    ) -> None:
        grid.maximize(0)
        grid.step(1)

        grid._upgrade_timer.timeout.emit()  # stands in for the delay elapsing

        assert grid.tile_at(1).stream_type is StreamType.MAIN  # type: ignore[union-attr]
        assert grid.tile_at(0).stream_type is StreamType.SUB, (  # type: ignore[union-attr]
            "the camera left behind goes back to the mosaic stream"
        )
        grid.clear()

    def test_stepping_past_a_camera_cancels_its_upgrade(
        self, grid: VideoGrid
    ) -> None:
        """Walking the wall must not restart every camera on the way."""
        grid.maximize(0)

        grid.step(1)
        grid.step(1)
        grid._upgrade_timer.timeout.emit()

        assert grid.tile_at(1).stream_type is StreamType.SUB  # type: ignore[union-attr]
        assert grid.tile_at(3).stream_type is StreamType.MAIN  # type: ignore[union-attr]
        grid.clear()

    def test_double_click_still_upgrades_at_once(self, grid: VideoGrid) -> None:
        """Picking a camera deliberately is not the same as passing by it."""
        grid.maximize(0)

        assert grid.tile_at(0).stream_type is StreamType.MAIN  # type: ignore[union-attr]
        grid.clear()

    def test_stepping_an_empty_grid_does_nothing(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        empty = VideoGrid(rows=2, columns=2)

        empty.step(1)  # must not raise

        assert empty.selected_position is None

    def test_zoom_applies_to_the_focused_cell(self, grid: VideoGrid) -> None:
        grid.select(1)
        grid.tile_at(1)._player = ZoomablePlayer()  # type: ignore[union-attr]

        grid.zoom_focused(2.0)

        assert grid.tile_at(1).zoom == 2.0  # type: ignore[union-attr]
        assert grid.tile_at(0).zoom == MIN_ZOOM  # type: ignore[union-attr]
        grid.clear()


class TestConfigurableShortcuts:
    @pytest.fixture
    def window(
        self,
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

    @staticmethod
    def _shortcuts(window: MainWindow) -> list[str]:
        return [
            action.shortcut().toString() for action in window._shortcut_actions
        ]

    def test_defaults_are_registered_on_the_window(self, window: MainWindow) -> None:
        assert QKeySequence("Right").toString() in self._shortcuts(window)
        assert QKeySequence("Left").toString() in self._shortcuts(window)

    def test_configured_keys_replace_the_defaults(
        self, window: MainWindow, db_connection: sqlite3.Connection
    ) -> None:
        save_settings(
            SettingsRepository(db_connection),
            AppSettings(shortcut_next_camera="Ctrl+Right"),
        )

        reopened = MainWindow(connection=db_connection)

        assert QKeySequence("Ctrl+Right").toString() in self._shortcuts(reopened)
        assert QKeySequence("Right").toString() not in self._shortcuts(reopened)

    def test_an_unusable_shortcut_is_skipped_not_fatal(
        self, window: MainWindow
    ) -> None:
        window._settings = AppSettings(shortcut_next_camera="")

        window._build_shortcuts()  # must not raise

        assert QKeySequence("Left").toString() in self._shortcuts(window)

    def test_stepping_reports_the_camera_it_landed_on(
        self, window: MainWindow
    ) -> None:
        nvr = window._nvr_repository.list_all()[0]
        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]

        window._step_camera(1)

        assert window.statusBar().currentMessage() == "Canal 2"
        window.video_grid.clear()

    def test_shortcuts_are_rebuilt_when_settings_change(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from PySide6.QtWidgets import QDialog

        from camview.ui.dialogs.settings_dialog import SettingsDialog

        def fake_exec(dialog: SettingsDialog) -> int:
            dialog.shortcut_edits["shortcut_next_camera"].setKeySequence(
                QKeySequence("F8")
            )
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(SettingsDialog, "exec", fake_exec)

        window._edit_settings()

        assert QKeySequence("F8").toString() in self._shortcuts(window)
