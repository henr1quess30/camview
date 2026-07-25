"""User-facing settings, and how they map onto the ``settings`` table.

Settings are stored one key per row as text, so this module owns both the
typed representation (:class:`AppSettings`) and the parsing that turns
stored text back into it. Parsing never raises: a value that has been
hand-edited into nonsense falls back to the default, because a bad row
must not stop the app from starting.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from camview.models.camera import StreamType

#: Keys used in the ``settings`` table, one per :class:`AppSettings` field.
SETTINGS_KEY_PREFIX = "app/"


class MosaicStream(str, Enum):
    """Which stream mosaic cells should use.

    ``NVR_DEFAULT`` defers to each NVR's own configured default, which is
    what a 1x1 grid has always done.
    """

    SUB = "sub"
    MAIN = "main"
    NVR_DEFAULT = "nvr_default"

    def resolve(self, nvr_default: StreamType) -> StreamType:
        if self is MosaicStream.MAIN:
            return StreamType.MAIN
        if self is MosaicStream.SUB:
            return StreamType.SUB
        return nvr_default


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Everything the settings screen can change.

    Defaults match the behaviour shipped in Phases 3–6, so an empty
    ``settings`` table and a fresh install behave identically.
    """

    #: Buffer libVLC keeps before playing. Lower is snappier, less tolerant.
    network_caching_ms: int = 300
    #: RTSP over TCP; UDP is lighter but loses frames on busy networks.
    rtsp_transport_tcp: bool = True
    mute_audio: bool = True
    reconnect_enabled: bool = True
    #: Ceiling for the reconnect backoff schedule (2s, 5s, 10s, ...).
    max_reconnect_delay_s: int = 30
    #: Stream used by mosaic cells; the per-cell menu still overrides it.
    mosaic_stream: MosaicStream = MosaicStream.SUB
    start_maximized: bool = False
    restore_last_layout: bool = True
    #: ``None`` means "wherever config.get_default_log_dir() points".
    log_dir: Path | None = None

    # Keyboard shortcuts, as Qt key sequence strings ("Right", "Ctrl+0").
    # Defaults are chosen to work while a cell fills the window: stepping
    # through cameras with the arrow keys and zooming with +/-/0.
    shortcut_next_camera: str = "Right"
    shortcut_previous_camera: str = "Left"
    shortcut_zoom_in: str = "+"
    shortcut_zoom_out: str = "-"
    shortcut_zoom_reset: str = "0"

    def backoff_schedule(self) -> tuple[int, ...]:
        """Reconnect delays, cut off at :attr:`max_reconnect_delay_s`."""
        full = (2, 5, 10, 30, 60)
        capped = tuple(
            delay for delay in full if delay < self.max_reconnect_delay_s
        )
        return (*capped, max(1, self.max_reconnect_delay_s))


def _to_bool(value: str, default: bool) -> bool:
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    return default


def _to_shortcut(value: str | None, default: str) -> str:
    """Keep a stored shortcut only if it still says something.

    Validity is Qt's business (the settings dialog uses a key-sequence
    editor); an empty row here just means "back to the default".
    """
    return value.strip() if value and value.strip() else default


def _to_int(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def settings_from_mapping(stored: dict[str, str]) -> AppSettings:
    """Build :class:`AppSettings` from raw ``settings`` rows."""
    defaults = AppSettings()

    def raw(field_name: str) -> str | None:
        return stored.get(f"{SETTINGS_KEY_PREFIX}{field_name}")

    mosaic_raw = raw("mosaic_stream")
    try:
        mosaic = (
            MosaicStream(mosaic_raw) if mosaic_raw else defaults.mosaic_stream
        )
    except ValueError:
        mosaic = defaults.mosaic_stream

    log_dir_raw = raw("log_dir")

    return AppSettings(
        network_caching_ms=_to_int(
            raw("network_caching_ms") or "",
            defaults.network_caching_ms,
            minimum=0,
            maximum=10_000,
        ),
        rtsp_transport_tcp=_to_bool(
            raw("rtsp_transport_tcp") or "", defaults.rtsp_transport_tcp
        ),
        mute_audio=_to_bool(raw("mute_audio") or "", defaults.mute_audio),
        reconnect_enabled=_to_bool(
            raw("reconnect_enabled") or "", defaults.reconnect_enabled
        ),
        max_reconnect_delay_s=_to_int(
            raw("max_reconnect_delay_s") or "",
            defaults.max_reconnect_delay_s,
            minimum=1,
            maximum=600,
        ),
        mosaic_stream=mosaic,
        start_maximized=_to_bool(
            raw("start_maximized") or "", defaults.start_maximized
        ),
        restore_last_layout=_to_bool(
            raw("restore_last_layout") or "", defaults.restore_last_layout
        ),
        log_dir=Path(log_dir_raw) if log_dir_raw else None,
        shortcut_next_camera=_to_shortcut(
            raw("shortcut_next_camera"), defaults.shortcut_next_camera
        ),
        shortcut_previous_camera=_to_shortcut(
            raw("shortcut_previous_camera"), defaults.shortcut_previous_camera
        ),
        shortcut_zoom_in=_to_shortcut(
            raw("shortcut_zoom_in"), defaults.shortcut_zoom_in
        ),
        shortcut_zoom_out=_to_shortcut(
            raw("shortcut_zoom_out"), defaults.shortcut_zoom_out
        ),
        shortcut_zoom_reset=_to_shortcut(
            raw("shortcut_zoom_reset"), defaults.shortcut_zoom_reset
        ),
    )


def settings_to_mapping(settings: AppSettings) -> dict[str, str]:
    """Serialize :class:`AppSettings` back to ``settings`` rows."""
    rows: dict[str, str] = {}
    for name, value in asdict(settings).items():
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, MosaicStream):
            text = value.value
        elif isinstance(value, Path):
            text = str(value)
        else:
            text = str(value)
        rows[f"{SETTINGS_KEY_PREFIX}{name}"] = text
    return rows
