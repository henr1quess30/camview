"""Tests for Hikvision ISAPI channel discovery.

HTTP is faked at the ``_fetch`` seam, so no device is contacted. The XML
fixtures mirror real responses observed on Hikvision NVRs, including the
channel-number gaps that motivated this feature.
"""

from __future__ import annotations

import pytest

from camview.services import hikvision
from camview.services.hikvision import DiscoveryError, discover_channels

STREAMING_XML = """
<StreamingChannelList>
  <StreamingChannel><id>101</id></StreamingChannel>
  <StreamingChannel><id>102</id></StreamingChannel>
  <StreamingChannel><id>201</id></StreamingChannel>
  <StreamingChannel><id>202</id></StreamingChannel>
  <StreamingChannel><id>401</id></StreamingChannel>
  <StreamingChannel><id>402</id></StreamingChannel>
</StreamingChannelList>
"""

INPUT_PROXY_XML = """
<InputProxyChannelList>
  <InputProxyChannel><id>1</id><name>MONTAGEM</name></InputProxyChannel>
  <InputProxyChannel><id>2</id><name></name></InputProxyChannel>
  <InputProxyChannel><id>4</id><name>Porta Principal</name></InputProxyChannel>
</InputProxyChannelList>
"""


def fake_fetch(responses: dict[str, str | Exception]):
    def _fetch(host: str, port: int, path: str, username: str, password: str) -> str:
        result = responses[path]
        if isinstance(result, Exception):
            raise result
        return result

    return _fetch


@pytest.fixture
def patched_fetch(monkeypatch: pytest.MonkeyPatch):
    def apply(responses: dict[str, str | Exception]) -> None:
        monkeypatch.setattr(hikvision, "_fetch", fake_fetch(responses))

    return apply


class TestDiscoverChannels:
    def test_returns_only_channels_the_device_reports(self, patched_fetch) -> None:
        """Real NVRs have gaps — channel 3 here simply has no camera."""
        patched_fetch(
            {
                hikvision._STREAMING_CHANNELS_PATH: STREAMING_XML,
                hikvision._INPUT_PROXY_PATH: INPUT_PROXY_XML,
            }
        )
        channels = discover_channels("192.0.2.10", "admin", "test-password")

        assert [c.channel_number for c in channels] == [1, 2, 4]

    def test_uses_the_configured_camera_names(self, patched_fetch) -> None:
        patched_fetch(
            {
                hikvision._STREAMING_CHANNELS_PATH: STREAMING_XML,
                hikvision._INPUT_PROXY_PATH: INPUT_PROXY_XML,
            }
        )
        channels = discover_channels("192.0.2.10", "admin", "test-password")

        by_number = {c.channel_number: c.name for c in channels}
        assert by_number[1] == "MONTAGEM"
        assert by_number[4] == "Porta Principal"

    def test_falls_back_for_unnamed_channels(self, patched_fetch) -> None:
        patched_fetch(
            {
                hikvision._STREAMING_CHANNELS_PATH: STREAMING_XML,
                hikvision._INPUT_PROXY_PATH: INPUT_PROXY_XML,
            }
        )
        channels = discover_channels("192.0.2.10", "admin", "test-password")

        by_number = {c.channel_number: c.name for c in channels}
        assert by_number[2] == "Canal 2"  # device returned an empty name

    def test_missing_name_endpoint_still_yields_channels(
        self, patched_fetch
    ) -> None:
        """Standalone cameras have no InputProxy endpoint."""
        patched_fetch(
            {
                hikvision._STREAMING_CHANNELS_PATH: STREAMING_XML,
                hikvision._INPUT_PROXY_PATH: DiscoveryError("404"),
            }
        )
        channels = discover_channels("192.0.2.10", "admin", "test-password")

        assert [c.channel_number for c in channels] == [1, 2, 4]
        assert all(c.name.startswith("Canal ") for c in channels)

    def test_unreadable_channel_list_raises(self, patched_fetch) -> None:
        patched_fetch(
            {hikvision._STREAMING_CHANNELS_PATH: DiscoveryError("sem rede")}
        )
        with pytest.raises(DiscoveryError):
            discover_channels("192.0.2.10", "admin", "test-password")

    def test_empty_channel_list_raises(self, patched_fetch) -> None:
        patched_fetch(
            {hikvision._STREAMING_CHANNELS_PATH: "<StreamingChannelList/>"}
        )
        with pytest.raises(DiscoveryError):
            discover_channels("192.0.2.10", "admin", "test-password")


class TestParsingHelpers:
    def test_collapses_main_and_sub_stream_ids(self) -> None:
        assert hikvision._streamable_channel_numbers(STREAMING_XML) == [1, 2, 4]

    def test_handles_two_digit_channels(self) -> None:
        xml = "<r><id>1601</id><id>1602</id></r>"
        assert hikvision._streamable_channel_numbers(xml) == [16]

    def test_ignores_blank_names(self) -> None:
        names = hikvision._channel_names(INPUT_PROXY_XML)
        assert names == {1: "MONTAGEM", 4: "Porta Principal"}
