"""Camera model and the main/sub stream-type enum."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class StreamType(str, Enum):
    """Which Hikvision stream to use: full-quality main, or lightweight sub."""

    MAIN = "main"
    SUB = "sub"


@dataclass(slots=True)
class Camera:
    """A single channel on an NVR."""

    nvr_id: int
    channel_number: int
    name: str
    enabled: bool = True
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
