"""Tests for VideoGrid: placement, swapping, resizing, maximize and cleanup.

Tiles are built with a fake libVLC instance (see ``fake_instance``) so no
RTSP source is needed and player release can be asserted directly.
"""

from __future__ import annotations

import pytest
from fakes import FakeInstance
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from camview.models.camera import StreamType
from camview.ui.widgets.grid_shapes import GRID_SHAPES
from camview.ui.widgets.video_grid import GRID_SHAPES, VideoGrid
from camview.ui.widgets.video_tile import VideoTile


STREAM_URLS = {
    StreamType.MAIN: "rtsp://example.invalid/Streaming/Channels/101",
    StreamType.SUB: "rtsp://example.invalid/Streaming/Channels/102",
}


def make_tile(name: str = "Canal", camera_id: int = 1) -> VideoTile:
    """A tile that has not connected yet (its deferred timer has not fired)."""
    return VideoTile(
        title=name,
        stream_urls=STREAM_URLS,
        stream_type=StreamType.SUB,
        camera_id=camera_id,
    )


def place_connected_tile(
    grid: VideoGrid, position: int, name: str = "Canal"
) -> VideoTile:
    """Place a tile and let it create its player.

    Mirrors production ordering: ``VideoTile.__init__`` defers connecting
    via ``QTimer.singleShot(0)``, so the tile is always parented into the
    grid *before* libVLC is handed its window id. Tests have no running
    event loop, so the connect is triggered explicitly here.
    """
    tile = make_tile(name)
    grid.place_tile(position, tile)
    tile._connect()
    return tile


class TestGridShape:
    def test_default_shape_is_2x2(self, qapp: QApplication) -> None:
        grid = VideoGrid()
        assert (grid.rows, grid.columns) == (2, 2)
        assert grid.cell_count == 4

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("1x1", 1),
            ("2x2", 4),
            ("3x3", 9),
            ("4x4", 16),
            ("1+5", 6),
            ("1+7", 8),
            ("1+12", 13),
        ],
    )
    def test_all_offered_shapes(
        self, qapp: QApplication, label: str, expected: int
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES[label])
        assert grid.cell_count == expected

    def test_growing_the_grid_keeps_existing_tiles(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["2x2"])
        grid.place_tile(0, make_tile("A"))
        grid.place_tile(3, make_tile("B"))

        grid.set_grid_shape(3, 3)

        assert grid.tile_at(0) is not None
        assert grid.tile_at(3) is not None
        assert grid.cell_count == 9

    def test_shrinking_closes_tiles_that_no_longer_fit(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["3x3"])
        place_connected_tile(grid, 0, "A")
        place_connected_tile(grid, 8, "B")
        dropped_player = fake_instance.players[-1]

        grid.set_grid_shape(2, 2)

        assert grid.tile_at(0) is not None
        assert grid.tile_at(8) is None
        assert dropped_player.stopped is True
        assert dropped_player.released is True


