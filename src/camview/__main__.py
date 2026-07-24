"""CamView entry point: ``python -m camview``."""

from __future__ import annotations

import sys

from camview.app import create_application
from camview.config import get_db_path
from camview.database.connection import initialize_database
from camview.ui.main_window import MainWindow


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
