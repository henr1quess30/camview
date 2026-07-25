"""Tests for the vlc.Instance singleton and per-stream playback options.

The instance getter is exercised against the real libvlc install (this
project requires VLC as a system dependency — see README), so these
tests confirm actual singleton behavior rather than mocking it away.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from camview.services.stream_manager import (
    PlaybackOptions,
    displayed_picture_count,
    get_vlc_instance,
    reset_vlc_instance,
)


@pytest.fixture(autouse=True)
def _reset_instance() -> Iterator[None]:
    reset_vlc_instance()
    yield
    reset_vlc_instance()


class TestGetVlcInstance:
    def test_returns_an_instance(self) -> None:
        instance = get_vlc_instance()
        assert instance is not None

    def test_returns_the_same_instance_on_repeated_calls(self) -> None:
        first = get_vlc_instance()
        second = get_vlc_instance()
        assert first is second

    def test_reset_forces_a_new_instance(self) -> None:
        first = get_vlc_instance()
        reset_vlc_instance()
        second = get_vlc_instance()
        assert first is not second


class TestDisplayedPictureCount:
    """The watchdog's only evidence — and it must never raise."""

    def test_unknown_when_the_player_has_no_media(self) -> None:
        class PlayerWithoutMedia:
            def get_media(self) -> None:
                return None

        assert displayed_picture_count(PlayerWithoutMedia()) is None

    def test_unknown_instead_of_raising_on_a_foreign_player(self) -> None:
        """A fake or unexpected player must read as 'unknown', not crash."""

        class NotAPlayer:
            pass

        assert displayed_picture_count(NotAPlayer()) is None

    def test_unknown_when_libvlc_refuses_the_stats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vlc

        class PlayerWithMedia:
            def get_media(self) -> object:
                return object()

        monkeypatch.setattr(vlc, "libvlc_media_get_stats", lambda *_a: False)

        assert displayed_picture_count(PlayerWithMedia()) is None

    def test_reads_the_counter_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vlc

        class PlayerWithMedia:
            def get_media(self) -> object:
                return object()

        def fill(_media: object, stats_ref: object) -> bool:
            stats_ref._obj.displayed_pictures = 4242  # type: ignore[attr-defined]
            return True

        monkeypatch.setattr(vlc, "libvlc_media_get_stats", fill)

        assert displayed_picture_count(PlayerWithMedia()) == 4242


class TestPlaybackOptions:
    def test_default_options_match_spec(self) -> None:
        options = PlaybackOptions()
        assert options.to_media_options() == [
            "network-caching=300",
            "rtsp-tcp",
            "no-audio",
        ]

    def test_udp_transport_omits_rtsp_tcp_flag(self) -> None:
        options = PlaybackOptions(rtsp_transport_tcp=False)
        assert "rtsp-tcp" not in options.to_media_options()

    def test_audio_enabled_omits_no_audio_flag(self) -> None:
        options = PlaybackOptions(mute_audio=False)
        assert "no-audio" not in options.to_media_options()

    def test_custom_network_caching(self) -> None:
        options = PlaybackOptions(network_caching_ms=1000)
        assert "network-caching=1000" in options.to_media_options()
