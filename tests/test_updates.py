"""Tests for the update check.

It only ever *checks*: the app never downloads or replaces itself, so
what matters here is that a real new version is noticed, that noise is
not, and that a failed check is invisible to the user.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication

from camview.database.repositories import SettingsRepository
from camview.models.settings import AppSettings
from camview.services import updates
from camview.services.updates import (
    Release,
    fetch_latest_release,
    find_update,
    is_newer,
    parse_version,
)
from camview.services.settings import save_settings
from camview.ui.main_window import MainWindow


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def serve(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    monkeypatch.setattr(
        updates.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(payload)
    )


def fail_with(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def raise_it(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(updates.urllib.request, "urlopen", raise_it)


class TestVersionComparison:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("1.2.3", (1, 2, 3)), ("v0.2.0", (0, 2, 0)), ("CamView 10.0.1", (10, 0, 1))],
    )
    def test_versions_are_parsed(
        self, text: str, expected: tuple[int, int, int]
    ) -> None:
        assert parse_version(text) == expected

    @pytest.mark.parametrize("text", ["", "nightly", "v", "latest"])
    def test_nonsense_is_not_a_version(self, text: str) -> None:
        assert parse_version(text) is None

    @pytest.mark.parametrize(
        ("candidate", "current", "newer"),
        [
            ("0.3.0", "0.2.0", True),
            ("1.0.0", "0.9.9", True),
            ("0.2.1", "0.2.0", True),
            ("0.2.0", "0.2.0", False),
            ("0.1.0", "0.2.0", False),
        ],
    )
    def test_comparison(self, candidate: str, current: str, newer: bool) -> None:
        assert is_newer(candidate, current) is newer

    def test_an_unparseable_tag_never_nags(self) -> None:
        """A malformed release tag must not mean 'update forever'."""
        assert is_newer("nightly-build", "0.2.0") is False


class TestFetchingTheRelease:
    def test_a_published_release_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serve(
            monkeypatch,
            {"tag_name": "v0.3.0", "html_url": "https://example.invalid/releases/1"},
        )

        release = fetch_latest_release()

        assert release == Release(
            version="0.3.0", url="https://example.invalid/releases/1"
        )

    def test_no_network_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fail_with(monkeypatch, urllib.error.URLError("no route to host"))

        assert fetch_latest_release() is None

    def test_a_private_repository_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """404 is what a private repo looks like; the user need not hear it."""
        fail_with(
            monkeypatch,
            urllib.error.HTTPError("url", 404, "Not Found", {}, None),  # type: ignore[arg-type]
        )

        assert fetch_latest_release() is None

    def test_garbage_json_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Broken:
            def read(self) -> bytes:
                return b"<html>rate limited</html>"

            def __enter__(self) -> "Broken":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        monkeypatch.setattr(
            updates.urllib.request, "urlopen", lambda *_a, **_k: Broken()
        )

        assert fetch_latest_release() is None

    def test_a_release_without_a_tag_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serve(monkeypatch, {"html_url": "https://example.invalid"})

        assert fetch_latest_release() is None


class TestFindUpdate:
    def test_a_newer_release_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serve(monkeypatch, {"tag_name": "v9.9.9", "html_url": "https://x.invalid"})

        found = find_update("0.2.0")

        assert found is not None
        assert found.version == "9.9.9"

    def test_the_same_version_is_not_an_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serve(monkeypatch, {"tag_name": "v0.2.0", "html_url": "https://x.invalid"})

        assert find_update("0.2.0") is None

    def test_running_ahead_of_the_release_is_not_an_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Developing on a version not published yet must not nag."""
        serve(monkeypatch, {"tag_name": "v0.2.0", "html_url": "https://x.invalid"})

        assert find_update("0.3.0") is None


class TestTheNoticeInTheWindow:
    @pytest.fixture
    def window(
        self,
        qapp: QApplication,
        db_connection: sqlite3.Connection,
        fake_keyring: object,
        fake_instance: FakeInstance,
    ) -> MainWindow:
        return MainWindow(connection=db_connection)

    # isHidden(), not isVisible(): the window is never shown in tests, so
    # isVisible() would be False even for a label that was made visible.
    def test_nothing_is_shown_when_up_to_date(self, window: MainWindow) -> None:
        assert window.update_label.isHidden() is True

    def test_an_update_shows_a_link(self, window: MainWindow) -> None:
        window._on_update_check_finished(
            Release(version="9.9.9", url="https://example.invalid/releases/9")
        )

        assert window.update_label.isHidden() is False
        assert "9.9.9" in window.update_label.text()
        assert "https://example.invalid/releases/9" in window.update_label.text()

    def test_a_failed_check_shows_nothing(self, window: MainWindow) -> None:
        window._on_update_check_finished(None)

        assert window.update_label.isHidden() is True

    def test_the_check_can_be_turned_off(
        self, window: MainWindow, db_connection: sqlite3.Connection
    ) -> None:
        save_settings(
            SettingsRepository(db_connection), AppSettings(check_for_updates=False)
        )
        reopened = MainWindow(connection=db_connection)

        reopened._check_for_updates()

        assert reopened._update_workers == set()

    def test_the_check_runs_off_the_gui_thread(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreachable GitHub must not hold the window at startup."""
        from camview.ui import workers

        monkeypatch.setattr(
            workers,
            "find_update",
            lambda _version: Release(version="9.9.9", url="https://x.invalid"),
        )
        received: list[object] = []
        worker = workers.UpdateCheckWorker("0.2.0")
        worker.finished_with.connect(received.append)

        worker.start()
        worker.wait(5000)
        QApplication.processEvents()

        assert received and isinstance(received[0], Release)
