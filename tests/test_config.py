"""Tests for XDG path resolution.

Every test redirects the XDG variables at a temporary directory: a test
that touched the real ``~/.local/share/camview`` could scribble on the
user's actual database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from camview.config import (
    AppConfig,
    get_data_dir,
    get_db_path,
    get_default_log_dir,
)


class TestDataDir:
    def test_follows_xdg_data_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        assert get_data_dir() == tmp_path / "data" / "camview"

    def test_falls_back_to_local_share(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

        assert get_data_dir() == tmp_path / ".local" / "share" / "camview"

    def test_directory_is_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        assert get_data_dir().is_dir()

    def test_database_lives_in_the_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        assert get_db_path() == tmp_path / "data" / "camview" / "camview.db"


class TestLogDir:
    def test_follows_xdg_state_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        assert get_default_log_dir() == tmp_path / "state" / "camview" / "logs"

    def test_falls_back_to_local_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

        assert get_default_log_dir() == tmp_path / ".local" / "state" / "camview" / "logs"

    def test_directory_is_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        assert get_default_log_dir().is_dir()


class TestAppConfig:
    def test_defaults_to_the_xdg_log_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        assert AppConfig().log_dir == tmp_path / "state" / "camview" / "logs"

    def test_each_instance_resolves_its_own_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mutable default would freeze the path at import time."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "first"))
        first = AppConfig()
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "second"))
        second = AppConfig()

        assert first.log_dir != second.log_dir

    def test_explicit_log_dir_wins(self, tmp_path: Path) -> None:
        assert AppConfig(log_dir=tmp_path).log_dir == tmp_path
