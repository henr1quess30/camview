"""Owns the single global vlc.Instance, built lazily on first use.

Importing this module never requires a working libVLC install: python-vlc
itself raises at *import* time if libvlc can't be found (it resolves the
shared library eagerly at module load), so ``import vlc`` only happens
inside :func:`get_vlc_instance`, not at this module's top level.
Construction failures surface as :class:`VlcUnavailableError`, which
callers turn into the "VLC not installed" user-facing message.

``--vout=xcb_x11`` is baked into the instance args deliberately: this app
runs under XWayland (see ``camview.__main__``) so libVLC can embed video
into a Qt widget. Letting libVLC auto-negotiate was observed on this
setup to pick the OpenGL vout with a VAAPI chroma converter, which fails
(``vaDeriveImage: operation failed``) and takes the whole video output
down with it. ``xcb_x11`` is plain X11 output with no GL/VAAPI
dependency, and rendered a real 1080p H.265 camera stream reliably in
testing.

``--avcodec-hw=none`` forces software decoding, since VAAPI/VDPAU
hardware decode of H.265 is unavailable on older GPUs (verified on a
Radeon HD 5000-series here). This should become a user setting in
Phase 7 — machines with working hardware decode will want it enabled,
especially for large mosaics.

Known benign noise: libVLC 3.x probes hardware paths on startup
regardless of these options and logs ``glconv_vaapi_x11 gl error:
vaDeriveImage: operation failed`` / ``video output creation failed``
before falling back. Playback verified working despite these messages
(a real 1080p H.265 camera frame was captured via
``video_take_snapshot``), so they are noise, not failure.

Note that on Arch the required plugins do NOT come with ``libvlc``
alone — ``vlc-plugin-live555`` (RTSP transport), ``vlc-plugin-ffmpeg``
(H.264/H.265 decoding) and ``vlc-plugins-video-output`` are separate
packages. Installing the ``vlc`` package pulls them all in; see README.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import vlc

logger = logging.getLogger(__name__)

DEFAULT_VLC_ARGS: tuple[str, ...] = ("--vout=xcb_x11", "--avcodec-hw=none")

_instance: "vlc.Instance | None" = None
_instance_lock = threading.Lock()


class VlcUnavailableError(Exception):
    """Raised when libVLC cannot be initialized (not installed or broken)."""


def get_vlc_instance(vlc_args: tuple[str, ...] = DEFAULT_VLC_ARGS) -> "vlc.Instance":
    """Return the process-wide ``vlc.Instance``, creating it on first call."""
    global _instance
    with _instance_lock:
        if _instance is not None:
            return _instance

        try:
            import vlc
        except (NotImplementedError, OSError) as exc:
            raise VlcUnavailableError(
                "libVLC não foi encontrado. Instale o pacote 'vlc' do sistema "
                "(sudo pacman -S vlc)."
            ) from exc

        instance = vlc.Instance(list(vlc_args))
        if instance is None:
            raise VlcUnavailableError("Não foi possível inicializar o libVLC.")

        _instance = instance
        logger.info("libVLC instance created (args=%s)", vlc_args)
        return _instance


def reset_vlc_instance() -> None:
    """Drop the cached global instance. For tests only."""
    global _instance
    with _instance_lock:
        _instance = None


@dataclass(frozen=True, slots=True)
class PlaybackOptions:
    """Low-latency playback tuning, applied per-stream (not instance-wide).

    Defaults match the spec: 300ms network caching, RTSP over TCP, no
    audio. Kept at the media level (not baked into the Instance) so a
    settings change in Phase 7 only affects new/reconnecting streams.
    """

    network_caching_ms: int = 300
    rtsp_transport_tcp: bool = True
    mute_audio: bool = True

    def to_media_options(self) -> list[str]:
        options = [f"network-caching={self.network_caching_ms}"]
        if self.rtsp_transport_tcp:
            options.append("rtsp-tcp")
        if self.mute_audio:
            options.append("no-audio")
        return options
