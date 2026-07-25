"""Phase 8: every failure path reports instead of crashing.

The rule being enforced here is the project's: no unhandled exception may
take the app down, every failure is logged with technical detail, and the
user gets a sentence naming what went wrong.
"""

from __future__ import annotations

import logging
import sqlite3
import sys

import pytest
import vlc
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from camview.app import _install_excepthook
from camview.models.camera import StreamType
from camview.models.nvr import Nvr
from camview.services.credentials import CredentialsError, set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.services.stream_manager import VlcUnavailableError
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.main_window import MainWindow
from camview.ui.widgets.video_tile import (
    CREDENTIAL_HINT_AFTER_FAILURES,
    VideoTile,
)

TEST_PASSWORD = "test-password"
DB_FAILURE = sqlite3.OperationalError("database disk image is malformed")


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


@pytest.fixture
def captured_errors(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    """Collect what would have been shown in a critical message box."""
    shown: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a, **k: shown.append(a))
    )
    return shown


def raise_db_error(*_args: object, **_kwargs: object) -> None:
    raise DB_FAILURE


class TestDatabaseFailuresDuringUse:
    def test_reading_devices_reports_instead_of_crashing(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        captured_errors: list[tuple[object, ...]],
    ) -> None:
        monkeypatch.setattr(window.device_tree, "refresh", raise_db_error)

        window._refresh_device_tree()  # must not raise

        assert len(captured_errors) == 1
        assert "NVRs" in captured_errors[0][2]

    def test_opening_a_camera_reports_instead_of_crashing(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        captured_errors: list[tuple[object, ...]],
    ) -> None:
        monkeypatch.setattr(window._camera_repository, "get", raise_db_error)

        window._open_camera_at(1, 0)

        assert len(captured_errors) == 1
        assert window.video_grid.tiles() == {}

    def test_opening_a_whole_nvr_reports_instead_of_crashing(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        captured_errors: list[tuple[object, ...]],
    ) -> None:
        nvr = window._nvr_repository.list_all()[0]
        monkeypatch.setattr(window._camera_repository, "list_by_nvr", raise_db_error)

        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]

        assert len(captured_errors) == 1
        assert window.video_grid.tiles() == {}

    def test_a_failed_layout_load_leaves_the_mosaic_alone(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        captured_errors: list[tuple[object, ...]],
    ) -> None:
        """Reporting must happen before anything on screen is torn down."""
        from camview.models.layout import Layout

        layout = window._layout_repository.create(
            Layout(name="Fábrica", rows=2, columns=2)
        )
        window._open_camera_at(camera_ids(window)[0], 0)
        monkeypatch.setattr(window._layout_repository, "get_items", raise_db_error)

        window._load_layout(layout.id)  # type: ignore[arg-type]

        assert len(captured_errors) == 1
        assert list(window.video_grid.tiles()) == [0], "live cells were kept"
        window.video_grid.clear()

    def test_saving_the_session_never_blocks_closing(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(window._settings_repository, "set", raise_db_error)

        window.close()  # must not raise

    def test_reading_the_session_never_blocks_startup(
        self, db_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from camview.database.repositories import SettingsRepository

        monkeypatch.setattr(SettingsRepository, "get_all", raise_db_error)

        MainWindow(connection=db_connection)  # must not raise


class TestUnusableDatabaseAtStartup:
    def test_startup_failure_is_explained_and_exits_nonzero(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from camview import __main__ as entry

        shown: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            QMessageBox, "critical", staticmethod(lambda *a, **k: shown.append(a))
        )

        code = entry._report_startup_failure(tmp_path / "camview.db", DB_FAILURE)

        assert code == 1
        message = shown[0][2]
        assert "banco de dados" in message
        assert "camview.db" in message
        assert "keyring" in message, "tell the user their passwords are safe"


class TestVlcUnavailable:
    def test_missing_vlc_is_reported_once_not_once_per_camera(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        captured_errors: list[tuple[object, ...]],
    ) -> None:
        from camview.ui import main_window as mw

        def unavailable() -> None:
            raise VlcUnavailableError("libVLC não foi encontrado.")

        monkeypatch.setattr(mw, "get_vlc_instance", unavailable)
        nvr = window._nvr_repository.list_all()[0]

        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]
        window._open_camera_at(camera_ids(window)[0], 0)

        assert len(captured_errors) == 1, "one message for the install, not per cell"
        assert window.video_grid.tiles() == {}


class TestCredentialFailures:
    def test_keyring_failure_is_reported(
        self,
        window: MainWindow,
        monkeypatch: pytest.MonkeyPatch,
        captured_errors: list[tuple[object, ...]],
    ) -> None:
        from camview.ui import main_window as mw

        def broken(_id: int) -> str:
            raise CredentialsError("Keyring indisponível.")

        monkeypatch.setattr(mw, "get_nvr_password", broken)

        window._open_camera_at(camera_ids(window)[0], 0)

        assert len(captured_errors) == 1
        assert window.video_grid.tiles() == {}

    def test_keyring_failure_stays_silent_while_restoring(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from camview.ui import main_window as mw

        def broken(_id: int) -> str:
            raise CredentialsError("Keyring indisponível.")

        monkeypatch.setattr(mw, "get_nvr_password", broken)
        shown: list[object] = []
        monkeypatch.setattr(
            QMessageBox, "critical", staticmethod(lambda *a, **k: shown.append(a))
        )
        nvr = window._nvr_repository.list_all()[0]

        assert window._nvr_password_or_warn(nvr, quiet=True) is None
        assert shown == []


class TestNvrFormValidation:
    @pytest.fixture
    def dialog(self, qapp: QApplication) -> NvrDialog:
        dlg = NvrDialog()
        dlg.name_edit.setText("NVR")
        dlg.host_edit.setText("192.0.2.10")
        dlg.username_edit.setText("admin")
        dlg.password_edit.setText(TEST_PASSWORD)
        return dlg

    @pytest.mark.parametrize(
        "host",
        ["rtsp://192.0.2.10:554/Streaming", "192.0.2.10:554", "10.0.0 .5", "a@b"],
    )
    def test_a_url_pasted_as_host_is_rejected(
        self, dialog: NvrDialog, monkeypatch: pytest.MonkeyPatch, host: str
    ) -> None:
        warnings: list[tuple[object, ...]] = []
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
        )
        dialog.host_edit.setText(host)

        dialog._on_accept()

        assert dialog.result() != QDialog.DialogCode.Accepted
        assert len(warnings) == 1

    def test_a_plain_hostname_is_accepted(self, dialog: NvrDialog) -> None:
        dialog.host_edit.setText("nvr-portaria.local")
        dialog._on_accept()
        assert dialog.result() == QDialog.DialogCode.Accepted

    def test_empty_password_asks_for_confirmation(
        self, dialog: NvrDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silence here becomes cells that refuse to open with no cause shown."""
        dialog.password_edit.setText("")
        asked: list[tuple[object, ...]] = []

        def question(*args: object, **_kwargs: object) -> QMessageBox.StandardButton:
            asked.append(args)
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", staticmethod(question))

        dialog._on_accept()

        assert len(asked) == 1
        assert dialog.result() != QDialog.DialogCode.Accepted

    def test_empty_password_can_be_confirmed(
        self, dialog: NvrDialog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dialog.password_edit.setText("")
        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )

        dialog._on_accept()

        assert dialog.result() == QDialog.DialogCode.Accepted


class TestRepeatedFailuresPointAtCredentials:
    """Wrong credentials look like an unreachable device from libVLC's side.

    The app can't tell them apart, but only one of the two has a
    consequence worth warning about: repeated failed logins get this
    machine's IP locked out by the NVR.
    """

    @staticmethod
    def _failing_tile(
        qapp: QApplication, fake_instance: FakeInstance
    ) -> tuple[VideoTile, object]:
        tile = VideoTile(
            title="Canal 1",
            stream_urls={StreamType.SUB: "rtsp://192.0.2.10/Streaming/Channels/102"},
        )
        tile._connect()
        return tile, fake_instance.players[0].event_manager_obj

    def test_first_failures_do_not_speculate(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile, events = self._failing_tile(qapp, fake_instance)

        events.trigger(vlc.EventType.MediaPlayerEncounteredError)

        assert "senha" not in tile._message_label.text().lower()
        tile.close_stream()

    def test_repeated_failures_warn_about_the_lockout_risk(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile, events = self._failing_tile(qapp, fake_instance)

        for _ in range(CREDENTIAL_HINT_AFTER_FAILURES):
            events.trigger(vlc.EventType.MediaPlayerEncounteredError)

        message = tile._message_label.text()
        assert "Verifique usuário e senha" in message
        assert "bloquear" in message
        tile.close_stream()

    def test_a_successful_connection_clears_the_suspicion(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile, events = self._failing_tile(qapp, fake_instance)
        for _ in range(CREDENTIAL_HINT_AFTER_FAILURES):
            events.trigger(vlc.EventType.MediaPlayerEncounteredError)

        events.trigger(vlc.EventType.MediaPlayerPlaying)
        events.trigger(vlc.EventType.MediaPlayerEncounteredError)

        assert "senha" not in tile._message_label.text().lower()
        tile.close_stream()


class TestExceptHook:
    def test_uncaught_exceptions_are_logged_not_fatal(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        original = sys.excepthook
        _install_excepthook()
        try:
            with caplog.at_level(logging.CRITICAL):
                try:
                    raise RuntimeError("boom")
                except RuntimeError:
                    sys.excepthook(*sys.exc_info())  # type: ignore[misc]
        finally:
            sys.excepthook = original

        assert "Unhandled exception" in caplog.text
        assert "boom" in caplog.text

    def test_keyboard_interrupt_still_reaches_the_default_hook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = sys.excepthook
        forwarded: list[object] = []
        monkeypatch.setattr(
            sys, "__excepthook__", lambda *a: forwarded.append(a)
        )
        _install_excepthook()
        try:
            try:
                raise KeyboardInterrupt
            except KeyboardInterrupt:
                sys.excepthook(*sys.exc_info())  # type: ignore[misc]
        finally:
            sys.excepthook = original

        assert len(forwarded) == 1
