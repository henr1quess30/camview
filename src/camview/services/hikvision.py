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
#: Status queries run while the app is live and must not delay closing it,
#: so they get a tighter budget than interactive discovery.
_STATUS_TIMEOUT_SECONDS = 2.5

#: Channels the recorder can stream, with per-channel stream ids (N01/N02).
_STREAMING_CHANNELS_PATH = "/ISAPI/Streaming/channels"
#: Cameras attached to an NVR's inputs, carrying their configured names.
_INPUT_PROXY_PATH = "/ISAPI/ContentMgmt/InputProxy/channels"
#: Same channels, with the recorder's own view of whether each is online.
_INPUT_PROXY_STATUS_PATH = "/ISAPI/ContentMgmt/InputProxy/channels/status"

_ID_RE = re.compile(r"<id>(\d+)</id>")
_NAME_RE = re.compile(r"<name>([^<]*)</name>")
_ONLINE_RE = re.compile(r"<online>(\w+)</online>")
_STATUS_BLOCK_RE = re.compile(
    r"<InputProxyChannelStatus[^>]*>(.*?)</InputProxyChannelStatus>", re.DOTALL
)


class DiscoveryError(Exception):
    """Raised when a device's channel list cannot be read."""


@dataclass(frozen=True, slots=True)
class DiscoveredChannel:
    channel_number: int
    name: str


def _fetch(
    host: str,
    port: int,
    path: str,
    username: str,
    password: str,
    timeout: float = _TIMEOUT_SECONDS,
) -> str:
    url = f"http://{host}:{port}{path}"
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, url, username, password)
    opener = urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(manager),
        urllib.request.HTTPBasicAuthHandler(manager),
    )
    try:
        with opener.open(url, timeout=timeout) as response:
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


def channel_online_status(
    host: str,
    username: str,
    password: str,
    http_port: int = DEFAULT_HTTP_PORT,
    timeout: float = _STATUS_TIMEOUT_SECONDS,
) -> dict[int, bool]:
    """Ask the recorder which of its channels currently have a camera online.

    An NVR knows this: a slot whose camera is unplugged, powered off or
    unreachable reports ``<online>false</online>``. Retrying RTSP against
    such a channel can only fail, so the app uses this to stop hammering
    it — and to say *why* the cell is dark instead of blaming the network.

    Returns an empty mapping when the device does not answer this query
    (older firmware, DVRs with analog inputs), which callers must read as
    "no information", never as "everything is offline".
    """
    try:
        xml = _fetch(
            host, http_port, _INPUT_PROXY_STATUS_PATH, username, password, timeout
        )
    except DiscoveryError as exc:
        logger.info("Channel status unavailable for %s: %s", host, exc)
        return {}
    return _parse_channel_status(xml)


def _parse_channel_status(xml: str) -> dict[int, bool]:
    """Map channel number to online flag from an InputProxy status document."""
    status: dict[int, bool] = {}
    for block in _STATUS_BLOCK_RE.finditer(xml):
        body = block.group(1)
        id_match = _ID_RE.search(body)
        online_match = _ONLINE_RE.search(body)
        if id_match is None or online_match is None:
            continue
        # Ids here are plain channel numbers, not stream ids (N01/N02).
        status[int(id_match.group(1))] = online_match.group(1).strip() == "true"
    return status


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
