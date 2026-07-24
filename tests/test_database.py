"""Tests for the SQLite persistence layer: migrations and repositories."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from camview.database.connection import initialize_database
from camview.database.migrations import apply_migrations
from camview.database.repositories import (
    CameraRepository,
    LayoutRepository,
    NvrRepository,
    SettingsRepository,
)
from camview.models.camera import Camera, StreamType
from camview.models.layout import Layout, LayoutItem
from camview.models.nvr import Nvr


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(tmp_path / "camview.db")
    yield conn
    conn.close()


def make_nvr(**overrides: object) -> Nvr:
    defaults: dict[str, object] = {
        "name": "Garagem",
        "host": "192.0.2.10",
        "username": "admin",
        "channel_count": 4,
    }
    defaults.update(overrides)
    return Nvr(**defaults)  # type: ignore[arg-type]


class TestMigrations:
    def test_creates_expected_tables(self, connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"nvrs", "cameras", "layouts", "layout_items", "settings"} <= tables

    def test_sets_user_version(self, connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == 1

    def test_is_idempotent(self, connection: sqlite3.Connection) -> None:
        # Applying migrations again on an up-to-date DB must not raise
        # (e.g. re-running CREATE TABLE) and must not touch existing data.
        NvrRepository(connection).create(make_nvr())
        apply_migrations(connection)
        assert len(NvrRepository(connection).list_all()) == 1


class TestNvrRepository:
    def test_create_assigns_id_and_timestamps(
        self, connection: sqlite3.Connection
    ) -> None:
        repo = NvrRepository(connection)
        created = repo.create(make_nvr())
        assert created.id is not None
        assert created.created_at is not None
        assert created.default_stream == StreamType.MAIN

    def test_get_roundtrip(self, connection: sqlite3.Connection) -> None:
        repo = NvrRepository(connection)
        created = repo.create(make_nvr(name="Entrada", rtsp_port=8554))
        fetched = repo.get(created.id)  # type: ignore[arg-type]
        assert fetched is not None
        assert fetched.name == "Entrada"
        assert fetched.rtsp_port == 8554

    def test_get_missing_returns_none(self, connection: sqlite3.Connection) -> None:
        assert NvrRepository(connection).get(999) is None

    def test_list_all_orders_by_name(self, connection: sqlite3.Connection) -> None:
        repo = NvrRepository(connection)
        repo.create(make_nvr(name="Zona Sul"))
        repo.create(make_nvr(name="Zona Norte"))
        names = [nvr.name for nvr in repo.list_all()]
        assert names == ["Zona Norte", "Zona Sul"]

    def test_update_persists_changes(self, connection: sqlite3.Connection) -> None:
        repo = NvrRepository(connection)
        created = repo.create(make_nvr())
        created.name = "Garagem (renomeado)"
        created.default_stream = StreamType.SUB
        repo.update(created)
        fetched = repo.get(created.id)  # type: ignore[arg-type]
        assert fetched is not None
        assert fetched.name == "Garagem (renomeado)"
        assert fetched.default_stream == StreamType.SUB

    def test_update_without_id_raises(self, connection: sqlite3.Connection) -> None:
        with pytest.raises(ValueError):
            NvrRepository(connection).update(make_nvr())

    def test_delete_removes_row(self, connection: sqlite3.Connection) -> None:
        repo = NvrRepository(connection)
        created = repo.create(make_nvr())
        repo.delete(created.id)  # type: ignore[arg-type]
        assert repo.get(created.id) is None  # type: ignore[arg-type]


class TestCameraRepository:
    def test_create_and_list_by_nvr(self, connection: sqlite3.Connection) -> None:
        nvr = NvrRepository(connection).create(make_nvr())
        cam_repo = CameraRepository(connection)
        cam_repo.create(Camera(nvr_id=nvr.id, channel_number=1, name="Canal 1"))  # type: ignore[arg-type]
        cam_repo.create(Camera(nvr_id=nvr.id, channel_number=2, name="Canal 2"))  # type: ignore[arg-type]

        cameras = cam_repo.list_by_nvr(nvr.id)  # type: ignore[arg-type]
        assert [c.channel_number for c in cameras] == [1, 2]

    def test_update_camera(self, connection: sqlite3.Connection) -> None:
        nvr = NvrRepository(connection).create(make_nvr())
        cam_repo = CameraRepository(connection)
        camera = cam_repo.create(
            Camera(nvr_id=nvr.id, channel_number=1, name="Canal 1")  # type: ignore[arg-type]
        )
        camera.name = "Portão"
        camera.enabled = False
        cam_repo.update(camera)
        fetched = cam_repo.get(camera.id)  # type: ignore[arg-type]
        assert fetched is not None
        assert fetched.name == "Portão"
        assert fetched.enabled is False

    def test_delete_nvr_cascades_to_cameras(
        self, connection: sqlite3.Connection
    ) -> None:
        nvr_repo = NvrRepository(connection)
        cam_repo = CameraRepository(connection)
        nvr = nvr_repo.create(make_nvr())
        camera = cam_repo.create(
            Camera(nvr_id=nvr.id, channel_number=1, name="Canal 1")  # type: ignore[arg-type]
        )

        nvr_repo.delete(nvr.id)  # type: ignore[arg-type]

        assert cam_repo.get(camera.id) is None  # type: ignore[arg-type]

    def test_duplicate_channel_number_rejected(
        self, connection: sqlite3.Connection
    ) -> None:
        nvr = NvrRepository(connection).create(make_nvr())
        cam_repo = CameraRepository(connection)
        cam_repo.create(Camera(nvr_id=nvr.id, channel_number=1, name="Canal 1"))  # type: ignore[arg-type]
        with pytest.raises(sqlite3.IntegrityError):
            cam_repo.create(
                Camera(nvr_id=nvr.id, channel_number=1, name="Duplicado")  # type: ignore[arg-type]
            )


class TestLayoutRepository:
    def test_create_get_rename_delete(self, connection: sqlite3.Connection) -> None:
        repo = LayoutRepository(connection)
        created = repo.create(Layout(name="Padrão", rows=2, columns=2))
        assert repo.get_by_name("Padrão") is not None

        repo.rename(created.id, "Padrão renomeado")  # type: ignore[arg-type]
        assert repo.get(created.id).name == "Padrão renomeado"  # type: ignore[union-attr, arg-type]

        repo.delete(created.id)  # type: ignore[arg-type]
        assert repo.get(created.id) is None  # type: ignore[arg-type]

    def test_set_items_replaces_existing(self, connection: sqlite3.Connection) -> None:
        nvr = NvrRepository(connection).create(make_nvr())
        cam_repo = CameraRepository(connection)
        cam1 = cam_repo.create(
            Camera(nvr_id=nvr.id, channel_number=1, name="Canal 1")  # type: ignore[arg-type]
        )
        cam2 = cam_repo.create(
            Camera(nvr_id=nvr.id, channel_number=2, name="Canal 2")  # type: ignore[arg-type]
        )

        layout_repo = LayoutRepository(connection)
        layout = layout_repo.create(Layout(name="Mosaico", rows=1, columns=2))

        layout_repo.set_items(
            layout.id,  # type: ignore[arg-type]
            [
                LayoutItem(
                    layout_id=layout.id,  # type: ignore[arg-type]
                    camera_id=cam1.id,  # type: ignore[arg-type]
                    position=0,
                    stream_type=StreamType.MAIN,
                ),
                LayoutItem(
                    layout_id=layout.id,  # type: ignore[arg-type]
                    camera_id=cam2.id,  # type: ignore[arg-type]
                    position=1,
                    stream_type=StreamType.SUB,
                ),
            ],
        )
        items = layout_repo.get_items(layout.id)  # type: ignore[arg-type]
        assert [item.camera_id for item in items] == [cam1.id, cam2.id]

        # Overwrite: only cam2 remains, at position 0.
        layout_repo.set_items(
            layout.id,  # type: ignore[arg-type]
            [
                LayoutItem(
                    layout_id=layout.id,  # type: ignore[arg-type]
                    camera_id=cam2.id,  # type: ignore[arg-type]
                    position=0,
                    stream_type=StreamType.MAIN,
                ),
            ],
        )
        items = layout_repo.get_items(layout.id)  # type: ignore[arg-type]
        assert len(items) == 1
        assert items[0].camera_id == cam2.id

    def test_delete_layout_cascades_to_items(
        self, connection: sqlite3.Connection
    ) -> None:
        nvr = NvrRepository(connection).create(make_nvr())
        camera = CameraRepository(connection).create(
            Camera(nvr_id=nvr.id, channel_number=1, name="Canal 1")  # type: ignore[arg-type]
        )
        layout_repo = LayoutRepository(connection)
        layout = layout_repo.create(Layout(name="Mosaico", rows=1, columns=1))
        layout_repo.set_items(
            layout.id,  # type: ignore[arg-type]
            [
                LayoutItem(
                    layout_id=layout.id,  # type: ignore[arg-type]
                    camera_id=camera.id,  # type: ignore[arg-type]
                    position=0,
                    stream_type=StreamType.MAIN,
                )
            ],
        )
        layout_repo.delete(layout.id)  # type: ignore[arg-type]
        remaining = connection.execute("SELECT * FROM layout_items").fetchall()
        assert remaining == []


class TestSettingsRepository:
    def test_get_missing_returns_default(self, connection: sqlite3.Connection) -> None:
        repo = SettingsRepository(connection)
        assert repo.get("missing_key") is None
        assert repo.get("missing_key", "fallback") == "fallback"

    def test_set_then_get(self, connection: sqlite3.Connection) -> None:
        repo = SettingsRepository(connection)
        repo.set("network_caching_ms", "300")
        assert repo.get("network_caching_ms") == "300"

    def test_set_overwrites_existing_value(
        self, connection: sqlite3.Connection
    ) -> None:
        repo = SettingsRepository(connection)
        repo.set("rtsp_transport", "tcp")
        repo.set("rtsp_transport", "udp")
        assert repo.get("rtsp_transport") == "udp"

    def test_get_all(self, connection: sqlite3.Connection) -> None:
        repo = SettingsRepository(connection)
        repo.set("a", "1")
        repo.set("b", "2")
        assert repo.get_all() == {"a": "1", "b": "2"}

    def test_delete(self, connection: sqlite3.Connection) -> None:
        repo = SettingsRepository(connection)
        repo.set("temp", "value")
        repo.delete("temp")
        assert repo.get("temp") is None