class TestPlacement:
    def test_first_free_position_scans_in_order(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["2x2"])
        assert grid.first_free_position() == 0

        grid.place_tile(0, make_tile())
        assert grid.first_free_position() == 1

        grid.place_tile(2, make_tile())
        assert grid.first_free_position() == 1

    def test_first_free_position_is_none_when_full(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["1x1"])
        grid.place_tile(0, make_tile())
        assert grid.first_free_position() is None

    def test_place_tile_sets_grid_position(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile()
        grid.place_tile(2, tile)
        assert tile.grid_position == 2

    def test_placing_over_an_occupied_cell_releases_the_old_player(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        place_connected_tile(grid, 0, "Old")
        old_player = fake_instance.players[-1]

        grid.place_tile(0, make_tile("New"))

        assert old_player.released is True
        assert grid.tile_at(0).title == "New"  # type: ignore[union-attr]

    def test_position_outside_the_grid_is_rejected(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["2x2"])
        with pytest.raises(ValueError):
            grid.place_tile(4, make_tile())

    def test_first_placed_tile_becomes_selected(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile()
        grid.place_tile(1, tile)
        assert grid.selected_position == 1
        assert tile.is_selected() is True


class TestRemoval:
    def test_remove_tile_releases_the_player(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        place_connected_tile(grid, 0)
        player = fake_instance.players[-1]

        grid.remove_tile(0)

        assert grid.tile_at(0) is None
        assert player.stopped is True
        assert player.released is True

    def test_remove_empty_cell_is_a_no_op(self, qapp: QApplication) -> None:
        grid = VideoGrid()
        grid.remove_tile(0)  # must not raise

    def test_clear_releases_every_player(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        place_connected_tile(grid, 0, "A")
        place_connected_tile(grid, 1, "B")

        grid.clear()

        assert grid.tiles() == {}
        assert len(fake_instance.players) == 2
        assert all(p.released for p in fake_instance.players)

    def test_close_button_removes_the_tile_from_the_grid(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile()
        grid.place_tile(0, tile)

        tile.closeRequested.emit()

        assert grid.tile_at(0) is None


class TestMoveAndSwap:
    def test_move_to_empty_cell(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile("A")
        grid.place_tile(0, tile)

        grid.move_tile(0, 3)

        assert grid.tile_at(0) is None
        assert grid.tile_at(3) is tile
        assert tile.grid_position == 3

    def test_move_onto_occupied_cell_swaps_both(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        first = make_tile("A")
        second = make_tile("B")
        grid.place_tile(0, first)
        grid.place_tile(1, second)

        grid.move_tile(0, 1)

        assert grid.tile_at(1) is first
        assert grid.tile_at(0) is second
        assert first.grid_position == 1
        assert second.grid_position == 0

    def test_swap_does_not_release_any_player(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """Repositioning must not interrupt playback."""
        grid = VideoGrid()
        place_connected_tile(grid, 0, "A")
        place_connected_tile(grid, 1, "B")

        grid.move_tile(0, 1)

        assert len(fake_instance.players) == 2
        assert not any(p.released for p in fake_instance.players)

    def test_move_to_same_position_is_a_no_op(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile()
        grid.place_tile(0, tile)
        grid.move_tile(0, 0)
        assert grid.tile_at(0) is tile

    def test_move_from_empty_cell_is_a_no_op(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile()
        grid.place_tile(1, tile)
        grid.move_tile(0, 1)
        assert grid.tile_at(1) is tile


class TestSelection:
    def test_selecting_marks_only_one_tile(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        first = make_tile("A")
        second = make_tile("B")
        grid.place_tile(0, first)
        grid.place_tile(1, second)

        grid.select(1)

        assert first.is_selected() is False
        assert second.is_selected() is True
        assert grid.selected_position == 1

    def test_clicking_a_tile_selects_it(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        grid.place_tile(0, make_tile("A"))
        second = make_tile("B")
        grid.place_tile(1, second)

        second.clicked.emit()

        assert grid.selected_position == 1

    def test_selection_follows_a_swap(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile("A")
        grid.place_tile(0, tile)
        grid.select(0)

        grid.move_tile(0, 2)

        assert grid.selected_position == 2


class TestMaximize:
    def test_maximize_hides_other_tiles(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        grid.resize(400, 400)
        grid.show()
        first = make_tile("A")
        second = make_tile("B")
        grid.place_tile(0, first)
        grid.place_tile(1, second)

        grid.maximize(0)

        assert grid.is_maximized()
        assert grid.maximized_position == 0
        assert first.isVisible() is True
        assert second.isVisible() is False
        grid.close()

    def test_maximize_does_not_release_players(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """Maximizing must not reparent/recreate the VLC window."""
        grid = VideoGrid()
        first = place_connected_tile(grid, 0, "A")
        place_connected_tile(grid, 1, "B")
        window_before = first._player.xwindow  # type: ignore[union-attr]

        grid.maximize(0)
        grid.restore()

        assert len(fake_instance.players) == 2
        assert not any(p.released for p in fake_instance.players)
        # Same X11 window handle throughout: libVLC keeps rendering into it.
        assert first._player.xwindow == window_before  # type: ignore[union-attr]
        assert int(first.video_widget.winId()) == window_before

    def test_restore_shows_everything_again(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        grid.resize(400, 400)
        grid.show()
        first = make_tile("A")
        second = make_tile("B")
        grid.place_tile(0, first)
        grid.place_tile(1, second)

        grid.maximize(0)
        grid.restore()

        assert not grid.is_maximized()
        assert first.isVisible() is True
        assert second.isVisible() is True
        grid.close()

    def test_double_click_toggles_maximize(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = make_tile()
        grid.place_tile(0, tile)

        tile.doubleClicked.emit()
        assert grid.maximized_position == 0

        tile.doubleClicked.emit()
        assert grid.is_maximized() is False

    def test_maximizing_an_empty_cell_is_a_no_op(self, qapp: QApplication) -> None:
        grid = VideoGrid()
        grid.maximize(0)
        assert grid.is_maximized() is False

    def test_restore_when_not_maximized_is_a_no_op(self, qapp: QApplication) -> None:
        grid = VideoGrid()
        grid.restore()  # must not raise

    def test_removing_the_maximized_tile_clears_maximized_state(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        grid.place_tile(0, make_tile())
        grid.maximize(0)

        grid.remove_tile(0)

        assert grid.is_maximized() is False

    def test_changing_grid_shape_restores_first(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["2x2"])
        grid.place_tile(0, make_tile())
        grid.maximize(0)

        grid.set_grid_shape(3, 3)

        assert grid.is_maximized() is False


class TestPositionAt:
    def test_maps_points_to_the_right_cells(
        self, qapp: QApplication
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["2x2"])
        grid.resize(400, 400)
        grid.show()
        qapp.processEvents()

        # Top-left quadrant is position 0, bottom-right is position 3.
        assert grid.position_at(QPoint(50, 50)) == 0
        assert grid.position_at(QPoint(350, 350)) == 3
        grid.close()

    def test_point_outside_any_cell_returns_none(self, qapp: QApplication) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["2x2"])
        grid.resize(400, 400)
        grid.show()
        qapp.processEvents()

        assert grid.position_at(QPoint(5000, 5000)) is None
        grid.close()


class TestStreamSwitchOnMaximize:
    """A mosaic-sized substream looks soft filling the window, so maximizing
    steps up to the main stream and restoring steps back down."""

    def test_maximize_switches_to_main_stream(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = place_connected_tile(grid, 0)
        assert tile.stream_type is StreamType.SUB

        grid.maximize(0)

        assert tile.stream_type is StreamType.MAIN
        assert tile.url == STREAM_URLS[StreamType.MAIN]

    def test_restore_returns_to_the_mosaic_stream(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = place_connected_tile(grid, 0)

        grid.maximize(0)
        grid.restore()

        assert tile.stream_type is StreamType.SUB
        assert tile.url == STREAM_URLS[StreamType.SUB]

    def test_switching_reuses_the_same_player(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """Swapping media must not leak or recreate the VLC player."""
        grid = VideoGrid()
        place_connected_tile(grid, 0)

        grid.maximize(0)
        grid.restore()

        assert len(fake_instance.players) == 1
        assert fake_instance.players[0].released is False

    def test_maximize_plays_the_main_stream_url(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        place_connected_tile(grid, 0)

        grid.maximize(0)

        url, _options = fake_instance.players[0].media
        assert url == STREAM_URLS[StreamType.MAIN]

    def test_a_main_stream_tile_is_left_alone(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """1x1 grids already use the NVR default, so there is nothing to switch."""
        grid = VideoGrid(shape=GRID_SHAPES["1x1"])
        tile = VideoTile(
            title="Canal", stream_urls=STREAM_URLS, stream_type=StreamType.MAIN
        )
        grid.place_tile(0, tile)
        tile._connect()

        grid.maximize(0)
        assert tile.stream_type is StreamType.MAIN

        grid.restore()
        assert tile.stream_type is StreamType.MAIN

    def test_tile_without_a_main_url_stays_on_its_substream(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        tile = VideoTile(
            title="Canal",
            stream_urls={StreamType.SUB: STREAM_URLS[StreamType.SUB]},
            stream_type=StreamType.SUB,
        )
        grid.place_tile(0, tile)
        tile._connect()

        grid.maximize(0)

        assert tile.stream_type is StreamType.SUB

    def test_removing_a_maximized_tile_forgets_its_saved_stream(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid()
        place_connected_tile(grid, 0)
        grid.maximize(0)

        grid.remove_tile(0)
        new_tile = place_connected_tile(grid, 0)
        grid.maximize(0)
        grid.restore()

        assert new_tile.stream_type is StreamType.SUB
