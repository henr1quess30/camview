"""Registered device model (an NVR/DVR, or a standalone camera)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from camview.models.camera import StreamType


class DeviceType(str, Enum):
    """What kind of equipment a record stands for.

    A standalone camera is still stored as a device with one channel —
    the RTSP path is the same on Hikvision gear either way — but the UI
    treats it differently: no channel count to fill in, a single row in
    the sidebar instead of a folder, and opening it fills the window
    rather than taking one mosaic cell.
    """

    NVR = "nvr"
    CAMERA = "camera"


@dataclass(slots=True)
class Nvr:
    """A registered device: an NVR/DVR, or a single camera.

    The name is historical — this is the ``nvrs`` table — but a record
    with ``device_type == DeviceType.CAMERA`` represents one camera.

    The password is deliberately not a field here — it is stored via
    :mod:`camview.services.credentials` (keyring), keyed by the device's
    id, never persisted alongside this record.
    """

    name: str
    host: str
    username: str
    channel_count: int
    rtsp_port: int = 554
    default_stream: StreamType = StreamType.MAIN
    device_type: DeviceType = DeviceType.NVR
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_camera(self) -> bool:
        return self.device_type is DeviceType.CAMERA
