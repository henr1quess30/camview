"""Tests for VideoTile's connection/reconnection state machine.

Uses a fake libVLC instance/player/event-manager so these tests don't
depend on a real RTSP source. ``vlc.EventType`` constants are still the
real ones (libvlc is a required system dependency for this project — see
README), which keeps the fake's event dispatch faithful to how libvlc
actually calls back.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import vlc
from PySide6.QtWidgets import QApplication

from camview.services.stream_manager import PlaybackOptions, VlcUnavailableError
from camview.ui.widgets.video_tile import ConnectionStatus, VideoTile


class FakeEventManager:
    def __init__(self) -> None:
        self._callbacks: dict[object, Callable[[object], None]] = {}

    def event_attach(self, event_type: object, callback: Callable[[object], None]) -> None:
        self._callbacks[event_type] = callback

    def trigger(self, event_type: object) -> None:
        self._callbacks[event_type](None)


class FakePlayer:
    def __init__(self) -> None:
        self.event_manager_obj = FakeEventManager()
        self.played = False
        self.stopped = False
        self.released = False
        self.media: object = None
        self.xwindow: int | None = None

    def event_manager(self) -> FakeEventManager:
        return self.event_manager_obj

    def set_xwindow(self, win_id: int) -> None:
        self.xwindow = win_id

    def set_media(self, media: object) -> None:
        self.media = media

    def play(self) -> None:
        self.played = True

    def stop(self) -> None:
        self.stopped = True

    def release(self) -> None:
        self.released = True


class FakeInstance:
    def __init__(self) -> None:
        self.players: list[FakePlayer] = []

    def media_player_new(self) -> FakePlayer:
        player = FakePlayer()
        self.players.append(player)
        return player

    def media_new(self, url: str, *options: str) -> tuple[str, tuple[str, ...]]:
        return (url, options)


@pytest.fixture
def fake_instance(monkeypatch: pytest.MonkeyPatch) -> FakeInstance:
    instance = FakeInstance()
    monkeypatch.setattr(
        "camview.ui.widgets.video_tile.get_vlc_instance", lambda: instance
    )
    return instance


def make_tile(qapp: QApplication, url: str = "rtsp://example.invalid/101") -> VideoTile:
    return VideoTile(title="Canal 1", url=url)


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
            url="rtsp://example.invalid/101",
            playback_options=PlaybackOptions(network_caching_ms=500, mute_audio=False),
        )
        tile._connect()

        url, options = fake_instance.players[0].media
        assert url == "rtsp://example.invalid/101"
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

    def test_playing_after_errors_resets_backoff(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = make_tile(qapp)
        tile._connect()
        event_manager = fake_instance.players[0].event_manager_obj

        event_manager.trigger(vlc.EventType.MediaPlayerEncounteredError)
        event_manager.trigger(vlc.EventType.MediaPlayerPlaying)

        assert tile.status == ConnectionStatus.PLAYING
        assert tile._backoff.next_delay_seconds() == 2  # schedule restarted
        tile.close_stream()


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
        tile = VideoTile(title="Canal 1", url="rtsp://example.invalid/101")
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
