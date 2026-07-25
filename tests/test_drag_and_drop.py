"""Tests for drag-and-drop: the tree as source, the grid as target.

These paths are pure Qt event plumbing, but they are how cameras get onto
the mosaic in the first place — worth pinning down, mime type included,
since a mismatch there fails silently.
"""

from __future__ import annotations

import sqlite3

import pytest
from fakes import FakeInstance
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from camview.database.repositories import CameraRepository, NvrRepository
from camview.models.camera import Camera, StreamType
from camview.models.nvr import Nvr
from camview.ui.widgets.device_tree import (
    CAMERA_ID_ROLE,
    CAMERA_MIME_TYPE,
    DeviceTree,
)
from camview.ui.widgets.video_grid import VideoGrid
from camview.ui.widgets.video_tile import GRID_POSITION_MIME_TYPE, VideoTile

STREAM_URLS = {
    StreamType.SUB: "rtsp://192.0.2.10/Streaming/Channels/102",
    StreamType.MAIN: "rtsp://192.0.2.10/Streaming/Channels/101",
}


def camera_mime(camera_id: int) -> QMimeData:
    mime = QMimeData()
    mime.setData(CAMERA_MIME_TYPE, str(camera_id).encode("ascii"))
    return mime


def position_mime(position: int) -> QMimeData:
    mime = QMimeData()
    mime.setData(GRID_POSITION_MIME_TYPE, str(position).encode("ascii"))
    return mime


def drop_at(grid: VideoGrid, mime: QMimeData, point: QPoint) -> QDropEvent:
    event = QDropEvent(
        QPointF(point),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    grid.dropEvent(event)
    return event


def cell_center(grid: VideoGrid, position: int) -> QPoint:
    row, column = divmod(position, grid.columns)
    item = grid._layout.itemAtPosition(row, column)
    assert item is not None
    return item.geometry().center()


@pytest.fixture
def grid(qapp: QApplication, fake_instance: FakeInstance) -> VideoGrid:
    widget = VideoGrid(rows=2, columns=2)
    widget.resize(400, 400)
    widget.show()
    QApplication.processEvents()
    return widget


class TestDeviceTreeAsDragSource:
    @pytest.fixture
    def tree(self, qapp: QApplication, db_connection: sqlite3.Connection) -> DeviceTree:
        nvrs = NvrRepository(db_connection)
        cameras = CameraRepository(db_connection)
        nvr = nvrs.create(
            Nvr(name="NVR", host="192.0.2.10", username="admin", channel_count=1)
        )
        cameras.create(
            Camera(nvr_id=nvr.id, channel_number=1, name="Canal 1")  # type: ignore[arg-type]
        )
        return DeviceTree(nvrs, cameras)

    def test_dragging_a_camera_carries_its_id(self, tree: DeviceTree) -> None:
        camera_item = tree.topLevelItem(0).child(0)
        camera_id = camera_item.data(0, CAMERA_ID_ROLE)

        mime = tree.mimeData([camera_item])

        assert mime.hasFormat(CAMERA_MIME_TYPE)
        assert bytes(mime.data(CAMERA_MIME_TYPE)).decode() == str(camera_id)

    def test_dragging_an_nvr_row_carries_nothing(self, tree: DeviceTree) -> None:
        """Only cameras are draggable; an NVR row must not look like one."""
        mime = tree.mimeData([tree.topLevelItem(0)])

        assert not mime.hasFormat(CAMERA_MIME_TYPE)

    def test_only_the_first_camera_of_a_multi_selection_is_carried(
        self, tree: DeviceTree
    ) -> None:
        extra = QTreeWidgetItem(["Canal 2"])
        extra.setData(0, CAMERA_ID_ROLE, 999)
        tree.topLevelItem(0).addChild(extra)
        first = tree.topLevelItem(0).child(0)

        mime = tree.mimeData([first, extra])

        assert bytes(mime.data(CAMERA_MIME_TYPE)).decode() == str(
            first.data(0, CAMERA_ID_ROLE)
        )


class TestGridAcceptsDrags:
    # The QMimeData must outlive the event: Qt keeps a borrowed pointer, so
    # letting Python collect it segfaults instead of failing a test.
    def test_camera_drags_are_accepted(self, grid: VideoGrid) -> None:
        mime = camera_mime(1)
        event = QDragEnterEvent(
            QPoint(10, 10),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        grid.dragEnterEvent(event)

        assert event.isAccepted()

    def test_tile_drags_are_accepted(self, grid: VideoGrid) -> None:
        mime = position_mime(0)
        event = QDragEnterEvent(
            QPoint(10, 10),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        grid.dragEnterEvent(event)

        assert event.isAccepted()

    def test_unrelated_drags_are_not_accepted(self, grid: VideoGrid) -> None:
        mime = QMimeData()
        mime.setText("/etc/passwd")
        event = QDragEnterEvent(
            QPoint(10, 10),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        grid.dragEnterEvent(event)

        assert not event.isAccepted()


class TestDroppingOnTheGrid:
    def test_dropping_a_camera_reports_the_target_cell(
        self, grid: VideoGrid
    ) -> None:
        seen: list[tuple[int, int]] = []
        grid.cameraDropped.connect(lambda cam, pos: seen.append((cam, pos)))

        drop_at(grid, camera_mime(7), cell_center(grid, 3))

        assert seen == [(7, 3)]

    def test_dropping_a_tile_moves_it(self, grid: VideoGrid) -> None:
        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
        grid.place_tile(0, tile)
        QApplication.processEvents()

        drop_at(grid, position_mime(0), cell_center(grid, 2))

        assert grid.tile_at(2) is tile
        assert grid.tile_at(0) is None
        grid.clear()

    def test_dropping_outside_any_cell_is_ignored(self, grid: VideoGrid) -> None:
        seen: list[tuple[int, int]] = []
        grid.cameraDropped.connect(lambda cam, pos: seen.append((cam, pos)))

        event = drop_at(grid, camera_mime(7), QPoint(10_000, 10_000))

        assert seen == []
        assert not event.isAccepted()

    def test_an_unknown_payload_is_ignored(self, grid: VideoGrid) -> None:
        mime = QMimeData()
        mime.setText("nada a ver")

        event = drop_at(grid, mime, cell_center(grid, 0))

        assert not event.isAccepted()


class TestTileAsDragSource:
    def test_a_placed_tile_knows_its_position(
        self, grid: VideoGrid, qapp: QApplication
    ) -> None:
        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
        grid.place_tile(1, tile)

        assert tile.grid_position == 1
        grid.clear()

    def test_an_unplaced_tile_has_no_position_to_drag(
        self, qapp: QApplication, fake_instance: FakeInstance
    ) -> None:
        tile = VideoTile(title="Canal 1", stream_urls=STREAM_URLS)
        assert tile.grid_position is None
        tile.close_stream()
