"""Integration tests: settings reaching playback, and per-cell stream choice.

The per-cell menu is the fix for choppy mosaics — several NVRs here ship
10 fps substreams against 25 on the main stream.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

from camview.database.repositories import SettingsRepository
from camview.models.camera import StreamType
from camview.models.nvr import Nvr
from camview.models.settings import AppSettings, MosaicStream
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.services.settings import save_settings
from camview.ui.dialogs.settings_dialog import SettingsDialog
from camview.ui.main_window import MainWindow

TEST_PASSWORD = "test-password"


@pytest.fixture
def window(
    qapp: QApplication,
    db_connection: sqlite3.Connection,
    fake_keyring: object,
    fake_instance: FakeInstance,
) -> MainWindow:
    win = MainWindow(connection=db_connection)
    nvr = win._nvr_repository.create(
        Nvr(name="NVR", host="192.0.2.10", username="admin", channel_count=4)
    )
    set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
    for camera in generate_missing_channel_cameras(nvr.id, 4):  # type: ignore[arg-type]
        win._camera_repository.create(camera)
    win.device_tree.refresh()
    return win


def camera_ids(window: MainWindow) -> list[int]:
    nvr = window._nvr_repository.list_all()[0]
    return [c.id for c in window._camera_repository.list_by_nvr(nvr.id)]  # type: ignore[arg-type,misc]


class TestMosaicStreamSetting:
    def test_default_keeps_mosaics_on_the_substream(self, window: MainWindow) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        assert window.video_grid.tile_at(0).url.endswith("/102")  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_main_stream_setting_applies_to_new_cells(
        self, window: MainWindow
    ) -> None:
        window._settings = AppSettings(mosaic_stream=MosaicStream.MAIN)

        window._open_camera_at(camera_ids(window)[0], 0)

        tile = window.video_grid.tile_at(0)
        assert tile is not None
        assert tile.stream_type is StreamType.MAIN
        assert tile.url.endswith("/101")
        window.video_grid.clear()

    def test_nvr_default_setting_follows_the_device(self, window: MainWindow) -> None:
        window._settings = AppSettings(mosaic_stream=MosaicStream.NVR_DEFAULT)
        nvr = window._nvr_repository.list_all()[0]
        assert nvr.default_stream is StreamType.MAIN  # the NVR's own default

        window._open_camera_at(camera_ids(window)[0], 0)

        assert window.video_grid.tile_at(0).stream_type is StreamType.MAIN  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_setting_survives_a_restart(
        self, window: MainWindow, db_connection: sqlite3.Connection
    ) -> None:
        save_settings(
            SettingsRepository(db_connection),
            AppSettings(mosaic_stream=MosaicStream.MAIN),
        )

        reopened = MainWindow(connection=db_connection)

        assert reopened._settings.mosaic_stream is MosaicStream.MAIN


class TestPlaybackSettingsReachTheTiles:
    def test_tiles_are_built_with_the_configured_options(
        self, window: MainWindow
    ) -> None:
        window._settings = AppSettings(
            network_caching_ms=900, rtsp_transport_tcp=False, mute_audio=False
        )

        window._open_camera_at(camera_ids(window)[0], 0)

        options = window.video_grid.tile_at(0)._playback_options.to_media_options()  # type: ignore[union-attr]
        assert "network-caching=900" in options
        assert "rtsp-tcp" not in options
        window.video_grid.clear()

    def test_reconnect_can_be_turned_off(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        import vlc

        window._settings = AppSettings(reconnect_enabled=False)
        window._open_camera_at(camera_ids(window)[0], 0)
        tile = window.video_grid.tile_at(0)
        assert tile is not None
        tile._connect()

        fake_instance.players[-1].event_manager_obj.trigger(
            vlc.EventType.MediaPlayerEncounteredError
        )

        assert tile._reconnect_timer.isActive() is False
        assert "Falha" in tile._message_label.text()
        window.video_grid.clear()

    def test_reconnect_interval_ceiling_is_applied(
        self, window: MainWindow, fake_instance: FakeInstance
    ) -> None:
        import vlc

        window._settings = AppSettings(max_reconnect_delay_s=10)
        window._open_camera_at(camera_ids(window)[0], 0)
        tile = window.video_grid.tile_at(0)
        assert tile is not None
        tile._connect()
        event_manager = fake_instance.players[-1].event_manager_obj

        intervals = []
        for _ in range(4):
            event_manager.trigger(vlc.EventType.MediaPlayerEncounteredError)
            intervals.append(tile._reconnect_timer.interval())

        assert intervals == [2000, 5000, 10000, 10000]
        window.video_grid.clear()


class TestPerCellStreamChoice:
    def test_choosing_a_stream_switches_only_that_cell(
        self, window: MainWindow
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        window._open_camera_at(camera_ids(window)[1], 1)

        window.video_grid.set_stream_type(0, StreamType.MAIN)

        assert window.video_grid.tile_at(0).stream_type is StreamType.MAIN  # type: ignore[union-attr]
        assert window.video_grid.tile_at(1).stream_type is StreamType.SUB  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_tile_signal_is_wired_to_the_grid(self, window: MainWindow) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        tile = window.video_grid.tile_at(0)
        assert tile is not None

        tile.streamTypeRequested.emit(StreamType.MAIN)

        assert tile.stream_type is StreamType.MAIN
        window.video_grid.clear()

    def test_choice_made_while_maximized_survives_restore(
        self, window: MainWindow
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        window.video_grid.maximize(0)

        window.video_grid.set_stream_type(0, StreamType.SUB)
        window.video_grid.restore()

        assert window.video_grid.tile_at(0).stream_type is StreamType.SUB  # type: ignore[union-attr]
        window.video_grid.clear()

    def test_choice_is_saved_with_the_layout(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        window._open_camera_at(camera_ids(window)[1], 1)
        window.video_grid.set_stream_type(1, StreamType.MAIN)
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("Fábrica", True))
        )

        window._save_layout_as()

        saved = window._layout_repository.get_by_name("Fábrica")
        items = window._layout_repository.get_items(saved.id)  # type: ignore[arg-type,union-attr]
        assert [(i.position, i.stream_type) for i in items] == [
            (0, StreamType.SUB),
            (1, StreamType.MAIN),
        ]
        window.video_grid.clear()


class TestSettingsDialogWiring:
    def test_accepting_the_dialog_persists_and_applies(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_exec(dialog: SettingsDialog) -> int:
            dialog.network_caching_spin.setValue(1000)
            dialog.mosaic_stream_combo.setCurrentIndex(
                dialog.mosaic_stream_combo.findData(MosaicStream.MAIN.value)
            )
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(SettingsDialog, "exec", fake_exec)

        window._edit_settings()

        assert window._settings.network_caching_ms == 1000
        assert window._settings.mosaic_stream is MosaicStream.MAIN
        reopened = MainWindow(connection=window._connection)
        assert reopened._settings.network_caching_ms == 1000

    def test_cancelling_changes_nothing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = window._settings
        monkeypatch.setattr(
            SettingsDialog,
            "exec",
            lambda self: QDialog.DialogCode.Rejected,
        )

        window._edit_settings()

        assert window._settings == before


class TestStartupSettings:
    def test_restore_last_layout_can_be_turned_off(
        self,
        window: MainWindow,
        db_connection: sqlite3.Connection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("Fábrica", True))
        )
        window._save_layout_as()
        window.close()
        save_settings(
            SettingsRepository(db_connection), AppSettings(restore_last_layout=False)
        )

        reopened = MainWindow(connection=db_connection)

        assert reopened.video_grid.tiles() == {}

    def test_start_maximized_is_applied(
        self, window: MainWindow, db_connection: sqlite3.Connection
    ) -> None:
        from PySide6.QtCore import Qt

        save_settings(
            SettingsRepository(db_connection), AppSettings(start_maximized=True)
        )

        reopened = MainWindow(connection=db_connection)

        assert bool(reopened.windowState() & Qt.WindowState.WindowMaximized)
