"""Lightweight TCP reachability check, used by the NVR "test connection" button.

This only proves the host/port is reachable — it does not attempt an
RTSP session (that starts in Phase 3, where actual stream playback is
implemented).
"""

from __future__ import annotations

import socket


def check_tcp_connection(host: str, port: int, timeout: float = 3.0) -> None:
    """Attempt a TCP connection to ``(host, port)``.

    Raises ``OSError`` (or a subclass, e.g. ``TimeoutError``,
    ``ConnectionRefusedError``, ``socket.gaierror``) on failure.
    """
    with socket.create_connection((host, port), timeout=timeout):
        pass
