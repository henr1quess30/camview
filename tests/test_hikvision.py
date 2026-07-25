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


class TestFetchErrorMapping:
    """Every HTTP failure must become a DiscoveryError the UI can show."""

    @staticmethod
    def _opener_raising(exc: Exception) -> object:
        class FakeOpener:
            def open(self, *_args: object, **_kwargs: object) -> object:
                raise exc

        return FakeOpener()

    def _fetch_with(
        self, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> Exception:
        monkeypatch.setattr(
            hikvision.urllib.request,
            "build_opener",
            lambda *_handlers: self._opener_raising(exc),
        )
        with pytest.raises(DiscoveryError) as caught:
            hikvision._fetch("192.0.2.10", 80, "/ISAPI/x", "admin", "senha-falsa")
        return caught.value

    @pytest.mark.parametrize("code", [401, 403])
    def test_auth_failures_name_the_credentials(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        error = hikvision.urllib.error.HTTPError(
            "http://192.0.2.10/", code, "Unauthorized", {}, None  # type: ignore[arg-type]
        )

        message = str(self._fetch_with(monkeypatch, error))

        assert "senha" in message.lower()

    def test_other_http_errors_report_the_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        error = hikvision.urllib.error.HTTPError(
            "http://192.0.2.10/", 500, "Server Error", {}, None  # type: ignore[arg-type]
        )

        assert "500" in str(self._fetch_with(monkeypatch, error))

    def test_network_failures_are_wrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        message = str(self._fetch_with(monkeypatch, TimeoutError("timed out")))

        assert "Não foi possível acessar o dispositivo" in message

    def test_a_successful_response_is_decoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeResponse:
            def read(self) -> bytes:
                return "<id>101</id>".encode("utf-8")

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        class FakeOpener:
            def open(self, *_args: object, **_kwargs: object) -> FakeResponse:
                return FakeResponse()

        monkeypatch.setattr(
            hikvision.urllib.request, "build_opener", lambda *_h: FakeOpener()
        )

        assert hikvision._fetch("192.0.2.10", 80, "/x", "admin", "x") == "<id>101</id>"


class TestParsingHelpers:
    def test_collapses_main_and_sub_stream_ids(self) -> None:
        assert hikvision._streamable_channel_numbers(STREAMING_XML) == [1, 2, 4]

    def test_handles_two_digit_channels(self) -> None:
        xml = "<r><id>1601</id><id>1602</id></r>"
        assert hikvision._streamable_channel_numbers(xml) == [16]

    def test_ignores_blank_names(self) -> None:
        names = hikvision._channel_names(INPUT_PROXY_XML)
        assert names == {1: "MONTAGEM", 4: "Porta Principal"}
