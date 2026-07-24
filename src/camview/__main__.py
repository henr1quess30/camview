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

import sys  # noqa: E402

from camview.app import create_application  # noqa: E402
from camview.config import get_db_path  # noqa: E402
from camview.database.connection import initialize_database  # noqa: E402
from camview.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = create_application(sys.argv)

    connection = initialize_database(get_db_path())
    try:
        window = MainWindow(connection=connection)
        window.show()
        return app.exec()
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
