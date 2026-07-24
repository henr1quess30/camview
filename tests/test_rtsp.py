"""Tests for Hikvision RTSP URL generation and channel auto-generation."""

from __future__ import annotations

import pytest

from camview.models.camera import StreamType
from camview.services.rtsp import build_channel_url, generate_missing_channel_cameras


class TestBuildChannelUrl:
    @pytest.mark.parametrize(
        ("channel_number", "stream_type", "expected_code"),
        [
            (1, StreamType.MAIN, 101),
            (1, StreamType.SUB, 102),
            (2, StreamType.MAIN, 201),
            (2, StreamType.SUB, 202),
            (3, StreamType.MAIN, 301),
            (3, StreamType.SUB, 302),
            (16, StreamType.SUB, 1602),
        ],
    )
    def test_hikvision_channel_numbering(
        self, channel_number: int, stream_type: StreamType, expected_code: int
    ) -> None:
        url = build_channel_url(
            host="192.0.2.10",
            port=554,
            username="admin",
            password="s3nha-teste",
            channel_number=channel_number,
            stream_type=stream_type,
        )
        assert (
            url
            == f"rtsp://admin:s3nha-teste@192.0.2.10:554/Streaming/Channels/{expected_code}"
        )

    def test_url_encodes_special_characters_in_credentials(self) -> None:
        url = build_channel_url(
            host="192.0.2.10",
            port=554,
            username="ad min",
            password="p@ss:word/1",
            channel_number=1,
            stream_type=StreamType.MAIN,
        )
        assert url == (
            "rtsp://ad%20min:p%40ss%3Aword%2F1@192.0.2.10:554/Streaming/Channels/101"
        )

    def test_uses_custom_port(self) -> None:
        url = build_channel_url(
            host="nvr.local",
            port=8554,
            username="admin",
            password="x",
            channel_number=1,
            stream_type=StreamType.MAIN,
        )
        assert url.startswith("rtsp://admin:x@nvr.local:8554/")

    def test_rejects_non_positive_channel_number(self) -> None:
        with pytest.raises(ValueError):
            build_channel_url(
                host="192.0.2.10",
                port=554,
                username="admin",
                password="x",
                channel_number=0,
                stream_type=StreamType.MAIN,
            )


class TestGenerateMissingChannelCameras:
    def test_generates_one_camera_per_channel(self) -> None:
        cameras = generate_missing_channel_cameras(nvr_id=1, channel_count=3)
        assert [c.channel_number for c in cameras] == [1, 2, 3]
        assert all(c.nvr_id == 1 for c in cameras)
        assert cameras[0].name == "Canal 1"

    def test_skips_existing_channel_numbers(self) -> None:
        cameras = generate_missing_channel_cameras(
            nvr_id=1, channel_count=4, existing_channel_numbers={1, 2}
        )
        assert [c.channel_number for c in cameras] == [3, 4]

    def test_no_missing_channels_returns_empty_list(self) -> None:
        cameras = generate_missing_channel_cameras(
            nvr_id=1, channel_count=2, existing_channel_numbers={1, 2}
        )
        assert cameras == []

    def test_rejects_non_positive_channel_count(self) -> None:
        with pytest.raises(ValueError):
            generate_missing_channel_cameras(nvr_id=1, channel_count=0)
