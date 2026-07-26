"""Layout and LayoutItem models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from camview.models.camera import StreamType


@dataclass(slots=True)
class Layout:
    """A saved mosaic layout's arrangement. Positions live in ``LayoutItem``.

    ``shape`` names the arrangement ("2x2", "1+5"). ``rows``/``columns``
    describe the base grid and are kept because layouts saved before
    named shapes existed only have those.
    """

    name: str
    rows: int
    columns: int
    shape: str = ""
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class LayoutItem:
    """A camera assigned to one grid position within a layout."""

    layout_id: int
    camera_id: int
    position: int
    stream_type: StreamType
    id: int | None = None
