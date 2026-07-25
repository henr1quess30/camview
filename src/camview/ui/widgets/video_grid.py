"""VideoGrid — the mosaic: a grid of cells, each empty or holding a VideoTile.

Cells are addressed by a single ``position`` integer, ``row * columns + col``,
counting left-to-right then top-to-bottom. Positions stay stable while the
grid shape is unchanged, which is what layouts persist in Phase 5.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from camview.models.camera import StreamType
from camview.ui.widgets.device_tree import CAMERA_MIME_TYPE
from camview.ui.widgets.video_tile import GRID_POSITION_MIME_TYPE, VideoTile

logger = logging.getLogger(__name__)

#: Grid shapes offered in the toolbar, as (rows, columns). Ordered smallest
#: first, which :func:`smallest_shape_for` relies on.
GRID_SHAPES: dict[str, tuple[int, int]] = {
    "1x1": (1, 1),
    "2x2": (2, 2),
    "3x3": (3, 3),
    "4x4": (4, 4),
}


def smallest_shape_for(camera_count: int) -> tuple[int, int]:
    """Smallest offered grid that fits ``camera_count`` cameras.

    Falls back to the largest shape when there are more cameras than
    cells; the caller decides what to do with the overflow.
    """
    for rows, columns in GRID_SHAPES.values():
        if rows * columns >= camera_count:
            return rows, columns
    return GRID_SHAPES["4x4"]


class _EmptyCell(QFrame):
    """Placeholder shown in a cell with no camera."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        label = QLabel("—")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: palette(mid);")
        layout.addWidget(label)


