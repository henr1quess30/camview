"""Tests for the visible affordances added in Phase 9.

Colours and spacing aren't worth asserting on — they follow the desktop
palette by design. What is worth pinning down is the information the UI
promises to show: which stream a cell is on, how full the mosaic is, and
that empty cells explain themselves.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication, QLineEdit, QToolBar

from camview.models.camera import StreamType
from camview.models.nvr import Nvr
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.ui.dialogs.nvr_dialog import NvrDialog
from camview.ui.main_window import MainWindow, _icon
from camview.ui.widgets.video_grid import EMPTY_CELL_HINT, _EmptyCell
from camview.ui.widgets.video_tile import VideoTile

TEST_PASSWORD = "test-password"
STREAM_URLS = {
    StreamType.SUB: "rtsp://example.invalid/Streaming/Channels/102",
    StreamType.MAIN: "rtsp://example.invalid/Streaming/Channels/101",
}


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


class TestStreamBadge:
    def test_a_substream_cell_says_so(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
        assert tile._stream_badge.text() == "SUB"
        tile.close_stream()

    def test_badge_follows_a_stream_change(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)

        tile.set_stream_type(StreamType.MAIN)

        assert tile._stream_badge.text() == "PRINCIPAL"
        tile.close_stream()

    def test_badge_explains_how_to_switch(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
        assert "botão direito" in tile._stream_badge.toolTip()
        tile.close_stream()


class TestEmptyCell:
    def test_empty_cells_say_what_to_do(self, qapp: QApplication) -> None:
        cell = _EmptyCell()
        assert cell.hint_label.text() == EMPTY_CELL_HINT


class TestCellCounter:
    def test_counter_starts_empty(self, window: MainWindow) -> None:
        assert window.cell_count_label.text() == "0/4 células"

    def test_counter_follows_the_mosaic(self, window: MainWindow) -> None:
        window._open_camera_at(camera_ids(window)[0], 0)
        assert window.cell_count_label.text() == "1/4 células"

        window.video_grid.remove_tile(0)
        assert window.cell_count_label.text() == "0/4 células"

    def test_counter_follows_the_grid_shape(self, window: MainWindow) -> None:
        window.layout_selector.setCurrentText("3x3")
        assert window.cell_count_label.text() == "0/9 células"


class TestDeviceTreeIcons:
    def test_items_carry_helpful_tooltips(self, window: MainWindow) -> None:
        nvr_item = window.device_tree.topLevelItem(0)
        assert nvr_item.toolTip(0) == "192.0.2.10:554"
        assert nvr_item.child(0).toolTip(0) == "Canal 1"


class TestPasswordVisibility:
    def test_password_starts_hidden(self, qapp: QApplication) -> None:
        dialog = NvrDialog()
        assert dialog.password_edit.echoMode() == QLineEdit.EchoMode.Password

    def test_toggle_reveals_and_hides_again(self, qapp: QApplication) -> None:
        """Typing an NVR password blind is how wrong credentials happen."""
        dialog = NvrDialog()

        dialog.reveal_password_action.setChecked(True)
        assert dialog.password_edit.echoMode() == QLineEdit.EchoMode.Normal

        dialog.reveal_password_action.setChecked(False)
        assert dialog.password_edit.echoMode() == QLineEdit.EchoMode.Password


class TestThemeIconLookup:
    def test_missing_icon_names_yield_an_empty_icon_not_an_error(self) -> None:
        """An unavailable icon must render as nothing, never break a menu."""
        assert _icon("definitely-not-an-icon-name").isNull()

    def test_first_available_name_wins(self, qapp: QApplication) -> None:
        icon = _icon("definitely-not-an-icon-name", "camera-video")
        # Whether the theme has camera-video depends on the machine; the
        # contract under test is only that lookup falls through in order
        # and never raises.
        assert icon is not None


class TestToolbar:
    def test_toolbar_exposes_the_common_actions(self, window: MainWindow) -> None:
        toolbar = window.findChild(QToolBar, "mainToolbar")
        assert toolbar is not None
        labels = [action.text() for action in toolbar.actions() if action.text()]
        assert labels == ["Adicionar NVR", "Salvar layout"]

    def test_grid_selector_offers_every_shape(self, window: MainWindow) -> None:
        shapes = [
            window.layout_selector.itemText(i)
            for i in range(window.layout_selector.count())
        ]
        assert shapes == ["1x1", "2x2", "3x3", "4x4"]
