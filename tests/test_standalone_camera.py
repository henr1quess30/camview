"""Tests for standalone cameras and for parking channels with no signal.

Both come from the same realisation: the app was designed around "an NVR
with N channels", and reality includes a lone camera on the wall and NVR
slots with nothing plugged into them.
"""

from __future__ import annotations

import sqlite3

import pytest
import vlc
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication

from camview.models.nvr import DeviceType, Nvr
from camview.services.credentials import set_nvr_password
from camview.services.hikvision import _parse_channel_status
from camview.services.rtsp import generate_missing_channel_cameras
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.main_window import MainWindow
from camview.ui.widgets.device_tree import CAMERA_ID_ROLE, NVR_ID_ROLE
from camview.ui.widgets.video_tile import (
    OFFLINE_RETRY_S,
    STATUS_CHECK_AFTER_FAILURES,
    ConnectionStatus,
)

TEST_PASSWORD = "test-password"

STATUS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<InputProxyChannelStatusList version="1.0">
<InputProxyChannelStatus version="1.0">
<id>1</id>
<sourceInputPortDescriptor><ipAddress>192.0.2.161</ipAddress></sourceInputPortDescriptor>
<online>true</online>
</InputProxyChannelStatus>
<InputProxyChannelStatus version="1.0">
<id>2</id>
<sourceInputPortDescriptor><ipAddress>192.0.2.162</ipAddress></sourceInputPortDescriptor>
<online>false</online>
</InputProxyChannelStatus>
</InputProxyChannelStatusList>
"""


@pytest.fixture
def window(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    fake_instance: FakeInstance,
) -> MainWindow:
    return MainWindow(connection=db_connection)


def add_device(
    window: MainWindow, device_type: DeviceType, channels: int, name: str = "Disp"
) -> Nvr:
    nvr = window._nvr_repository.create(
        Nvr(
            name=name,
            host="192.0.2.10",
            username="admin",
            channel_count=channels,
            device_type=device_type,
        )
    )
    set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
    window._create_cameras(nvr, None)
    window.device_tree.refresh()
    return nvr


class TestDeviceTypePersistence:
    def test_devices_default_to_nvr(self, window: MainWindow) -> None:
        """Everything registered before this existed was an NVR."""
        nvr = add_device(window, DeviceType.NVR, 4)
        assert window._nvr_repository.get(nvr.id).device_type is DeviceType.NVR  # type: ignore[arg-type,union-attr]

    def test_a_camera_round_trips(self, window: MainWindow) -> None:
        camera = add_device(window, DeviceType.CAMERA, 1, name="Portaria")

        stored = window._nvr_repository.get(camera.id)  # type: ignore[arg-type]

        assert stored.is_camera  # type: ignore[union-attr]

    def test_a_camera_gets_exactly_one_channel(self, window: MainWindow) -> None:
        camera = add_device(window, DeviceType.CAMERA, 1, name="Portaria")

        cameras = window._camera_repository.list_by_nvr(camera.id)  # type: ignore[arg-type]

        assert len(cameras) == 1
        assert cameras[0].name == "Portaria", "the camera carries the device's name"

    def test_a_camera_ignores_a_larger_channel_count(
        self, window: MainWindow
    ) -> None:
        camera = add_device(window, DeviceType.CAMERA, 8, name="Portaria")

        assert len(window._camera_repository.list_by_nvr(camera.id)) == 1  # type: ignore[arg-type]


class TestSidebar:
    def test_a_camera_is_one_row_not_a_folder(self, window: MainWindow) -> None:
        add_device(window, DeviceType.CAMERA, 1, name="Portaria")

        item = window.device_tree.topLevelItem(0)

        assert item.childCount() == 0
        assert item.text(0) == "Portaria"
        assert item.data(0, CAMERA_ID_ROLE) is not None
        assert item.data(0, NVR_ID_ROLE) is not None, "still editable as a device"

    def test_an_nvr_keeps_its_channels_as_children(self, window: MainWindow) -> None:
        add_device(window, DeviceType.NVR, 3, name="NVR")

        item = window.device_tree.topLevelItem(0)

        assert item.childCount() == 3


class TestOpeningASingleCamera:
    def test_double_click_fills_the_window(self, window: MainWindow) -> None:
        add_device(window, DeviceType.CAMERA, 1, name="Portaria")
        item = window.device_tree.topLevelItem(0)

        window._on_device_tree_item_double_clicked(item, 0)

        assert (window.video_grid.rows, window.video_grid.columns) == (1, 1)
        assert window.video_grid.tile_at(0) is not None
        window.video_grid.clear()

    def test_an_nvr_channel_still_goes_to_the_next_free_cell(
        self, window: MainWindow
    ) -> None:
        """Clicking several channels in a row must still build a mosaic."""
        add_device(window, DeviceType.NVR, 4, name="NVR")
        nvr_item = window.device_tree.topLevelItem(0)

        window._on_device_tree_item_double_clicked(nvr_item.child(0), 0)
        window._on_device_tree_item_double_clicked(nvr_item.child(1), 0)

        assert (window.video_grid.rows, window.video_grid.columns) == (2, 2)
        assert sorted(window.video_grid.tiles()) == [0, 1]
        window.video_grid.clear()

    def test_opening_a_camera_clears_the_loaded_layout(
        self, window: MainWindow
    ) -> None:
        add_device(window, DeviceType.CAMERA, 1, name="Portaria")
        window._set_current_layout(
            window._layout_repository.create(
                __import__("camview.models.layout", fromlist=["Layout"]).Layout(
                    name="Fábrica", rows=2, columns=2
                )
            )
        )

        window._on_device_tree_item_double_clicked(window.device_tree.topLevelItem(0), 0)

        assert window._current_layout_id is None
        window.video_grid.clear()


class TestDialogAdaptsToDeviceType:
    def test_channel_count_is_hidden_for_a_camera(self, qapp: QApplication) -> None:
        dialog = NvrDialog()
        dialog.show()

        dialog.device_type_combo.setCurrentIndex(1)

        assert dialog.channel_count_spin.isVisible() is False
        assert dialog.channel_count_spin.value() == 1
        assert dialog.result_device_type() is DeviceType.CAMERA

    def test_channel_count_returns_for_an_nvr(self, qapp: QApplication) -> None:
        dialog = NvrDialog()
        dialog.show()
        dialog.device_type_combo.setCurrentIndex(1)

        dialog.device_type_combo.setCurrentIndex(0)

        assert dialog.channel_count_spin.isVisible() is True

    def test_editing_a_camera_opens_on_the_camera_type(
        self, qapp: QApplication
    ) -> None:
        camera = Nvr(
            name="Portaria",
            host="192.0.2.10",
            username="admin",
            channel_count=1,
            device_type=DeviceType.CAMERA,
            id=1,
        )

        dialog = NvrDialog(nvr=camera, password=TEST_PASSWORD)

        assert dialog.result_device_type() is DeviceType.CAMERA
        assert dialog.result_nvr(existing=camera).is_camera


class TestChannelStatusParsing:
    def test_reads_the_online_flag_per_channel(self) -> None:
        assert _parse_channel_status(STATUS_XML) == {1: True, 2: False}

    def test_an_unexpected_document_yields_no_information(self) -> None:
        """No information must never be read as 'everything is offline'."""
        assert _parse_channel_status("<html>login</html>") == {}


class TestStatusWorkerSignal:
    def test_the_mapping_survives_the_thread_hop(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a ``dict`` signal argument is marshalled as a
        QVariantMap, which only accepts string keys — channel numbers are
        ints, so the mapping arrived empty on the GUI side and nothing was
        ever parked. Declared as ``object`` now."""
        from camview.ui import workers

        monkeypatch.setattr(
            workers, "channel_online_status", lambda *a, **k: {1: True, 12: False}
        )
        received: list[dict[int, bool]] = []
        worker = workers.ChannelStatusWorker(3, "192.0.2.10", "admin", TEST_PASSWORD)
        worker.finished_with.connect(lambda _id, status: received.append(status))

        worker.start()
        worker.wait(5000)
        QApplication.processEvents()

        assert received == [{1: True, 12: False}]

    def test_a_failing_query_reports_no_information(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from camview.ui import workers

        def explode(*_args: object, **_kwargs: object) -> dict[int, bool]:
            raise RuntimeError("rede caiu")

        monkeypatch.setattr(workers, "channel_online_status", explode)
        received: list[dict[int, bool]] = []
        worker = workers.ChannelStatusWorker(3, "192.0.2.10", "admin", TEST_PASSWORD)
        worker.finished_with.connect(lambda _id, status: received.append(status))

        worker.start()
        worker.wait(5000)
        QApplication.processEvents()

        assert received == [{}]


class TestParkingDeadChannels:
    def _failing_window(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> MainWindow:
        nvr = add_device(window, DeviceType.NVR, 2, name="NVR")
        for camera in window._camera_repository.list_by_nvr(nvr.id):  # type: ignore[arg-type]
            window._open_camera_at(camera.id, camera.channel_number - 1)
        for position in (0, 1):
            window.video_grid.tile_at(position)._connect()  # type: ignore[union-attr]
        return window

    def test_a_failing_cell_asks_the_device_about_its_channel(
        self,
        window: MainWindow,
        fake_instance: FakeInstance,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        asked: list[str] = []
        from camview.ui import workers as workers_module

        monkeypatch.setattr(
            workers_module,
            "channel_online_status",
            lambda host, *a, **k: asked.append(host) or {},
        )
        self._failing_window(window, fake_instance)

        for _ in range(STATUS_CHECK_AFTER_FAILURES):
            for player in fake_instance.players:
                player.event_manager_obj.trigger(
                    vlc.EventType.MediaPlayerEncounteredError
                )
        for worker in list(window._status_workers):
            worker.wait(2000)

        assert asked, "the device was never consulted"
        window.video_grid.clear()

    def test_a_channel_reported_offline_stops_the_fast_retry(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        self._failing_window(window, fake_instance)
        for player in fake_instance.players:
            player.event_manager_obj.trigger(vlc.EventType.MediaPlayerEncounteredError)

        window._on_channel_status(
            window._nvr_repository.list_all()[0].id, {1: True, 2: False}  # type: ignore[arg-type]
        )

        parked = window.video_grid.tile_at(1)
        assert parked is not None
        assert "sem transmissão" in parked._message_label.text()
        assert parked._reconnect_timer.interval() == OFFLINE_RETRY_S * 1000
        window.video_grid.clear()

    def test_a_late_failure_does_not_unpark_the_cell(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        """The attempt already in flight when the cell was parked still
        reports its failure; that must not restore the fast retry."""
        self._failing_window(window, fake_instance)
        for player in fake_instance.players:
            player.event_manager_obj.trigger(vlc.EventType.MediaPlayerEncounteredError)
        window._on_channel_status(
            window._nvr_repository.list_all()[0].id, {1: True, 2: False}  # type: ignore[arg-type]
        )

        fake_instance.players[1].event_manager_obj.trigger(
            vlc.EventType.MediaPlayerEncounteredError
        )

        parked = window.video_grid.tile_at(1)
        assert parked.is_parked is True  # type: ignore[union-attr]
        assert parked._reconnect_timer.interval() == OFFLINE_RETRY_S * 1000  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_the_long_retry_unparks_the_cell(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        """Parking is never permanent — a repaired camera comes back."""
        self._failing_window(window, fake_instance)
        for player in fake_instance.players:
            player.event_manager_obj.trigger(vlc.EventType.MediaPlayerEncounteredError)
        window._on_channel_status(
            window._nvr_repository.list_all()[0].id, {1: True, 2: False}  # type: ignore[arg-type]
        )
        parked = window.video_grid.tile_at(1)

        parked._connect()  # type: ignore[union-attr]

        assert parked.is_parked is False  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_a_channel_the_device_does_not_list_is_parked_too(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        """A slot the recorder never mentions cannot start transmitting."""
        self._failing_window(window, fake_instance)
        for player in fake_instance.players:
            player.event_manager_obj.trigger(vlc.EventType.MediaPlayerEncounteredError)

        window._on_channel_status(
            window._nvr_repository.list_all()[0].id, {1: True}  # type: ignore[arg-type]
        )

        parked = window.video_grid.tile_at(1)
        assert "não existe" in parked._message_label.text()  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_a_healthy_channel_is_left_alone(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        self._failing_window(window, fake_instance)
        for player in fake_instance.players:
            player.event_manager_obj.trigger(vlc.EventType.MediaPlayerEncounteredError)

        window._on_channel_status(
            window._nvr_repository.list_all()[0].id, {1: True, 2: True}  # type: ignore[arg-type]
        )

        still_retrying = window.video_grid.tile_at(0)
        assert still_retrying._reconnect_timer.interval() < OFFLINE_RETRY_S * 1000  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_no_answer_from_the_device_changes_nothing(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        self._failing_window(window, fake_instance)
        for player in fake_instance.players:
            player.event_manager_obj.trigger(vlc.EventType.MediaPlayerEncounteredError)
        before = window.video_grid.tile_at(1)._reconnect_timer.interval()  # type: ignore[union-attr]

        window._on_channel_status(window._nvr_repository.list_all()[0].id, {})  # type: ignore[arg-type]

        assert window.video_grid.tile_at(1)._reconnect_timer.interval() == before  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_a_playing_cell_is_never_parked(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        self._failing_window(window, fake_instance)
        fake_instance.players[0].event_manager_obj.trigger(
            vlc.EventType.MediaPlayerPlaying
        )

        window._on_channel_status(
            window._nvr_repository.list_all()[0].id, {1: False, 2: False}  # type: ignore[arg-type]
        )

        assert window.video_grid.tile_at(0).status is ConnectionStatus.PLAYING  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_queries_are_throttled_per_device(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        """Sixteen cells failing at once must not mean sixteen HTTP requests."""
        nvr = add_device(window, DeviceType.NVR, 2, name="NVR")

        window._check_channel_status(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
        first = len(window._status_workers)
        window._check_channel_status(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]

        assert len(window._status_workers) == first
        for worker in list(window._status_workers):
            worker.wait(2000)

    def test_a_standalone_camera_is_not_asked_about_channels(
        self, window: MainWindow
    ) -> None:
        camera = add_device(window, DeviceType.CAMERA, 1, name="Portaria")

        window._check_channel_status(camera.id, TEST_PASSWORD)  # type: ignore[arg-type]

        assert window._status_workers == set()
