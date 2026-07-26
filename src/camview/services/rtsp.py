"""Hikvision RTSP URL generation and channel auto-generation.

Numbering follows the Hikvision convention: channel N, main stream ->
N01, sub stream -> N02 (channel 1 main = 101, channel 1 sub = 102,
channel 2 main = 201, ...).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from urllib.parse import quote

from camview.models.camera import Camera, StreamType

_STREAM_SUFFIX: dict[StreamType, int] = {StreamType.MAIN: 1, StreamType.SUB: 2}


def build_channel_url(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    channel_number: int,
    stream_type: StreamType,
) -> str:
    """Build a Hikvision RTSP URL for one channel/stream.

    The password is only ever embedded in the URL returned here — it is
    never persisted to SQLite (see :mod:`camview.services.credentials`).
    """
    if channel_number < 1:
        raise ValueError(f"channel_number must be >= 1, got {channel_number}")

    channel_code = channel_number * 100 + _STREAM_SUFFIX[stream_type]
    user = quote(username, safe="")
    pwd = quote(password, safe="")
    return f"rtsp://{user}:{pwd}@{host}:{port}/Streaming/Channels/{channel_code}"


#: Names a device hands out when nobody has named the camera yet. Anything
#: matching these carries no information, so it is always safe to replace.
_GENERIC_NAME_RE = re.compile(
    r"^(canal|camera|câmera|ipcamera|ip camera|chn)\s*\d*$", re.IGNORECASE
)


def is_generic_camera_name(name: str) -> bool:
    """Is this a placeholder like ``Canal 3`` or ``Camera 01``?"""
    return bool(_GENERIC_NAME_RE.match(name.strip()))


def camera_names_to_update(
    cameras: Iterable[Camera], discovered_names: Mapping[int, str]
) -> list[Camera]:
    """Cameras whose stored name the device disagrees with, updated.

    The device is the authority on what a camera is called — its name is
    what the operator typed into the recorder. The one thing never done
    is the reverse: a real name is never replaced by a placeholder, so a
    recorder that forgot its labels cannot wipe good ones here.
    """
    updated: list[Camera] = []
    for camera in cameras:
        discovered = discovered_names.get(camera.channel_number, "").strip()
        if not discovered or discovered == camera.name:
            continue
        if is_generic_camera_name(discovered):
            continue
        camera.name = discovered
        updated.append(camera)
    return updated


def generate_missing_channel_cameras(
    nvr_id: int,
    channel_count: int,
    existing_channel_numbers: Iterable[int] = (),
) -> list[Camera]:
    """Build one ``Camera`` per channel number not already in ``existing_channel_numbers``.

    Used both to auto-populate all channels when an NVR is first
    registered (empty ``existing_channel_numbers``) and, when editing an
    NVR whose channel count increased, to add only the newly available
    channels without touching cameras that already exist.
    """
    if channel_count < 1:
        raise ValueError(f"channel_count must be >= 1, got {channel_count}")

    existing = set(existing_channel_numbers)
    return [
        Camera(nvr_id=nvr_id, channel_number=n, name=f"Canal {n}")
        for n in range(1, channel_count + 1)
        if n not in existing
    ]
