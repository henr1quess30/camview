"""Tests for registering cameras from pasted RTSP URLs.

This is how equipment that does not follow Hikvision's channel numbering
gets in: the camera's own path, taken verbatim from the URL its maker
documents. No real credentials appear here — every password below is
invented.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication, QDialog

from camview.models.camera import Camera, StreamType
from camview.models.nvr import DeviceType, Nvr
from camview.services.credentials import get_nvr_password
from camview.services.rtsp import (
    build_stream_url,
    parse_rtsp_url,
    parse_rtsp_urls,
)
from camview.ui.dialogs.paste_urls_dialog import PasteUrlsDialog
from camview.ui.main_window import MainWindow, _stream_urls_for

# Documentation address, invented password with characters that must be
# percent-encoded in a URL.
SAMPLE = "rtsp://operador:Se%40nha%23123@192.0.2.10:554/live/main"


class TestParsingOneUrl:
    def test_every_part_is_extracted(self) -> None:
        stream = parse_rtsp_url(SAMPLE)

        assert stream is not None
        assert (stream.host, stream.port) == ("192.0.2.10", 554)
        assert stream.username == "operador"
        assert stream.path == "/live/main"

    def test_the_password_is_decoded(self) -> None:
        """%40 is '@' and %23 is '#'; the camera expects the real thing."""
        stream = parse_rtsp_url(SAMPLE)

        assert stream is not None
        assert stream.password == "Se@nha#123"

    def test_a_missing_port_defaults_to_554(self) -> None:
        stream = parse_rtsp_url("rtsp://u:p@192.0.2.10/live/main")

        assert stream is not None
        assert stream.port == 554

    def test_a_query_string_is_kept(self) -> None:
        """Some cameras carry the channel in the query, not the path."""
        stream = parse_rtsp_url(
            "rtsp://u:p@192.0.2.10:554/cam/realmonitor?channel=1&subtype=0"
        )

        assert stream is not None
        assert stream.path == "/cam/realmonitor?channel=1&subtype=0"

    def test_a_url_without_credentials_still_parses(self) -> None:
        stream = parse_rtsp_url("rtsp://192.0.2.10:554/live/main")

        assert stream is not None
        assert stream.username == "" and stream.password == ""

    @pytest.mark.parametrize(
        "text", ["", "   ", "não é url", "http://192.0.2.10/live", "rtsp://"]
    )
    def test_anything_else_is_rejected(self, text: str) -> None:
        assert parse_rtsp_url(text) is None

    def test_the_round_trip_rebuilds_the_url(self) -> None:
        stream = parse_rtsp_url(SAMPLE)
        assert stream is not None

        rebuilt = build_stream_url(
            host=stream.host,
            port=stream.port,
            username=stream.username,
            password=stream.password,
            path=stream.path,
        )

        assert rebuilt == SAMPLE


class TestSuggestions:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/live/main", "/live/sub"),
            ("/cam/stream1", "/cam/stream2"),
            ("/h264/ch1/0", "/h264/ch1/1"),
            ("/onvif1", ""),
        ],
    )
    def test_the_sub_stream_path_is_guessed(self, path: str, expected: str) -> None:
        stream = parse_rtsp_url(f"rtsp://u:p@192.0.2.10:554{path}")

        assert stream is not None
        assert stream.suggested_sub_path == expected

    def test_the_name_falls_back_to_the_address(self) -> None:
        stream = parse_rtsp_url(SAMPLE)

        assert stream is not None
        assert stream.suggested_name == "Câmera 192.0.2.10"


class TestParsingAList:
    def test_each_line_becomes_a_camera(self) -> None:
        text = "\n".join(
            f"rtsp://u:p@192.0.2.{n}:554/live/main" for n in (10, 11, 12)
        )

        assert len(parse_rtsp_urls(text)) == 3

    def test_noise_between_urls_is_ignored(self) -> None:
        text = f"# minhas câmeras\n{SAMPLE}\n\nobservação qualquer\n"

        assert len(parse_rtsp_urls(text)) == 1

    def test_duplicates_are_dropped(self) -> None:
        assert len(parse_rtsp_urls(f"{SAMPLE}\n{SAMPLE}")) == 1

    def test_the_same_host_with_another_path_is_a_separate_camera(self) -> None:
        text = (
            "rtsp://u:p@192.0.2.10:554/live/main\n"
            "rtsp://u:p@192.0.2.10:554/live/second"
        )

        assert len(parse_rtsp_urls(text)) == 2


class TestUrlBuildingForCustomPaths:
    def _device(self, **kwargs: object) -> Nvr:
        return Nvr(
            name="Portaria",
            host="192.0.2.10",
            username="operador",
            channel_count=1,
            device_type=DeviceType.CAMERA,
            **kwargs,  # type: ignore[arg-type]
        )

    def test_the_camera_path_is_used_verbatim(self) -> None:
        device = self._device(stream_path="/live/main", stream_path_sub="/live/sub")
        camera = Camera(nvr_id=1, channel_number=1, name="Portaria")

        urls = _stream_urls_for(camera, device, "senha")

        assert urls[StreamType.MAIN].endswith("/live/main")
        assert urls[StreamType.SUB].endswith("/live/sub")

    def test_one_path_serves_both_streams(self) -> None:
        device = self._device(stream_path="/live/main")
        camera = Camera(nvr_id=1, channel_number=1, name="Portaria")

        urls = _stream_urls_for(camera, device, "senha")

        assert urls[StreamType.MAIN] == urls[StreamType.SUB]

    def test_a_device_without_a_path_still_uses_channel_numbering(self) -> None:
        device = Nvr(name="NVR", host="192.0.2.10", username="admin", channel_count=4)
        camera = Camera(nvr_id=1, channel_number=3, name="Canal 3")

        urls = _stream_urls_for(camera, device, "senha")

        assert urls[StreamType.MAIN].endswith("/Streaming/Channels/301")
        assert urls[StreamType.SUB].endswith("/Streaming/Channels/302")


class TestTheDialog:
    def test_pasting_urls_fills_the_preview(self, qapp: QApplication) -> None:
        dialog = PasteUrlsDialog()

        dialog.text_edit.setPlainText(f"{SAMPLE}\nrtsp://u:p@192.0.2.11:554/live/main")

        assert dialog.table.rowCount() == 2
        assert "2 câmera(s)" in dialog.summary_label.text()

    def test_ok_is_disabled_until_something_parses(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QDialogButtonBox

        dialog = PasteUrlsDialog()
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok.isEnabled() is False

        dialog.text_edit.setPlainText(SAMPLE)
        assert ok.isEnabled() is True

    def test_urls_without_a_password_are_called_out(self, qapp: QApplication) -> None:
        """The app refuses to open a stream with no stored password."""
        dialog = PasteUrlsDialog()

        dialog.text_edit.setPlainText("rtsp://192.0.2.10:554/live/main")

        assert "sem senha" in dialog.summary_label.text()

    def test_edits_in_the_table_are_returned(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QTableWidgetItem

        dialog = PasteUrlsDialog()
        dialog.text_edit.setPlainText(SAMPLE)

        dialog.table.setItem(0, 0, QTableWidgetItem("Portaria"))
        dialog.table.setItem(0, 3, QTableWidgetItem(""))

        stream, name, sub_path = dialog.result_streams()[0]
        assert name == "Portaria"
        assert sub_path == ""
        assert stream.host == "192.0.2.10"


class TestRegisteringFromTheWindow:
    @pytest.fixture
    def window(
        self,
        qapp: QApplication,
        db_connection: sqlite3.Connection,
        fake_keyring: object,
        fake_instance: FakeInstance,
    ) -> MainWindow:
        return MainWindow(connection=db_connection)

    def paste(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch, text: str
    ) -> None:
        def fake_exec(dialog: PasteUrlsDialog) -> int:
            dialog.text_edit.setPlainText(text)
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(PasteUrlsDialog, "exec", fake_exec)
        window._add_cameras_from_urls()

    def test_each_url_becomes_a_standalone_camera(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.paste(
            window,
            monkeypatch,
            "\n".join(f"rtsp://u:p@192.0.2.{n}:554/live/main" for n in (10, 11)),
        )

        devices = window._nvr_repository.list_all()
        assert len(devices) == 2
        assert all(d.is_camera and d.stream_path == "/live/main" for d in devices)

    def test_the_password_goes_to_the_keyring_not_the_database(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.paste(window, monkeypatch, SAMPLE)
        device = window._nvr_repository.list_all()[0]

        assert get_nvr_password(device.id) == "Se@nha#123"  # type: ignore[arg-type]
        row = window._connection.execute(
            "SELECT * FROM nvrs WHERE id = ?", (device.id,)
        ).fetchone()
        assert "Se@nha#123" not in str(tuple(row))

    def test_each_camera_gets_its_row_in_the_tree(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.paste(window, monkeypatch, SAMPLE)

        assert window.device_tree.topLevelItemCount() == 1
        assert window.device_tree.topLevelItem(0).childCount() == 0

    def test_it_reports_how_many_were_added(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.paste(window, monkeypatch, SAMPLE)

        assert "1 câmera(s) adicionada" in window.statusBar().currentMessage()

    def test_cancelling_adds_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            PasteUrlsDialog, "exec", lambda self: QDialog.DialogCode.Rejected
        )

        window._add_cameras_from_urls()

        assert window._nvr_repository.list_all() == []

    def test_opening_one_uses_its_own_path(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.paste(window, monkeypatch, SAMPLE)
        device = window._nvr_repository.list_all()[0]
        camera = window._camera_repository.list_by_nvr(device.id)[0]  # type: ignore[arg-type]

        window._open_camera_at(camera.id, 0)  # type: ignore[arg-type]

        tile = window.video_grid.tile_at(0)
        assert tile is not None
        assert tile.url.endswith("/live/main") or tile.url.endswith("/live/sub")
        window.video_grid.clear()
