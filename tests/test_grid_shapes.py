"""Tests for mosaic shapes, uniform and with a highlighted cell.

The invariants that matter: every cell rectangle stays inside the base
grid, none overlap, and position numbering keeps meaning the same thing
it did before non-uniform shapes existed — otherwise every saved layout
would silently reshuffle.
"""

from __future__ import annotations

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication

from camview.models.camera import StreamType
from camview.ui.widgets.grid_shapes import (
    GRID_SHAPES,
    GridShape,
    shape_for_grid,
    shape_for_label,
    smallest_shape_for,
    uniform,
)
from camview.ui.widgets.video_grid import VideoGrid
from camview.ui.widgets.video_tile import VideoTile

STREAM_URLS = {
    StreamType.SUB: "rtsp://192.0.2.10/Streaming/Channels/102",
    StreamType.MAIN: "rtsp://192.0.2.10/Streaming/Channels/101",
}

ALL_SHAPES = list(GRID_SHAPES.values())


def covered_squares(shape: GridShape) -> list[tuple[int, int]]:
    return [
        (row + dr, column + dc)
        for row, column, row_span, column_span in shape.cells
        for dr in range(row_span)
        for dc in range(column_span)
    ]


class TestShapeIntegrity:
    @pytest.mark.parametrize("shape", ALL_SHAPES, ids=lambda s: s.label)
    def test_cells_stay_inside_the_base_grid(self, shape: GridShape) -> None:
        for row, column in covered_squares(shape):
            assert 0 <= row < shape.rows
            assert 0 <= column < shape.columns

    @pytest.mark.parametrize("shape", ALL_SHAPES, ids=lambda s: s.label)
    def test_no_two_cells_overlap(self, shape: GridShape) -> None:
        squares = covered_squares(shape)
        assert len(squares) == len(set(squares))

    @pytest.mark.parametrize("shape", ALL_SHAPES, ids=lambda s: s.label)
    def test_the_grid_is_fully_used(self, shape: GridShape) -> None:
        """No dead squares: a gap in the mosaic would just look broken."""
        assert len(covered_squares(shape)) == shape.rows * shape.columns

    def test_uniform_shapes_are_recognised_as_such(self) -> None:
        assert GRID_SHAPES["3x3"].is_uniform is True
        assert GRID_SHAPES["1+5"].is_uniform is False

    def test_uniform_positions_still_read_row_by_row(self) -> None:
        """Saved layouts number cells this way; changing it would reshuffle
        every layout on disk."""
        shape = uniform(3)
        assert shape.cells[4] == (1, 1, 1, 1)  # position 4 = row 1, column 1

    @pytest.mark.parametrize(
        ("label", "big_cell"),
        [("1+5", (0, 0, 2, 2)), ("1+7", (0, 0, 3, 3)), ("1+12", (1, 1, 2, 2))],
    )
    def test_the_highlighted_cell_comes_first_where_expected(
        self, label: str, big_cell: tuple[int, int, int, int]
    ) -> None:
        shape = GRID_SHAPES[label]
        assert big_cell in shape.cells

    def test_the_big_cell_of_1_plus_12_is_centred(self) -> None:
        shape = GRID_SHAPES["1+12"]
        big = next(c for c in shape.cells if c[2] > 1)
        assert big == (1, 1, 2, 2)


class TestShapeLookup:
    def test_labels_resolve(self) -> None:
        assert shape_for_label("1+5") is GRID_SHAPES["1+5"]

    def test_an_unknown_label_resolves_to_nothing(self) -> None:
        assert shape_for_label("7x7") is None

    def test_auto_fit_only_picks_uniform_shapes(self) -> None:
        """Opening a whole recorder means 'show me everything'; singling
        one camera out would be an arbitrary choice."""
        for count in range(1, 17):
            assert smallest_shape_for(count).is_uniform

    @pytest.mark.parametrize(
        ("count", "label"), [(1, "1x1"), (4, "2x2"), (6, "3x3"), (16, "4x4")]
    )
    def test_auto_fit_picks_the_smallest_that_fits(
        self, count: int, label: str
    ) -> None:
        assert smallest_shape_for(count).label == label

    def test_more_cameras_than_cells_falls_back_to_the_largest(self) -> None:
        assert smallest_shape_for(40).label == "4x4"

    def test_a_layout_saved_before_shapes_had_names_still_loads(self) -> None:
        shape = shape_for_grid(3, 3)
        assert shape.label == "3x3"
        assert shape.cell_count == 9

    def test_an_unoffered_grid_size_is_built_on_the_fly(self) -> None:
        shape = shape_for_grid(2, 3)
        assert shape.cell_count == 6
        assert shape.rows == 2 and shape.columns == 3


class TestGridWithShapes:
    def test_the_grid_reports_the_shape_cell_count(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["1+5"])
        assert grid.cell_count == 6
        assert (grid.rows, grid.columns) == (3, 3)

    def test_the_big_cell_spans_in_the_layout(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["1+5"])
        grid.place_tile(0, VideoTile(title="Canal 1", stream_urls=STREAM_URLS))

        index = grid._layout.indexOf(grid.tile_at(0))
        _, _, row_span, column_span = grid._layout.getItemPosition(index)

        assert (row_span, column_span) == (2, 2)
        grid.clear()

    def test_switching_shape_keeps_the_cameras_that_still_fit(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["3x3"])
        for position in (0, 5, 8):
            grid.place_tile(
                position, VideoTile(title=f"Canal {position}", stream_urls=STREAM_URLS)
            )

        grid.set_shape(GRID_SHAPES["1+5"])  # six cells

        assert sorted(grid.tiles()) == [0, 5], "position 8 no longer exists"
        grid.clear()

    def test_switching_to_the_same_shape_changes_nothing(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        grid = VideoGrid(shape=GRID_SHAPES["1+7"])
        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
        grid.place_tile(0, tile)

        grid.set_shape(GRID_SHAPES["1+7"])

        assert grid.tile_at(0) is tile
        grid.clear()

    def test_drops_land_on_the_big_cell(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """A point inside the large rectangle must map to position 0, not to
        whichever small cell would sit there in a uniform grid."""
        grid = VideoGrid(shape=GRID_SHAPES["1+5"])
        grid.resize(300, 300)
        grid.show()
        QApplication.processEvents()

        item = grid._layout.itemAtPosition(0, 0)
        assert grid.position_at(item.geometry().center()) == 0
        grid.close()
