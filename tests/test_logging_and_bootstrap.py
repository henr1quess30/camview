"""Tests for logging setup and application bootstrap.

``create_application`` builds a QApplication, which can only exist once
per process, so the Qt part is replaced by a stand-in — what matters here
is the wiring around it: which log directory is used, and what happens
when that directory cannot be written to.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from camview import app as app_module
from camview.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def restore_camview_logger() -> object:
    """Keep these tests from leaving handlers on the shared logger."""
    logger = logging.getLogger("camview")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    yield None
    logger.handlers.clear()
    logger.handlers.extend(original_handlers)
    logger.setLevel(original_level)


class TestConfigureLogging:
    def test_creates_the_directory_and_writes_a_log_file(
        self, tmp_path: Path
    ) -> None:
        log_dir = tmp_path / "deep" / "logs"

        configure_logging(log_dir)
        logging.getLogger("camview.test").info("olá")
        for handler in logging.getLogger("camview").handlers:
            handler.flush()

        assert log_dir.is_dir()
        assert "olá" in (log_dir / "camview.log").read_text(encoding="utf-8")

    def test_installs_console_and_file_handlers(self, tmp_path: Path) -> None:
        configure_logging(tmp_path)

        handlers = logging.getLogger("camview").handlers
        assert len(handlers) == 2
        assert any(isinstance(h, logging.StreamHandler) for h in handlers)

    def test_reconfiguring_does_not_stack_handlers(self, tmp_path: Path) -> None:
        configure_logging(tmp_path)
        configure_logging(tmp_path)

        assert len(logging.getLogger("camview").handlers) == 2


class FakeQApplication:
    """Stand-in for QApplication: only one real one may exist per process."""

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.application_name = ""
        self.display_name = ""
        self.organization = ""
        self.desktop_file = ""
        self.icon: object = None

    def setApplicationName(self, name: str) -> None:
        self.application_name = name

    def setApplicationDisplayName(self, name: str) -> None:
        self.display_name = name

    def setOrganizationName(self, name: str) -> None:
        self.organization = name

    def setDesktopFileName(self, name: str) -> None:
        self.desktop_file = name

    def setWindowIcon(self, icon: object) -> None:
        self.icon = icon


class TestCreateApplication:
    @pytest.fixture(autouse=True)
    def fake_qt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(app_module, "QApplication", FakeQApplication)

    def test_uses_the_configured_log_directory(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "custom"

        app_module.create_application([], log_dir=log_dir)

        assert (log_dir / "camview.log").exists()

    def test_identifies_itself_to_the_desktop(self, tmp_path: Path) -> None:
        """setDesktopFileName is what gives KDE the right taskbar icon."""
        app = app_module.create_application([], log_dir=tmp_path)

        assert app.application_name == "CamView"
        assert app.desktop_file == "camview"

    def test_unusable_log_directory_falls_back_instead_of_aborting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fallback = tmp_path / "fallback"
        monkeypatch.setenv("XDG_STATE_HOME", str(fallback))
        blocked = tmp_path / "blocked"
        blocked.write_text("this is a file, not a directory")

        app_module.create_application([], log_dir=blocked)

        assert (fallback / "camview" / "logs" / "camview.log").exists()

    def test_excepthook_is_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        original = sys.excepthook
        try:
            app_module.create_application([], log_dir=tmp_path)
            assert sys.excepthook is not original
        finally:
            sys.excepthook = original
