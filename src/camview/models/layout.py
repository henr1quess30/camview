"""Layout and LayoutItem models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from camview.models.camera import StreamType


@dataclass(slots=True)
class Layout:
    """A saved mosaic layout's grid shape. Positions live in ``LayoutItem``."""

    name: str
    rows: int
    columns: int
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
