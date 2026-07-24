"""Nvr model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from camview.models.camera import StreamType


@dataclass(slots=True)
class Nvr:
    """A registered NVR/DVR device.

    The password is deliberately not a field here — it is stored via
    :mod:`camview.services.credentials` (keyring), keyed by the NVR's id,
    never persisted alongside this record.
    """

    name: str
    host: str
    username: str
    channel_count: int
    rtsp_port: int = 554
    default_stream: StreamType = StreamType.MAIN
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
