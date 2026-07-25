"""Load and save :class:`AppSettings` through the ``settings`` table."""

from __future__ import annotations

import logging

from camview.database.repositories import SettingsRepository
from camview.models.settings import (
    SETTINGS_KEY_PREFIX,
    AppSettings,
    settings_from_mapping,
    settings_to_mapping,
)
from camview.services.stream_manager import PlaybackOptions

logger = logging.getLogger(__name__)


def load_settings(repository: SettingsRepository) -> AppSettings:
    """Read the stored settings, falling back to defaults for anything missing."""
    return settings_from_mapping(repository.get_all())


def save_settings(repository: SettingsRepository, settings: AppSettings) -> None:
    """Persist ``settings``, removing rows for values that are back to default.

    Deleting rather than writing an empty value matters for optional
    settings such as the log directory: absence is what "use the default
    location" means, so a stale row would keep overriding it.
    """
    rows = settings_to_mapping(settings)
    for key, value in rows.items():
        repository.set(key, value)

    stale = [
        key
        for key in repository.get_all()
        if key.startswith(SETTINGS_KEY_PREFIX) and key not in rows
    ]
    for key in stale:
        repository.delete(key)


def playback_options_for(settings: AppSettings) -> PlaybackOptions:
    """Playback tuning derived from user settings, applied per stream."""
    return PlaybackOptions(
        network_caching_ms=settings.network_caching_ms,
        rtsp_transport_tcp=settings.rtsp_transport_tcp,
        mute_audio=settings.mute_audio,
    )
