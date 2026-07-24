"""Tests for the TCP reachability check used by the NVR 'test connection' button."""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from camview.services.connectivity import check_tcp_connection


@pytest.fixture
def local_server() -> Iterator[tuple[str, int]]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    try:
        yield host, port
    finally:
        server.close()


def test_succeeds_when_port_is_listening(local_server: tuple[str, int]) -> None:
    host, port = local_server
    check_tcp_connection(host, port, timeout=1.0)  # must not raise


def test_raises_when_connection_is_refused() -> None:
    # A closed local port refuses the connection immediately (no wait for
    # the timeout), so this stays fast without relying on the network.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    _host, port = server.getsockname()
    server.close()  # port is now guaranteed closed

    with pytest.raises(OSError):
        check_tcp_connection("127.0.0.1", port, timeout=1.0)


def test_raises_on_invalid_host() -> None:
    with pytest.raises(OSError):
        check_tcp_connection("this.host.does.not.resolve.invalid", 554, timeout=1.0)
