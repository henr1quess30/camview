"""CamView entry point: ``python -m camview``."""

from __future__ import annotations

import sys

from camview.app import create_application
from camview.ui.main_window import MainWindow


def main() -> int:
    app = create_application(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
