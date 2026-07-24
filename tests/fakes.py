"""Fake libVLC objects shared by the tile and grid tests.

These stand in for ``vlc.Instance`` / ``vlc.MediaPlayer`` so tests never
need a real RTSP source, and so player release can be asserted directly.
"""

from __future__ import annotations

from collections.abc import Callable


class FakeEventManager:
    def __init__(self) -> None:
        self._callbacks: dict[object, Callable[[object], None]] = {}

    def event_attach(
        self, event_type: object, callback: Callable[[object], None]
    ) -> None:
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
        self.mouse_input: bool | None = None
        self.key_input: bool | None = None

    def event_manager(self) -> FakeEventManager:
        return self.event_manager_obj

    def set_xwindow(self, win_id: int) -> None:
        self.xwindow = win_id

    def video_set_mouse_input(self, on: bool) -> None:
        self.mouse_input = on

    def video_set_key_input(self, on: bool) -> None:
        self.key_input = on

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
