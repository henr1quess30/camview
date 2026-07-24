"""Tests for the reconnect backoff schedule."""

from __future__ import annotations

import pytest

from camview.services.reconnect import ReconnectBackoff


class TestReconnectBackoff:
    def test_follows_default_schedule(self) -> None:
        backoff = ReconnectBackoff()
        assert [backoff.next_delay_seconds() for _ in range(4)] == [2, 5, 10, 30]

    def test_caps_at_last_value_once_schedule_is_exhausted(self) -> None:
        backoff = ReconnectBackoff()
        for _ in range(4):
            backoff.next_delay_seconds()
        assert backoff.next_delay_seconds() == 30
        assert backoff.next_delay_seconds() == 30

    def test_reset_restarts_the_schedule(self) -> None:
        backoff = ReconnectBackoff()
        backoff.next_delay_seconds()
        backoff.next_delay_seconds()
        backoff.reset()
        assert backoff.next_delay_seconds() == 2

    def test_custom_schedule(self) -> None:
        backoff = ReconnectBackoff(schedule=(1, 3))
        assert backoff.next_delay_seconds() == 1
        assert backoff.next_delay_seconds() == 3
        assert backoff.next_delay_seconds() == 3

    def test_rejects_empty_schedule(self) -> None:
        with pytest.raises(ValueError):
            ReconnectBackoff(schedule=())
