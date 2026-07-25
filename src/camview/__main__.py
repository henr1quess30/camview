"""CamView entry point: ``python -m camview``."""

from __future__ import annotations

import os

# libVLC 3.x only knows how to embed video into an X11 window. Under a
# native Wayland session, a Qt widget's winId() is not an X11 window id,
# so video output silently fails (confirmed empirically: the default
# "wayland" QPA platform makes every libVLC vout module fail to attach).
# Forcing "xcb" runs the whole app through XWayland instead, which fixes
# embedding with no other observable side effects. This must happen
# before PySide6 is imported anywhere, since Qt reads the platform plugin
# name at QApplication construction. setdefault() lets an explicit
# QT_QPA_PLATFORM in the environment still override this.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import logging  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from camview.app import create_application  # noqa: E402
from camview.config import get_db_path  # noqa: E402
from camview.database.connection import initialize_database  # noqa: E402
from camview.database.repositories import SettingsRepository  # noqa: E402
from camview.services.settings import load_settings  # noqa: E402
from camview.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    # The database comes first because it holds the log directory setting,
    # and logging is configured as part of building the application.
    db_path = get_db_path()
    try:
        connection = initialize_database(db_path)
    except (sqlite3.Error, OSError) as exc:
        return _report_startup_failure(db_path, exc)

    try:
        settings = load_settings(SettingsRepository(connection))
        app = create_application(sys.argv, log_dir=settings.log_dir)
        window = MainWindow(connection=connection)
        window.show()
        return app.exec()
    finally:
        connection.close()


def _report_startup_failure(db_path: Path, exc: Exception) -> int:
    """Explain an unusable database instead of dying with a traceback.

    This is the one failure that happens before there is a window to put a
    message in, so it builds a throwaway QApplication just to show it.
    """
    from PySide6.QtWidgets import QApplication, QMessageBox

    logging.getLogger(__name__).critical(
        "Could not open the database at %s: %s", db_path, exc
    )
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.critical(
        None,
        "CamView",
        f"Não foi possível abrir o banco de dados:\n{db_path}\n\n"
        f"Detalhe técnico: {exc}\n\n"
        "Se o arquivo estiver corrompido, renomeie-o e abra o CamView "
        "novamente — um banco novo será criado (os NVRs precisarão ser "
        "cadastrados de novo; as senhas seguem no keyring).",
    )
    del app
    return 1


if __name__ == "__main__":
    sys.exit(main())
