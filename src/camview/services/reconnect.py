"""Exponential-ish backoff schedule for stream reconnection attempts."""

from __future__ import annotations

DEFAULT_BACKOFF_SCHEDULE_S: tuple[int, ...] = (2, 5, 10, 30)


class ReconnectBackoff:
    """Tracks reconnect attempts and returns the delay before the next one.

    Delays follow ``schedule`` (default 2s, 5s, 10s, 30s); once exhausted,
    every further attempt reuses the last (largest) delay. Call
    :meth:`reset` after a successful connection so the next failure starts
    the schedule over from the beginning.
    """

    def __init__(self, schedule: tuple[int, ...] = DEFAULT_BACKOFF_SCHEDULE_S) -> None:
        if not schedule:
            raise ValueError("schedule must not be empty")
        self._schedule = schedule
        self._attempt = 0

    def next_delay_seconds(self) -> int:
        index = min(self._attempt, len(self._schedule) - 1)
        delay = self._schedule[index]
        self._attempt += 1
        return delay

    def reset(self) -> None:
        self._attempt = 0
