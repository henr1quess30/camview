"""Tests for VideoTile's connection/reconnection state machine.

Uses a fake libVLC instance/player/event-manager so these tests don't
depend on a real RTSP source. ``vlc.EventType`` constants are still the
real ones (libvlc is a required system dependency for this project — see
README), which keeps the fake's event dispatch faithful to how libvlc
actually calls back.
"""

from __future__ import annotations

from time import monotonic

import pytest
import vlc
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication

from camview.models.camera import StreamType
from camview.services.stream_manager import PlaybackOptions, VlcUnavailableError
from camview.ui.widgets.video_tile import (
    STALL_TIMEOUT_S,
    ConnectionStatus,
    VideoTile,
)


SUB_URL = "rtsp://example.invalid/Streaming/Channels/102"
MAIN_URL = "rtsp://example.invalid/Streaming/Channels/101"
STREAM_URLS = {StreamType.SUB: SUB_URL, StreamType.MAIN: MAIN_URL}


def make_tile(qapp: QApplication, url: str = SUB_URL) -> VideoTile:
    return VideoTile(title="Canal 1", stream_urls={StreamType.SUB: url})


class TestInitialState:
    def test_starts_in_connecting_status_before_connect_runs(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        assert tile.status == ConnectionStatus.CONNECTING
        tile.close_stream()


class TestConnect:
    def test_creates_one_player_and_starts_playback(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()

        assert len(fake_instance.players) == 1
        player = fake_instance.players[0]
        assert player.played is True
        assert player.xwindow == int(tile.video_widget.winId())
        # libVLC must not grab input, or Qt never sees double-click/drag/Esc.
        assert player.mouse_input is False
        assert player.key_input is False
        tile.close_stream()

    def test_reconnecting_reuses_the_same_player(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        tile._connect()

        assert len(fake_instance.players) == 1
        tile.close_stream()

    def test_uses_provided_playback_options_in_media_options(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = VideoTile(
            title="Canal 1",
            stream_urls={StreamType.SUB: SUB_URL},
            playback_options=PlaybackOptions(network_caching_ms=500, mute_audio=False),
        )
        tile._connect()

        url, options = fake_instance.players[0].media
        assert url == SUB_URL
        assert "network-caching=500" in options
        assert "no-audio" not in options
        tile.close_stream()

    def test_vlc_unavailable_sets_error_status_without_creating_a_player(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_unavailable() -> None:
            raise VlcUnavailableError("libVLC não foi encontrado.")

        monkeypatch.setattr(
            "camview.ui.widgets.video_tile.get_vlc_instance", raise_unavailable
        )
        tile = make_tile(qapp)
        tile._connect()

        assert tile.status == ConnectionStatus.ERROR
        assert tile._player is None
        tile.close_stream()


class TestPlayingEvent:
    def test_playing_event_sets_status_and_resets_backoff(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        fake_instance.players[0].event_manager_obj.trigger(vlc.EventType.MediaPlayerPlaying)

        assert tile.status == ConnectionStatus.PLAYING
        tile.close_stream()


class TestErrorAndReconnect:
    def test_error_event_sets_error_status_and_schedules_reconnect(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        fake_instance.players[0].event_manager_obj.trigger(
            vlc.EventType.MediaPlayerEncounteredError
        )

        assert tile.status == ConnectionStatus.ERROR
        assert tile._reconnect_timer.isActive()
        assert tile._reconnect_timer.interval() == 2000  # first backoff step: 2s
        tile.close_stream()

    def test_end_reached_event_also_triggers_reconnect(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        fake_instance.players[0].event_manager_obj.trigger(
            vlc.EventType.MediaPlayerEndReached
        )

        assert tile.status == ConnectionStatus.ERROR
        assert tile._reconnect_timer.isActive()
        tile.close_stream()

    def test_consecutive_errors_escalate_backoff_delay(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        event_manager = fake_instance.players[0].event_manager_obj

        event_manager.trigger(vlc.EventType.MediaPlayerEncounteredError)
        assert tile._reconnect_timer.interval() == 2000

        event_manager.trigger(vlc.EventType.MediaPlayerEncounteredError)
        assert tile._reconnect_timer.interval() == 5000
        tile.close_stream()

    def test_reaching_playing_alone_does_not_reset_the_backoff(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        """A stream that flaps must keep backing off, not retry every 2s."""
        tile = make_tile(qapp)
        tile._connect()
        event_manager = fake_instance.players[0].event_manager_obj

        event_manager.trigger(vlc.EventType.MediaPlayerEncounteredError)
        event_manager.trigger(vlc.EventType.MediaPlayerPlaying)

        assert tile.status == ConnectionStatus.PLAYING
        assert tile._backoff.next_delay_seconds() == 5
        tile.close_stream()

    def test_backoff_resets_after_a_healthy_stretch_of_playback(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        event_manager = fake_instance.players[0].event_manager_obj

        event_manager.trigger(vlc.EventType.MediaPlayerEncounteredError)
        event_manager.trigger(vlc.EventType.MediaPlayerPlaying)
        assert tile._healthy_timer.isActive()
        tile._healthy_timer.timeout.emit()  # stands in for 30s of playback

        assert tile._backoff.next_delay_seconds() == 2
        tile.close_stream()


class TestStallWatchdog:
    """A stalled RTSP stream stays 'playing' and reports no error.

    The frame counter is the only evidence, so these tests drive it
    directly instead of going through libVLC.
    """

    @staticmethod
    def _playing_tile(
        qapp: QApplication, fake_instance: FakeInstance
    ) -> tuple[VideoTile, object]:
        tile = make_tile(qapp)
        tile.show()
        tile._connect()
        event_manager = fake_instance.players[0].event_manager_obj
        event_manager.trigger(vlc.EventType.MediaPlayerPlaying)
        return tile, event_manager

    @staticmethod
    def _frames(monkeypatch: pytest.MonkeyPatch, count: int | None) -> None:
        monkeypatch.setattr(
            "camview.ui.widgets.video_tile.displayed_picture_count",
            lambda _player: count,
        )

    def test_watchdog_runs_only_while_playing(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        assert tile._stall_timer.isActive() is False

        fake_instance.players[0].event_manager_obj.trigger(
            vlc.EventType.MediaPlayerPlaying
        )
        assert tile._stall_timer.isActive() is True
        tile.close_stream()

    def test_advancing_frames_are_not_a_stall(
        self,
        qapp: QApplication,
        fake_instance: FakeInstance,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tile, _ = self._playing_tile(qapp, fake_instance)
        tile._last_progress_at = monotonic() - 3600  # long past the timeout

        self._frames(monkeypatch, 10)
        tile._check_for_stall()
        self._frames(monkeypatch, 11)
        tile._check_for_stall()

        assert tile.status == ConnectionStatus.PLAYING
        assert tile._reconnect_timer.isActive() is False
        tile.close_stream()

    def test_frozen_frame_count_triggers_a_reconnect(
        self,
        qapp: QApplication,
        fake_instance: FakeInstance,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tile, _ = self._playing_tile(qapp, fake_instance)
        self._frames(monkeypatch, 42)
        tile._check_for_stall()  # establishes the baseline
        tile._last_progress_at = monotonic() - (STALL_TIMEOUT_S + 1)

        tile._check_for_stall()

        assert tile.status == ConnectionStatus.ERROR
        assert "travado" in tile._message_label.text()
        assert tile._reconnect_timer.isActive() is True
        tile.close_stream()

    def test_stall_is_only_declared_after_the_timeout(
        self,
        qapp: QApplication,
        fake_instance: FakeInstance,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tile, _ = self._playing_tile(qapp, fake_instance)
        self._frames(monkeypatch, 42)
        tile._check_for_stall()
        tile._last_progress_at = monotonic() - (STALL_TIMEOUT_S - 2)

        tile._check_for_stall()

        assert tile.status == ConnectionStatus.PLAYING
        tile.close_stream()

    def test_unknown_frame_count_is_never_treated_as_stalled(
        self,
        qapp: QApplication,
        fake_instance: FakeInstance,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A libVLC that won't report counters must not cause reconnect loops."""
        tile, _ = self._playing_tile(qapp, fake_instance)
        self._frames(monkeypatch, None)
        tile._last_progress_at = monotonic() - (STALL_TIMEOUT_S + 1)

        tile._check_for_stall()

        assert tile.status == ConnectionStatus.PLAYING
        tile.close_stream()

    def test_hidden_tile_is_not_judged(
        self,
        qapp: QApplication,
        fake_instance: FakeInstance,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """While another cell is maximized the rest are hidden, not broken."""
        tile, _ = self._playing_tile(qapp, fake_instance)
        self._frames(monkeypatch, 42)
        tile._check_for_stall()
        tile.hide()
        tile._last_progress_at = monotonic() - (STALL_TIMEOUT_S + 1)

        tile._check_for_stall()

        assert tile.status == ConnectionStatus.PLAYING
        tile.close_stream()

    def test_reconnecting_restarts_the_grace_period(
        self,
        qapp: QApplication,
        fake_instance: FakeInstance,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tile, _ = self._playing_tile(qapp, fake_instance)
        self._frames(monkeypatch, 42)
        tile._check_for_stall()
        tile._last_progress_at = monotonic() - 3600

        tile._connect()

        assert tile._last_picture_count is None
        assert monotonic() - tile._last_progress_at < 1
        tile.close_stream()

    def test_closing_stops_the_watchdog(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile, _ = self._playing_tile(qapp, fake_instance)
        tile.close_stream()
        assert tile._stall_timer.isActive() is False
        assert tile._healthy_timer.isActive() is False


class TestCloseStream:
    def test_stops_and_releases_the_player(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        player = fake_instance.players[0]

        tile.close_stream()

        assert player.stopped is True
        assert player.released is True
        assert tile._player is None

    def test_stops_the_pending_reconnect_timer(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        fake_instance.players[0].event_manager_obj.trigger(
            vlc.EventType.MediaPlayerEncounteredError
        )
        assert tile._reconnect_timer.isActive()

        tile.close_stream()

        assert not tile._reconnect_timer.isActive()

    def test_safe_to_call_before_any_connect_attempt(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = VideoTile(title="Canal 1", stream_urls={StreamType.SUB: SUB_URL})
        tile.close_stream()  # must not raise


class TestCloseRequested:
    def test_close_button_click_emits_close_requested(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        received = []
        tile.closeRequested.connect(lambda: received.append(True))

        tile._close_button.click()

        assert received == [True]
        tile.close_stream()
