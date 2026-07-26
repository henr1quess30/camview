"""Mosaic shapes, uniform and with a highlighted cell.

A shape is a base grid plus the rectangles its cells occupy. Uniform
shapes (2x2, 3x3, ...) have one rectangle per grid square; the "1+N"
shapes give one camera a large rectangle and line the rest around it,
which is how most control rooms actually watch a site — one view that
matters, the others in the corner of the eye.

Cell ``position`` is the index into :attr:`GridShape.cells`, and those
are listed in reading order, so for uniform shapes it is still
``row * columns + column`` — the numbering saved layouts already use.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A cell rectangle: row, column, how many rows it spans, how many columns.
CellRect = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class GridShape:
    """One mosaic arrangement."""

    label: str
    rows: int
    columns: int
    cells: tuple[CellRect, ...]

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def is_uniform(self) -> bool:
        """True when every cell is a single grid square."""
        return all(
            row_span == 1 and column_span == 1
            for _, _, row_span, column_span in self.cells
        )


def uniform(size: int) -> GridShape:
    """A plain ``size x size`` grid."""
    return GridShape(
        label=f"{size}x{size}",
        rows=size,
        columns=size,
        cells=tuple(
            (row, column, 1, 1) for row in range(size) for column in range(size)
        ),
    )


def _big_plus_border(base: int, span: int) -> tuple[CellRect, ...]:
    """One ``span x span`` cell at the top left, the rest along the edges.

    Reading order is kept — big cell first, then the right-hand column,
    then the bottom row — so positions stay stable and predictable.
    """
    cells: list[CellRect] = [(0, 0, span, span)]
    cells += [(row, span, 1, 1) for row in range(span)]
    cells += [(span, column, 1, 1) for column in range(base)]
    return tuple(cells)


def _big_centred(base: int, span: int) -> tuple[CellRect, ...]:
    """A ``span x span`` cell in the middle, ringed by single cells."""
    offset = (base - span) // 2
    occupied = {
        (row, column)
        for row in range(offset, offset + span)
        for column in range(offset, offset + span)
    }
    cells: list[CellRect] = []
    for row in range(base):
        for column in range(base):
            if (row, column) == (offset, offset):
                cells.append((row, column, span, span))
            elif (row, column) not in occupied:
                cells.append((row, column, 1, 1))
    return tuple(cells)


#: Every shape the toolbar offers, in the order it offers them.
GRID_SHAPES: dict[str, GridShape] = {
    shape.label: shape
    for shape in (
        uniform(1),
        uniform(2),
        uniform(3),
        uniform(4),
        GridShape("1+5", 3, 3, _big_plus_border(3, 2)),
        GridShape("1+7", 4, 4, _big_plus_border(4, 3)),
        GridShape("1+12", 4, 4, _big_centred(4, 2)),
    )
}

#: Uniform shapes only, smallest first — what auto-fitting picks from.
_UNIFORM_SHAPES: tuple[GridShape, ...] = tuple(
    shape for shape in GRID_SHAPES.values() if shape.is_uniform
)


def shape_for_label(label: str) -> GridShape | None:
    return GRID_SHAPES.get(label)


def smallest_shape_for(camera_count: int) -> GridShape:
    """Smallest uniform grid that fits ``camera_count`` cameras.

    Uniform on purpose: opening a whole recorder is "show me everything",
    where singling one camera out would be an arbitrary choice.
    """
    for shape in _UNIFORM_SHAPES:
        if shape.cell_count >= camera_count:
            return shape
    return _UNIFORM_SHAPES[-1]


def shape_for_grid(rows: int, columns: int) -> GridShape:
    """The uniform shape of that size, built on the fly if it is not offered.

    Used when loading a layout saved before shapes had names.
    """
    if rows == columns:
        offered = GRID_SHAPES.get(f"{rows}x{rows}")
        if offered is not None:
            return offered
    return GridShape(
        label=f"{rows}x{columns}",
        rows=rows,
        columns=columns,
        cells=tuple(
            (row, column, 1, 1) for row in range(rows) for column in range(columns)
        ),
    )