class VideoGrid(QWidget):
    """A mosaic of video tiles with drag-and-drop placement and maximize."""

    #: Emitted when a camera is dropped on a cell: (camera_id, position).
    cameraDropped = Signal(int, int)

    def __init__(
        self, rows: int = 2, columns: int = 2, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.rows = rows
        self.columns = columns
        self._tiles: dict[int, VideoTile] = {}
        self._empty_cells: dict[int, _EmptyCell] = {}
        self._selected_position: int | None = None
        self._maximized_position: int | None = None
        #: Stream each tile used before being maximized, to restore it after.
        self._mosaic_stream_types: dict[int, StreamType] = {}

        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._rebuild()

    # ------------------------------------------------------------------
    # Grid shape
    # ------------------------------------------------------------------

    @property
    def cell_count(self) -> int:
        return self.rows * self.columns

    def set_grid_shape(self, rows: int, columns: int) -> None:
        """Change the grid shape, closing any streams that no longer fit."""
        if (rows, columns) == (self.rows, self.columns):
            return

        self.restore()
        for position in sorted(self._tiles):
            if position >= rows * columns:
                self.remove_tile(position)

        self.rows = rows
        self.columns = columns
        self._rebuild()

    def _rebuild(self) -> None:
        """Re-place every cell widget according to the current shape."""
        while self._layout.count():
            self._layout.takeAt(0)

        for cell in self._empty_cells.values():
            cell.deleteLater()
        self._empty_cells.clear()

        for position in range(self.cell_count):
            row, column = divmod(position, self.columns)
            tile = self._tiles.get(position)
            if tile is not None:
                self._layout.addWidget(tile, row, column)
                tile.show()
            else:
                empty = _EmptyCell(self)
                self._empty_cells[position] = empty
                self._layout.addWidget(empty, row, column)
                empty.show()

        for row in range(self.rows):
            self._layout.setRowStretch(row, 1)
        for column in range(self.columns):
            self._layout.setColumnStretch(column, 1)

    # ------------------------------------------------------------------
    # Tiles
    # ------------------------------------------------------------------

    def first_free_position(self) -> int | None:
        """Lowest position with no tile, or ``None`` when the grid is full."""
        for position in range(self.cell_count):
            if position not in self._tiles:
                return position
        return None

    def tile_at(self, position: int) -> VideoTile | None:
        return self._tiles.get(position)

    def tiles(self) -> dict[int, VideoTile]:
        return dict(self._tiles)

    def mosaic_stream_type(self, position: int) -> StreamType | None:
        """Which stream cell ``position`` uses *in the mosaic*.

        While a cell is maximized its live stream is bumped to
        :attr:`StreamType.MAIN`, which is a viewing state, not the cell's
        configuration. Saving a layout must record the mosaic stream, so
        loading it back doesn't turn every cell into a main stream.
        """
        tile = self._tiles.get(position)
        if tile is None:
            return None
        return self._mosaic_stream_types.get(position, tile.stream_type)

    def place_tile(self, position: int, tile: VideoTile) -> None:
        """Put ``tile`` at ``position``, replacing whatever was there."""
        if not 0 <= position < self.cell_count:
            raise ValueError(f"position {position} outside the current grid")

        self.remove_tile(position)
        tile.setParent(self)
        tile.grid_position = position
        tile.closeRequested.connect(lambda: self._on_tile_close_requested(tile))
        tile.doubleClicked.connect(lambda: self._on_tile_double_clicked(tile))
        tile.clicked.connect(lambda: self._on_tile_clicked(tile))
        self._tiles[position] = tile
        self._rebuild()

        if self._selected_position is None:
            self.select(position)

    def remove_tile(self, position: int) -> None:
        """Stop, release and discard the tile at ``position``, if any."""
        tile = self._tiles.pop(position, None)
        if tile is None:
            return

        if self._maximized_position == position:
            self._maximized_position = None
        if self._selected_position == position:
            self._selected_position = None
        self._mosaic_stream_types.pop(position, None)

        tile.close_stream()
        tile.setParent(None)
        tile.deleteLater()
        self._rebuild()

    def clear(self) -> None:
        """Remove every tile, releasing all players."""
        for position in list(self._tiles):
            self.remove_tile(position)

    def move_tile(self, source: int, target: int) -> None:
        """Move a tile to another cell, swapping with the tile already there."""
        if source == target:
            return

        source_tile = self._tiles.get(source)
        if source_tile is None:
            return

        target_tile = self._tiles.get(target)
        self._tiles[target] = source_tile
        source_tile.grid_position = target

        if target_tile is not None:
            self._tiles[source] = target_tile
            target_tile.grid_position = source
        else:
            self._tiles.pop(source, None)

        if self._selected_position == source:
            self._selected_position = target
        elif self._selected_position == target:
            self._selected_position = source

        self._rebuild()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    @property
    def selected_position(self) -> int | None:
        return self._selected_position

    def select(self, position: int | None) -> None:
        self._selected_position = position
        for pos, tile in self._tiles.items():
            tile.set_selected(pos == position)

    # ------------------------------------------------------------------
    # Maximize / restore
    # ------------------------------------------------------------------

    @property
    def maximized_position(self) -> int | None:
        return self._maximized_position

    def is_maximized(self) -> bool:
        return self._maximized_position is not None

    def maximize(self, position: int) -> None:
        """Expand one tile to fill the whole grid, hiding the others.

        The tile is re-added to the *same* layout rather than reparented:
        reparenting recreates the underlying X11 window, which would break
        the window handle libVLC is already rendering into.
        """
        tile = self._tiles.get(position)
        if tile is None:
            return

        self._maximized_position = position
        # Filling the window with a mosaic-sized substream looks soft, so
        # step up to the main stream while the cell is enlarged.
        self._mosaic_stream_types.setdefault(position, tile.stream_type)
        tile.set_stream_type(StreamType.MAIN)

        while self._layout.count():
            self._layout.takeAt(0)
        for other_position, other in self._tiles.items():
            if other_position != position:
                other.hide()
        for empty in self._empty_cells.values():
            empty.hide()

        self._layout.addWidget(tile, 0, 0, self.rows, self.columns)
        tile.show()
        self.select(position)

    def restore(self) -> None:
        """Undo :meth:`maximize`, showing the full mosaic again."""
        if self._maximized_position is None:
            return

        restored = self._tiles.get(self._maximized_position)
        previous = self._mosaic_stream_types.pop(self._maximized_position, None)
        if restored is not None and previous is not None:
            restored.set_stream_type(previous)

        self._maximized_position = None
        for tile in self._tiles.values():
            tile.show()
        self._rebuild()

    def toggle_maximized(self, position: int) -> None:
        if self._maximized_position == position:
            self.restore()
        else:
            self.maximize(position)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _on_tile_close_requested(self, tile: VideoTile) -> None:
        if tile.grid_position is not None:
            self.remove_tile(tile.grid_position)

    def _on_tile_double_clicked(self, tile: VideoTile) -> None:
        if tile.grid_position is not None:
            self.toggle_maximized(tile.grid_position)

    def _on_tile_clicked(self, tile: VideoTile) -> None:
        if tile.grid_position is not None:
            self.select(tile.grid_position)

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key() == Qt.Key.Key_Escape and self.is_maximized():
            self.restore()
            event.accept()
            return
        super().keyPressEvent(event)

    def position_at(self, point) -> int | None:  # type: ignore[no-untyped-def]
        """Which cell contains ``point`` (widget coordinates)?"""
        for position in range(self.cell_count):
            row, column = divmod(position, self.columns)
            item = self._layout.itemAtPosition(row, column)
            if item is not None and item.geometry().contains(point):
                return position
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasFormat(CAMERA_MIME_TYPE) or mime.hasFormat(
            GRID_POSITION_MIME_TYPE
        ):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        mime = event.mimeData()
        if mime.hasFormat(CAMERA_MIME_TYPE) or mime.hasFormat(
            GRID_POSITION_MIME_TYPE
        ):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        position = self.position_at(event.position().toPoint())
        if position is None:
            event.ignore()
            return

        mime = event.mimeData()
        if mime.hasFormat(GRID_POSITION_MIME_TYPE):
            source = int(bytes(mime.data(GRID_POSITION_MIME_TYPE)).decode("ascii"))
            self.move_tile(source, position)
            event.acceptProposedAction()
        elif mime.hasFormat(CAMERA_MIME_TYPE):
            camera_id = int(bytes(mime.data(CAMERA_MIME_TYPE)).decode("ascii"))
            self.cameraDropped.emit(camera_id, position)
            event.acceptProposedAction()
        else:
            event.ignore()
