"""Tests for the settings model, its persistence and the settings dialog."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtWidgets import QApplication

from camview.database.repositories import SettingsRepository
from camview.models.camera import StreamType
from camview.models.settings import (
    AppSettings,
    MosaicStream,
    settings_from_mapping,
    settings_to_mapping,
)
from camview.services.settings import (
    load_settings,
    playback_options_for,
    save_settings,
)
from camview.ui.dialogs.settings_dialog import SettingsDialog


class TestDefaults:
    def test_defaults_match_the_behaviour_shipped_before_settings_existed(
        self,
    ) -> None:
        settings = AppSettings()
        assert settings.network_caching_ms == 300
        assert settings.rtsp_transport_tcp is True
        assert settings.mute_audio is True
        assert settings.reconnect_enabled is True
        assert settings.mosaic_stream is MosaicStream.SUB
        assert settings.restore_last_layout is True
        assert settings.start_maximized is False
        assert settings.log_dir is None

    def test_empty_database_yields_defaults(
        self, db_connection: sqlite3.Connection
    ) -> None:
        assert load_settings(SettingsRepository(db_connection)) == AppSettings()


class TestMosaicStreamResolution:
    def test_explicit_choices_win_over_the_nvr_default(self) -> None:
        assert MosaicStream.MAIN.resolve(StreamType.SUB) is StreamType.MAIN
        assert MosaicStream.SUB.resolve(StreamType.MAIN) is StreamType.SUB

    def test_nvr_default_defers_to_the_device(self) -> None:
        assert MosaicStream.NVR_DEFAULT.resolve(StreamType.MAIN) is StreamType.MAIN
        assert MosaicStream.NVR_DEFAULT.resolve(StreamType.SUB) is StreamType.SUB


class TestBackoffSchedule:
    def test_schedule_is_capped_at_the_configured_maximum(self) -> None:
        assert AppSettings(max_reconnect_delay_s=30).backoff_schedule() == (
            2,
            5,
            10,
            30,
        )
        assert AppSettings(max_reconnect_delay_s=10).backoff_schedule() == (2, 5, 10)

    def test_a_very_short_maximum_still_yields_a_usable_schedule(self) -> None:
        schedule = AppSettings(max_reconnect_delay_s=1).backoff_schedule()
        assert schedule == (1,)


class TestPersistence:
    def test_round_trip_through_the_database(
        self, db_connection: sqlite3.Connection
    ) -> None:
        repository = SettingsRepository(db_connection)
        original = AppSettings(
            network_caching_ms=1500,
            rtsp_transport_tcp=False,
            mute_audio=False,
            reconnect_enabled=False,
            max_reconnect_delay_s=45,
            mosaic_stream=MosaicStream.MAIN,
            start_maximized=True,
            restore_last_layout=False,
            log_dir=Path("/tmp/camview-logs"),
        )

        save_settings(repository, original)

        assert load_settings(repository) == original

    def test_clearing_the_log_dir_removes_the_row(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """Absence is what 'use the default location' means."""
        repository = SettingsRepository(db_connection)
        save_settings(repository, AppSettings(log_dir=Path("/tmp/camview-logs")))

        save_settings(repository, AppSettings(log_dir=None))

        assert load_settings(repository).log_dir is None
        assert "app/log_dir" not in repository.get_all()

    def test_saving_settings_leaves_other_rows_alone(
        self, db_connection: sqlite3.Connection
    ) -> None:
        repository = SettingsRepository(db_connection)
        repository.set("window/geometry", "abc")

        save_settings(repository, AppSettings(start_maximized=True))

        assert repository.get("window/geometry") == "abc"


class TestParsingIsForgiving:
    def test_nonsense_values_fall_back_to_defaults(self) -> None:
        parsed = settings_from_mapping(
            {
                "app/network_caching_ms": "muito",
                "app/rtsp_transport_tcp": "talvez",
                "app/max_reconnect_delay_s": "",
                "app/mosaic_stream": "holograma",
            }
        )
        assert parsed == AppSettings()

    def test_out_of_range_values_are_clamped(self) -> None:
        parsed = settings_from_mapping({"app/network_caching_ms": "999999"})
        assert parsed.network_caching_ms == 10_000

    def test_boolean_spellings_are_accepted(self) -> None:
        assert settings_from_mapping({"app/start_maximized": "TRUE"}).start_maximized
        assert settings_from_mapping({"app/start_maximized": "1"}).start_maximized
        assert not settings_from_mapping({"app/mute_audio": "no"}).mute_audio

    def test_mapping_keys_are_namespaced(self) -> None:
        rows = settings_to_mapping(AppSettings())
        assert all(key.startswith("app/") for key in rows)


class TestPlaybackOptions:
    def test_settings_reach_libvlc_media_options(self) -> None:
        options = playback_options_for(
            AppSettings(
                network_caching_ms=800, rtsp_transport_tcp=False, mute_audio=False
            )
        ).to_media_options()

        assert "network-caching=800" in options
        assert "rtsp-tcp" not in options, "UDP was requested"
        assert "no-audio" not in options


class TestSettingsDialog:
    def test_shows_the_current_settings(self, qapp: QApplication) -> None:
        settings = AppSettings(
            network_caching_ms=750,
            rtsp_transport_tcp=False,
            mosaic_stream=MosaicStream.MAIN,
            start_maximized=True,
        )

        dialog = SettingsDialog(settings)

        assert dialog.network_caching_spin.value() == 750
        assert dialog.transport_combo.currentData() is False
        assert dialog.maximized_check.isChecked() is True

    def test_edits_round_trip_through_the_dialog(self, qapp: QApplication) -> None:
        dialog = SettingsDialog(AppSettings())

        dialog.network_caching_spin.setValue(1200)
        dialog.mute_check.setChecked(False)
        dialog.reconnect_check.setChecked(False)
        dialog.log_dir_edit.setText("/tmp/camview-logs")

        result = dialog.result_settings()
        assert result.network_caching_ms == 1200
        assert result.mute_audio is False
        assert result.reconnect_enabled is False
        assert result.log_dir == Path("/tmp/camview-logs")

    def test_mosaic_stream_keeps_its_enum_type(self, qapp: QApplication) -> None:
        """MosaicStream inherits str, so a raw currentData() would slip through."""
        dialog = SettingsDialog(AppSettings())
        dialog.mosaic_stream_combo.setCurrentIndex(
            dialog.mosaic_stream_combo.findData(MosaicStream.NVR_DEFAULT.value)
        )

        result = dialog.result_settings()

        assert isinstance(result.mosaic_stream, MosaicStream)
        assert result.mosaic_stream is MosaicStream.NVR_DEFAULT

    def test_blank_log_dir_means_default(self, qapp: QApplication) -> None:
        dialog = SettingsDialog(AppSettings(log_dir=Path("/tmp/x")))
        dialog.log_dir_edit.setText("   ")
        assert dialog.result_settings().log_dir is None

    def test_disabling_reconnect_greys_out_the_interval(
        self, qapp: QApplication
    ) -> None:
        dialog = SettingsDialog(AppSettings())
        dialog.reconnect_check.setChecked(False)
        assert dialog.max_delay_spin.isEnabled() is False
