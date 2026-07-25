"""Repository classes for CRUD access to CamView's SQLite tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from camview.models.camera import Camera, StreamType
from camview.models.layout import Layout, LayoutItem
from camview.models.nvr import DeviceType, Nvr


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


class NvrRepository:
    """CRUD access to the ``nvrs`` table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, nvr: Nvr) -> Nvr:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO nvrs (
                    name, host, rtsp_port, username, channel_count,
                    default_stream, device_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nvr.name,
                    nvr.host,
                    nvr.rtsp_port,
                    nvr.username,
                    nvr.channel_count,
                    nvr.default_stream.value,
                    nvr.device_type.value,
                ),
            )
        created = self.get(cursor.lastrowid)
        assert created is not None
        return created

    def get(self, nvr_id: int) -> Nvr | None:
        row = self._connection.execute(
            "SELECT * FROM nvrs WHERE id = ?", (nvr_id,)
        ).fetchone()
        return self._row_to_nvr(row) if row is not None else None

    def list_all(self) -> list[Nvr]:
        rows = self._connection.execute("SELECT * FROM nvrs ORDER BY name").fetchall()
        return [self._row_to_nvr(row) for row in rows]

    def update(self, nvr: Nvr) -> None:
        if nvr.id is None:
            raise ValueError("Cannot update an Nvr without an id")
        with self._connection:
            self._connection.execute(
                """
                UPDATE nvrs
                SET name = ?, host = ?, rtsp_port = ?, username = ?, channel_count = ?,
                    default_stream = ?, device_type = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    nvr.name,
                    nvr.host,
                    nvr.rtsp_port,
                    nvr.username,
                    nvr.channel_count,
                    nvr.default_stream.value,
                    nvr.device_type.value,
                    nvr.id,
                ),
            )

    def delete(self, nvr_id: int) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM nvrs WHERE id = ?", (nvr_id,))

    @staticmethod
    def _row_to_nvr(row: sqlite3.Row) -> Nvr:
        return Nvr(
            id=row["id"],
            name=row["name"],
            host=row["host"],
            rtsp_port=row["rtsp_port"],
            username=row["username"],
            channel_count=row["channel_count"],
            default_stream=StreamType(row["default_stream"]),
            device_type=DeviceType(row["device_type"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )


class CameraRepository:
    """CRUD access to the ``cameras`` table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, camera: Camera) -> Camera:
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO cameras (nvr_id, channel_number, name, enabled)
                VALUES (?, ?, ?, ?)
                """,
                (camera.nvr_id, camera.channel_number, camera.name, int(camera.enabled)),
            )
        created = self.get(cursor.lastrowid)
        assert created is not None
        return created

    def get(self, camera_id: int) -> Camera | None:
        row = self._connection.execute(
            "SELECT * FROM cameras WHERE id = ?", (camera_id,)
        ).fetchone()
        return self._row_to_camera(row) if row is not None else None

    def list_by_nvr(self, nvr_id: int) -> list[Camera]:
        rows = self._connection.execute(
            "SELECT * FROM cameras WHERE nvr_id = ? ORDER BY channel_number", (nvr_id,)
        ).fetchall()
        return [self._row_to_camera(row) for row in rows]

    def update(self, camera: Camera) -> None:
        if camera.id is None:
            raise ValueError("Cannot update a Camera without an id")
        with self._connection:
            self._connection.execute(
                """
                UPDATE cameras
                SET nvr_id = ?, channel_number = ?, name = ?, enabled = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    camera.nvr_id,
                    camera.channel_number,
                    camera.name,
                    int(camera.enabled),
                    camera.id,
                ),
            )

    def delete(self, camera_id: int) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))

    @staticmethod
    def _row_to_camera(row: sqlite3.Row) -> Camera:
        return Camera(
            id=row["id"],
            nvr_id=row["nvr_id"],
            channel_number=row["channel_number"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )


class LayoutRepository:
    """CRUD access to the ``layouts`` and ``layout_items`` tables."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self, layout: Layout) -> Layout:
        with self._connection:
            cursor = self._connection.execute(
                "INSERT INTO layouts (name, rows, columns) VALUES (?, ?, ?)",
                (layout.name, layout.rows, layout.columns),
            )
        created = self.get(cursor.lastrowid)
        assert created is not None
        return created

    def get(self, layout_id: int) -> Layout | None:
        row = self._connection.execute(
            "SELECT * FROM layouts WHERE id = ?", (layout_id,)
        ).fetchone()
        return self._row_to_layout(row) if row is not None else None

    def get_by_name(self, name: str) -> Layout | None:
        row = self._connection.execute(
            "SELECT * FROM layouts WHERE name = ?", (name,)
        ).fetchone()
        return self._row_to_layout(row) if row is not None else None

    def list_all(self) -> list[Layout]:
        rows = self._connection.execute("SELECT * FROM layouts ORDER BY name").fetchall()
        return [self._row_to_layout(row) for row in rows]

    def rename(self, layout_id: int, new_name: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE layouts SET name = ?, updated_at = datetime('now') WHERE id = ?",
                (new_name, layout_id),
            )

    def update_shape(self, layout_id: int, rows: int, columns: int) -> None:
        """Change a saved layout's grid shape (used when overwriting it)."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE layouts
                SET rows = ?, columns = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (rows, columns, layout_id),
            )

    def delete(self, layout_id: int) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM layouts WHERE id = ?", (layout_id,))

    def get_items(self, layout_id: int) -> list[LayoutItem]:
        rows = self._connection.execute(
            "SELECT * FROM layout_items WHERE layout_id = ? ORDER BY position",
            (layout_id,),
        ).fetchall()
        return [self._row_to_layout_item(row) for row in rows]

    def set_items(self, layout_id: int, items: list[LayoutItem]) -> None:
        """Replace all ``layout_items`` for ``layout_id`` with ``items``.

        Used both for the initial save and for overwriting an existing
        layout — always a full replace, never a partial merge, so callers
        don't need to diff old vs. new positions themselves.
        """
        with self._connection:
            self._connection.execute(
                "DELETE FROM layout_items WHERE layout_id = ?", (layout_id,)
            )
            self._connection.executemany(
                """
                INSERT INTO layout_items (layout_id, camera_id, position, stream_type)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (layout_id, item.camera_id, item.position, item.stream_type.value)
                    for item in items
                ],
            )
            self._connection.execute(
                "UPDATE layouts SET updated_at = datetime('now') WHERE id = ?",
                (layout_id,),
            )

    @staticmethod
    def _row_to_layout(row: sqlite3.Row) -> Layout:
        return Layout(
            id=row["id"],
            name=row["name"],
            rows=row["rows"],
            columns=row["columns"],
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    @staticmethod
    def _row_to_layout_item(row: sqlite3.Row) -> LayoutItem:
        return LayoutItem(
            id=row["id"],
            layout_id=row["layout_id"],
            camera_id=row["camera_id"],
            position=row["position"],
            stream_type=StreamType(row["stream_type"]),
        )


class SettingsRepository:
    """Key/value access to the ``settings`` table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row is not None else default

    def set(self, key: str, value: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_all(self) -> dict[str, str]:
        rows = self._connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def delete(self, key: str) -> None:
        with self._connection:
            self._connection.execute("DELETE FROM settings WHERE key = ?", (key,))
