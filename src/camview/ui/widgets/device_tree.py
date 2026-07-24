"""DeviceTree — sidebar tree of registered NVRs and their camera channels."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QWidget

from camview.database.repositories import CameraRepository, NvrRepository

NVR_ID_ROLE = Qt.ItemDataRole.UserRole
CAMERA_ID_ROLE = Qt.ItemDataRole.UserRole + 1

#: Mime type used when dragging a camera from the tree onto a grid cell.
CAMERA_MIME_TYPE = "application/x-camview-camera-id"


class DeviceTree(QTreeWidget):
    """Tree of ``NVR -> Camera`` items, loaded from the database on demand.

    Camera rows are drag sources: dropping one onto a :class:`VideoGrid`
    cell opens that camera there.
    """

    def __init__(
        self,
        nvr_repository: NvrRepository,
        camera_repository: CameraRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._nvr_repository = nvr_repository
        self._camera_repository = camera_repository
        self.setHeaderLabels(["NVRs / Cameras"])
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.refresh()

    def refresh(self) -> None:
        """Reload every NVR and camera from the database."""
        self.clear()
        for nvr in self._nvr_repository.list_all():
            nvr_item = QTreeWidgetItem([nvr.name])
            nvr_item.setData(0, NVR_ID_ROLE, nvr.id)
            for camera in self._camera_repository.list_by_nvr(nvr.id):  # type: ignore[arg-type]
                camera_item = QTreeWidgetItem([camera.name])
                camera_item.setData(0, CAMERA_ID_ROLE, camera.id)
                nvr_item.addChild(camera_item)
            self.addTopLevelItem(nvr_item)
        self.expandAll()

    def mimeData(self, items: list[QTreeWidgetItem]) -> QMimeData:  # type: ignore[override]
        """Carry the dragged camera's id in a private mime type.

        Qt's default item mime data encodes model indexes, which are
        meaningless to a drop target outside this view. A plain camera id
        is all :class:`VideoGrid` needs.
        """
        mime = QMimeData()
        for item in items:
            camera_id = item.data(0, CAMERA_ID_ROLE)
            if camera_id is not None:
                mime.setData(CAMERA_MIME_TYPE, str(camera_id).encode("ascii"))
                break  # only single-camera drags are supported
        return mime
