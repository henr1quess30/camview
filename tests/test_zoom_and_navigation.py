"""Tests for digital zoom and camera-to-camera navigation.

Both exist so a camera can be inspected without dismantling the mosaic:
zoom into a corner of the picture, and step to the next camera while one
still fills the window.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QKeySequence, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from camview.database.repositories import SettingsRepository
from camview.models.camera import StreamType
from camview.models.nvr import Nvr
from camview.models.settings import AppSettings
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.services.settings import save_settings
from camview.ui.main_window import MainWindow
from camview.ui.widgets.grid_shapes import GRID_SHAPES
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


AREA = (400, 300)


def resize_area(tile: VideoTile, width: int, height: int) -> None:
    """Resize the visible video area the way a real layout pass would.

    Qt does not deliver resize events to hidden widgets, and these tests
    never show a window, so the event is dispatched by hand — that keeps
    the resize hook itself under test instead of assuming it fired.
    """
    old = tile._video_area.size()
    tile._video_area.resize(width, height)
    tile._video_area.resizeEvent(QResizeEvent(QSize(width, height), old))


def zoomable_tile(qapp: QApplication) -> VideoTile:
    """A tile whose visible video area has a known size.

    Zoom is pure widget geometry now — no libVLC involved — so the video
    widget's rectangle inside that area is the whole truth.
    """
    tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
    resize_area(tile, *AREA)
    return tile


class TestZoom:
    def test_a_new_cell_is_not_zoomed(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)

        assert tile.zoom == MIN_ZOOM
        assert tile.video_widget.geometry().size().toTuple() == AREA
        tile.close_stream()

    def test_zooming_in_enlarges_the_picture_proportionally(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """Both axes by the same factor — that is what keeps it from
        stretching, which the earlier crop-based version got wrong."""
        tile = zoomable_tile(qapp)

        tile.zoom_by(2.0)

        geometry = tile.video_widget.geometry()
        assert (geometry.width(), geometry.height()) == (AREA[0] * 2, AREA[1] * 2)
        tile.close_stream()

    def test_the_point_under_the_cursor_stays_put(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """The complaint that started this rewrite: it zoomed elsewhere."""
        tile = zoomable_tile(qapp)
        anchor = QPoint(100, 75)
        before = tile.video_widget.geometry()
        u = (anchor.x() - before.x()) / before.width()
        v = (anchor.y() - before.y()) / before.height()

        tile.zoom_by(2.0, anchor)

        after = tile.video_widget.geometry()
        assert after.x() + u * after.width() == pytest.approx(anchor.x(), abs=1)
        assert after.y() + v * after.height() == pytest.approx(anchor.y(), abs=1)
        tile.close_stream()

    def test_the_picture_never_leaves_a_gap(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)

        tile.zoom_by(2.0, QPoint(AREA[0], AREA[1]))  # bottom-right corner

        geometry = tile.video_widget.geometry()
        assert geometry.x() <= 0 and geometry.y() <= 0
        assert geometry.right() >= AREA[0] - 1
        assert geometry.bottom() >= AREA[1] - 1
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
        assert tile.video_widget.geometry().topLeft() == QPoint(0, 0)
        tile.close_stream()

    def test_reset_returns_to_the_full_picture(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.zoom_by(3.0, QPoint(20, 250))

        tile.reset_zoom()

        assert tile.zoom == MIN_ZOOM
        assert tile.video_widget.geometry().size().toTuple() == AREA
        tile.close_stream()

    def test_resizing_the_cell_keeps_the_zoom(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.zoom_by(2.0)

        resize_area(tile, 800, 600)

        geometry = tile.video_widget.geometry()
        assert (geometry.width(), geometry.height()) == (1600, 1200)
        tile.close_stream()

    def test_zoom_is_reapplied_when_a_stream_starts(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """A stream switch can resize the video widget under us."""
        tile = zoomable_tile(qapp)
        tile.zoom_by(2.0)
        tile.video_widget.setGeometry(0, 0, *AREA)  # as if reset by a switch

        tile._on_playing()

        assert tile.video_widget.geometry().width() == AREA[0] * 2
        tile.close_stream()


class TestPan:
    def test_dragging_moves_the_zoomed_picture(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.zoom_by(2.0)
        before = tile.video_widget.geometry().topLeft()

        tile.pan_by(-30, -20)

        after = tile.video_widget.geometry().topLeft()
        assert after == before + QPoint(-30, -20)
        tile.close_stream()

    def test_panning_stops_at_the_edge(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.zoom_by(2.0)

        tile.pan_by(10_000, 10_000)

        assert tile.video_widget.geometry().topLeft() == QPoint(0, 0)
        tile.close_stream()

    def test_panning_does_nothing_at_normal_zoom(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """At 1x there is nothing hidden to drag into view."""
        tile = zoomable_tile(qapp)

        tile.pan_by(-50, -50)

        assert tile.video_widget.geometry().topLeft() == QPoint(0, 0)
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

        tile.wheelEvent(self._wheel(tile, 1, QPoint(200, 150)))

        assert tile.zoom == pytest.approx(ZOOM_STEP)
        tile.close_stream()

    def test_scrolling_down_zooms_out(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = zoomable_tile(qapp)
        tile.zoom_by(4.0)

        tile.wheelEvent(self._wheel(tile, -1, QPoint(200, 150)))

        assert tile.zoom == pytest.approx(4.0 / ZOOM_STEP)
        tile.close_stream()


class TestGridNavigation:
    @pytest.fixture
    def grid(self, qapp: QApplication, fake_instance: FakeInstance) -> VideoGrid:
        widget = VideoGrid(shape=GRID_SHAPES["2x2"])
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
        empty = VideoGrid(shape=GRID_SHAPES["2x2"])

        empty.step(1)  # must not raise

        assert empty.selected_position is None

    def test_zoom_applies_to_the_focused_cell(self, grid: VideoGrid) -> None:
        grid.select(1)

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
