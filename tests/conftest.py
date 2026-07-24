"""Shared pytest fixtures: QApplication, temp DB connection, fake keyring."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import keyring
import keyring.backend
import pytest
from PySide6.QtWidgets import QApplication

from camview.database.connection import initialize_database


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def db_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = initialize_database(tmp_path / "camview.db")
    yield conn
    conn.close()


class FakeKeyringBackend(keyring.backend.KeyringBackend):
    """In-memory keyring backend, so tests never touch the real OS keyring."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self._store[(service, username)]
        except KeyError as exc:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError("not found") from exc


@pytest.fixture
def fake_keyring() -> Iterator[FakeKeyringBackend]:
    backend = FakeKeyringBackend()
    original = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)
