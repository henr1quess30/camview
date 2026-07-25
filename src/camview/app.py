"""Qt application bootstrap: logging, crash handling, QApplication setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import TracebackType

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from camview.config import AppConfig
from camview.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def _install_excepthook() -> None:
    """Log uncaught exceptions instead of letting them crash the app."""

    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = handle_exception


def create_application(
    argv: list[str], log_dir: Path | None = None
) -> QApplication:
    """Build the QApplication, with logging and crash handling wired up.

    ``log_dir`` comes from the user's settings; ``None`` keeps the default
    XDG location. A configured directory that can't be written to falls
    back to the default rather than aborting startup.
    """
    config = AppConfig(log_dir=log_dir) if log_dir is not None else AppConfig()
    try:
        configure_logging(config.log_dir)
    except OSError:
        config = AppConfig()
        configure_logging(config.log_dir)
        logger.warning("Configured log directory unusable; using %s", config.log_dir)
    _install_excepthook()

    app = QApplication(argv)
    app.setApplicationName("CamView")
    app.setApplicationDisplayName("CamView")
    app.setOrganizationName("CamView")
    # Lets the desktop match the window to scripts/camview.desktop, which is
    # what gives KDE's task manager the right icon and name.
    app.setDesktopFileName("camview")
    icon = QIcon.fromTheme("camera-video")
    if not icon.isNull():
        app.setWindowIcon(icon)
    logger.info("CamView starting up (log dir: %s)", config.log_dir)
    return app
