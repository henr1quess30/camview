"""Shared pytest fixtures: QApplication, temp DB connection, fake keyring."""

from __future__ import annotations

import os

# The suite must run without a display (CI, ssh session, tty). Set before
# PySide6 is imported anywhere, since Qt reads this at QApplication
# construction; an explicit value in the environment still wins.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import keyring  # noqa: E402
import keyring.backend  # noqa: E402
import pytest  # noqa: E402
from fakes import FakeInstance  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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
def fake_instance(monkeypatch: pytest.MonkeyPatch) -> FakeInstance:
    """Replace the global vlc.Instance getter with an in-memory fake.

    Patched at every import site, not just the tile's: MainWindow also
    resolves the instance to check whether libVLC is usable at all, and a
    missed site would quietly build a real one.
    """
    instance = FakeInstance()
    for module in ("camview.ui.widgets.video_tile", "camview.ui.main_window"):
        monkeypatch.setattr(f"{module}.get_vlc_instance", lambda: instance)
    return instance


@pytest.fixture(autouse=True)
def no_device_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the channel-status query away from the network.

    A cell that fails twice asks its recorder whether the channel is even
    transmitting. In tests that would be a real HTTP request to a
    documentation address, left running in a thread at exit — which
    aborts the process. Tests that want to exercise the flow monkeypatch
    this again; theirs is applied later and wins.
    """
    monkeypatch.setattr(
        "camview.ui.main_window.channel_online_status",
        lambda *_args, **_kwargs: {},
    )


@pytest.fixture
def fake_keyring() -> Iterator[FakeKeyringBackend]:
    backend = FakeKeyringBackend()
    original = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(original)
