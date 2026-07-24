"""Tests for the vlc.Instance singleton and per-stream playback options.

The instance getter is exercised against the real libvlc install (this
project requires VLC as a system dependency — see README), so these
tests confirm actual singleton behavior rather than mocking it away.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from camview.services.stream_manager import PlaybackOptions, get_vlc_instance, reset_vlc_instance


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
