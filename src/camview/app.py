"""Qt application bootstrap: logging, crash handling, QApplication setup."""

from __future__ import annotations

import logging
import sys
from types import TracebackType

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


def create_application(argv: list[str]) -> QApplication:
    """Build the QApplication, with logging and crash handling wired up."""
    config = AppConfig()
    configure_logging(config.log_dir)
    _install_excepthook()

    app = QApplication(argv)
    app.setApplicationName("CamView")
    app.setOrganizationName("CamView")
    logger.info("CamView starting up (log dir: %s)", config.log_dir)
    return app
