"""Channel discovery for Hikvision devices, over their ISAPI HTTP interface.

Asking the device which channels actually exist beats assuming ``1..N``:
real NVRs have gaps (a 16-slot recorder with cameras on 1-11 and 13-16),
and they know each camera's configured name. Both are used to seed the
``cameras`` table when an NVR is registered.

This is Hikvision's own HTTP API, not ONVIF — a full ONVIF implementation
for third-party devices remains future work. Discovery is optional: an
NVR can still be registered with a plain channel count if it is
unreachable over HTTP or speaks a different protocol.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_HTTP_PORT = 80
_TIMEOUT_SECONDS = 8.0

#: Channels the recorder can stream, with per-channel stream ids (N01/N02).
_STREAMING_CHANNELS_PATH = "/ISAPI/Streaming/channels"
#: Cameras attached to an NVR's inputs, carrying their configured names.
_INPUT_PROXY_PATH = "/ISAPI/ContentMgmt/InputProxy/channels"

_ID_RE = re.compile(r"<id>(\d+)</id>")
_NAME_RE = re.compile(r"<name>([^<]*)</name>")


class DiscoveryError(Exception):
    """Raised when a device's channel list cannot be read."""


@dataclass(frozen=True, slots=True)
class DiscoveredChannel:
    channel_number: int
    name: str


def _fetch(
    host: str, port: int, path: str, username: str, password: str
) -> str:
    url = f"http://{host}:{port}{path}"
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, url, username, password)
    opener = urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(manager),
        urllib.request.HTTPBasicAuthHandler(manager),
    )
    try:
        with opener.open(url, timeout=_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise DiscoveryError(
                "Usuário ou senha incorretos para acessar o dispositivo."
            ) from exc
        raise DiscoveryError(
            f"O dispositivo respondeu com erro HTTP {exc.code}."
        ) from exc
    except OSError as exc:
        raise DiscoveryError(f"Não foi possível acessar o dispositivo: {exc}") from exc


def _streamable_channel_numbers(xml: str) -> list[int]:
    """Channel numbers from ``<id>N01</id>``-style stream ids.

    Every channel advertises one id per stream (101 main, 102 sub, ...),
    so the trailing two digits are dropped and duplicates collapsed.
    """
    numbers = {int(raw) // 100 for raw in _ID_RE.findall(xml)}
    return sorted(number for number in numbers if number > 0)


def _channel_names(xml: str) -> dict[int, str]:
    ids = [int(raw) for raw in _ID_RE.findall(xml)]
    names = _NAME_RE.findall(xml)
    return {
        channel: name.strip()
        for channel, name in zip(ids, names, strict=False)
        if name.strip()
    }


def discover_channels(
    host: str,
    username: str,
    password: str,
    http_port: int = DEFAULT_HTTP_PORT,
) -> list[DiscoveredChannel]:
    """Ask a Hikvision device which channels it has, and what they are called.

    Falls back to ``Canal N`` for channels the device does not name. Raises
    :class:`DiscoveryError` if the channel list cannot be read at all; a
    failure to read *names* is tolerated, since the channel list alone is
    still useful.
    """
    xml = _fetch(host, http_port, _STREAMING_CHANNELS_PATH, username, password)
    channel_numbers = _streamable_channel_numbers(xml)
    if not channel_numbers:
        raise DiscoveryError("O dispositivo não informou nenhum canal.")

    names: dict[int, str] = {}
    try:
        proxy_xml = _fetch(host, http_port, _INPUT_PROXY_PATH, username, password)
        names = _channel_names(proxy_xml)
    except DiscoveryError as exc:
        # Standalone cameras have no InputProxy endpoint; names are a bonus.
        logger.info("Channel names unavailable for %s: %s", host, exc)

    return [
        DiscoveredChannel(
            channel_number=number,
            name=names.get(number) or f"Canal {number}",
        )
        for number in channel_numbers
    ]
