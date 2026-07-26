"""Tests for the NVR dialog's background work and its result handling.

Both workers are QThreads so the GUI never blocks on the network. Their
``run()`` is called directly here — starting real threads would make the
tests depend on timing, and what matters is the mapping from service
outcome to signal, and from signal to what the user sees.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from camview.services.hikvision import DiscoveredChannel, DiscoveryError
from camview.ui import workers as workers_module
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.workers import ChannelDiscoveryWorker, ConnectionTestWorker

# RFC 5737 documentation address: never a real device.
TEST_HOST = "192.0.2.10"


class TestConnectionTestWorker:
    def test_reachable_device_reports_success(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            workers_module, "check_tcp_connection", lambda *_a, **_k: None
        )
        worker = ConnectionTestWorker(TEST_HOST, 554)
        outcomes: list[str] = []
        worker.succeeded.connect(lambda: outcomes.append("ok"))
        worker.failed.connect(lambda msg: outcomes.append(f"fail:{msg}"))

        worker.run()

        assert outcomes == ["ok"]

    def test_network_error_is_reported_not_raised(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*_args: object, **_kwargs: object) -> None:
            raise ConnectionRefusedError("Connection refused")

        monkeypatch.setattr(workers_module, "check_tcp_connection", refuse)
        worker = ConnectionTestWorker(TEST_HOST, 554)
        failures: list[str] = []
        worker.failed.connect(failures.append)

        worker.run()

        assert failures == ["Connection refused"]


class TestChannelDiscoveryWorker:
    def test_discovered_channels_are_handed_over(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        found = [DiscoveredChannel(1, "Portaria"), DiscoveredChannel(3, "Copa")]
        monkeypatch.setattr(
            workers_module, "discover_channels", lambda *_a, **_k: found
        )
        worker = ChannelDiscoveryWorker(TEST_HOST, "admin", "senha-falsa")
        results: list[list[DiscoveredChannel]] = []
        worker.succeeded.connect(results.append)

        worker.run()

        assert results == [found]

    def test_discovery_error_is_reported_not_raised(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args: object, **_kwargs: object) -> None:
            raise DiscoveryError("Usuário ou senha incorretos.")

        monkeypatch.setattr(workers_module, "discover_channels", fail)
        worker = ChannelDiscoveryWorker(TEST_HOST, "admin", "senha-falsa")
        failures: list[str] = []
        worker.failed.connect(failures.append)

        worker.run()

        assert failures == ["Usuário ou senha incorretos."]


class TestDialogFeedback:
    @pytest.fixture
    def dialog(self, qapp: QApplication) -> NvrDialog:
        dlg = NvrDialog()
        dlg.host_edit.setText(TEST_HOST)
        dlg.username_edit.setText("admin")
        dlg.password_edit.setText("senha-falsa")
        return dlg

    def test_testing_without_an_address_says_so_instead_of_connecting(
        self, dialog: NvrDialog
    ) -> None:
        dialog.host_edit.setText("")

        dialog._on_test_connection()

        assert "Informe o endereço" in dialog.test_result_label.text()
        assert dialog._test_worker is None

    def test_detecting_without_credentials_says_so(self, dialog: NvrDialog) -> None:
        dialog.password_edit.setText("")

        dialog._on_detect_channels()

        assert "senha" in dialog.test_result_label.text()
        assert dialog._discovery_worker is None

    def test_success_and_failure_are_shown_to_the_user(
        self, dialog: NvrDialog
    ) -> None:
        dialog._on_test_succeeded()
        assert "bem-sucedida" in dialog.test_result_label.text()

        dialog._on_test_failed("Connection refused")
        assert "Connection refused" in dialog.test_result_label.text()

    def test_discovery_mirrors_the_device_channel_count(
        self, dialog: NvrDialog
    ) -> None:
        """The device is the authority; leaving the spin box disagreeing lies."""
        dialog.channel_count_spin.setValue(4)

        dialog._on_discovery_succeeded(
            [DiscoveredChannel(1, "Portaria"), DiscoveredChannel(16, "Copa")]
        )

        assert dialog.channel_count_spin.value() == 16
        assert dialog.discovered_channels is not None
        assert "2 canais detectados" in dialog.test_result_label.text()

    def test_discovery_counts_the_named_channels(self, dialog: NvrDialog) -> None:
        dialog._on_discovery_succeeded(
            [DiscoveredChannel(1, "Portaria"), DiscoveredChannel(2, "Canal 2")]
        )

        assert "1 com nome configurado" in dialog.test_result_label.text()

    def test_failed_discovery_clears_any_previous_result(
        self, dialog: NvrDialog
    ) -> None:
        dialog._on_discovery_succeeded([DiscoveredChannel(1, "Portaria")])

        dialog._on_discovery_failed("dispositivo inacessível")

        assert dialog.discovered_channels is None
        assert "Não foi possível detectar canais" in dialog.test_result_label.text()
