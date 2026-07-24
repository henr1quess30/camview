"""SQLite connection helper.

Schema creation/migration lives in :mod:`camview.database.migrations`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from camview.database.migrations import apply_migrations


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enabled and row access by name."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(db_path: Path) -> sqlite3.Connection:
    """Open a connection to ``db_path`` and bring its schema up to date."""
    connection = get_connection(db_path)
    apply_migrations(connection)
    return connection
