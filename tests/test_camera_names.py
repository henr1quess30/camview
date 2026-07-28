"""Tests for adopting the names a device uses for its channels.

The rule under test is one-way: a placeholder like ``Canal 7`` is
replaced by the real label an operator typed into the recorder, never the
other way round — a recorder that forgot its labels must not be able to
wipe good names here.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication

from camview.models.camera import Camera
from camview.models.nvr import Nvr
from camview.services.credentials import set_nvr_password
from camview.services.hikvision import DiscoveredChannel
from camview.services.rtsp import (
    camera_names_to_update,
    generate_missing_channel_cameras,
    is_generic_camera_name,
)
from camview.ui.main_window import MainWindow

TEST_PASSWORD = "test-password"


def camera(channel: int, name: str) -> Camera:
    return Camera(nvr_id=1, channel_number=channel, name=name, id=channel)


class TestGenericNameDetection:
    @pytest.mark.parametrize(
        "name",
        ["Canal 3", "canal 12", "Camera 01", "câmera 2", "IPCamera 01", "Chn5", "Canal"],
    )
    def test_placeholders_are_recognised(self, name: str) -> None:
        assert is_generic_camera_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "MONTAGEM",
            "Entrada teatro",
            "Sala 3D",
            "Canal do Vestiario",
            "Portao princ. ext.",
            "Corredor 2 - fundos",
        ],
    )
    def test_real_names_are_not(self, name: str) -> None:
        assert is_generic_camera_name(name) is False


class TestNameReconciliation:
    def test_a_placeholder_is_replaced_by_the_real_name(self) -> None:
        cameras = [camera(1, "Canal 1")]

        updated = camera_names_to_update(cameras, {1: "MONTAGEM"})

        assert [c.name for c in updated] == ["MONTAGEM"]

    def test_a_real_name_is_never_replaced_by_a_placeholder(self) -> None:
        """A recorder that lost its labels must not wipe good ones here."""
        cameras = [camera(1, "Portaria")]

        updated = camera_names_to_update(cameras, {1: "Camera 01"})

        assert updated == []
        assert cameras[0].name == "Portaria"

    def test_a_renamed_channel_is_followed(self) -> None:
        cameras = [camera(1, "Entrada")]

        updated = camera_names_to_update(cameras, {1: "Entrada teatro"})

        assert [c.name for c in updated] == ["Entrada teatro"]

    def test_matching_names_are_left_alone(self) -> None:
        cameras = [camera(1, "MONTAGEM")]

        assert camera_names_to_update(cameras, {1: "MONTAGEM"}) == []

    def test_channels_the_device_did_not_mention_are_untouched(self) -> None:
        cameras = [camera(1, "Canal 1"), camera(2, "Canal 2")]

        updated = camera_names_to_update(cameras, {1: "MONTAGEM"})

        assert len(updated) == 1
        assert cameras[1].name == "Canal 2"

    def test_an_empty_name_is_ignored(self) -> None:
        cameras = [camera(1, "Canal 1")]

        assert camera_names_to_update(cameras, {1: "   "}) == []


class TestSyncingADevice:
    @pytest.fixture
    def window(
        self,
        qapp: QApplication,
        db_connection: sqlite3.Connection,
        fake_keyring: object,
        fake_instance: FakeInstance,
    ) -> MainWindow:
        win = MainWindow(connection=db_connection)
        nvr = win._nvr_repository.create(
            Nvr(name="NVR", host="192.0.2.10", username="admin", channel_count=3)
        )
        set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
        for cam in generate_missing_channel_cameras(nvr.id, 3):  # type: ignore[arg-type]
            win._camera_repository.create(cam)
        win.device_tree.refresh()
        return win

    def stored_names(self, window: MainWindow) -> list[str]:
        nvr = window._nvr_repository.list_all()[0]
        return [c.name for c in window._camera_repository.list_by_nvr(nvr.id)]  # type: ignore[arg-type]

    def test_discovery_adopts_the_device_names(self, window: MainWindow) -> None:
        nvr = window._nvr_repository.list_all()[0]

        window._on_device_synced(
            nvr.id,  # type: ignore[arg-type]
            [
                DiscoveredChannel(1, "MONTAGEM"),
                DiscoveredChannel(2, "Copa"),
                DiscoveredChannel(3, "Canal 3"),
            ],
        )

        assert self.stored_names(window) == ["MONTAGEM", "Copa", "Canal 3"]

    def test_it_reports_what_changed(self, window: MainWindow) -> None:
        nvr = window._nvr_repository.list_all()[0]

        window._on_device_synced(
            nvr.id,  # type: ignore[arg-type]
            [DiscoveredChannel(1, "MONTAGEM"), DiscoveredChannel(4, "Nova")],
        )

        message = window.statusBar().currentMessage()
        assert "1 novo" in message
        assert "renomeado" in message

    def test_new_channels_are_created_too(self, window: MainWindow) -> None:
        nvr = window._nvr_repository.list_all()[0]

        window._on_device_synced(
            nvr.id,  # type: ignore[arg-type]
            [DiscoveredChannel(n, f"Cam {n}") for n in range(1, 6)],
        )

        assert len(self.stored_names(window)) == 5

    def test_the_sidebar_is_refreshed(self, window: MainWindow) -> None:
        nvr = window._nvr_repository.list_all()[0]

        window._on_device_synced(
            nvr.id, [DiscoveredChannel(1, "MONTAGEM")]  # type: ignore[arg-type]
        )

        assert window.device_tree.topLevelItem(0).child(0).text(0) == "MONTAGEM"

    def test_a_failed_query_only_reports(self, window: MainWindow) -> None:
        nvr = window._nvr_repository.list_all()[0]

        window._on_device_sync_failed(nvr.id, "Usuário ou senha incorretos.")  # type: ignore[arg-type]

        assert "senha" in window.statusBar().currentMessage()
        assert self.stored_names(window) == ["Canal 1", "Canal 2", "Canal 3"]

    def test_a_device_without_a_password_is_not_queried(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from camview.ui import main_window as mw
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(mw, "get_nvr_password", lambda _id: None)
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
        nvr = window._nvr_repository.list_all()[0]

        window._sync_device(nvr.id)  # type: ignore[arg-type]

        assert window._sync_workers == set()

    def test_the_context_menu_offers_it(self, window: MainWindow) -> None:
        """It was previously buried in Editar → Detectar canais → OK."""
        nvr = window._nvr_repository.list_all()[0]

        menu = window.build_device_context_menu(nvr.id)  # type: ignore[arg-type]

        labels = [action.text() for action in menu.actions() if action.text()]
        assert labels == [
            "Editar...",
            "Atualizar canais e nomes",
            "Remover",
            "Gerenciar dispositivos...",
        ]

    def test_the_menu_action_starts_the_query(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started: list[int] = []
        monkeypatch.setattr(
            MainWindow, "_sync_device", lambda _self, nvr_id: started.append(nvr_id)
        )
        nvr = window._nvr_repository.list_all()[0]

        for action in window.build_device_context_menu(nvr.id).actions():  # type: ignore[arg-type]
            if action.text() == "Atualizar canais e nomes":
                action.trigger()

        assert started == [nvr.id]
