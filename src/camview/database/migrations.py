"""Schema migrations, stepped via ``PRAGMA user_version``.

Each entry in ``MIGRATIONS`` is applied, in order, exactly once: any
migration whose version is greater than the database's current
``user_version`` runs inside a transaction, after which ``user_version``
is updated to match. Migrations are only ever appended, never edited
in place, so a given database's history stays reproducible.

Migration 1 creates the full ``nvrs`` / ``cameras`` / ``layouts`` /
``layout_items`` / ``settings`` schema.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    description: str
    apply: Callable[[sqlite3.Connection], None]


_SCHEMA_V1 = """
CREATE TABLE nvrs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    rtsp_port INTEGER NOT NULL DEFAULT 554,
    username TEXT NOT NULL,
    channel_count INTEGER NOT NULL,
    default_stream TEXT NOT NULL DEFAULT 'main',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE cameras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nvr_id INTEGER NOT NULL REFERENCES nvrs (id) ON DELETE CASCADE,
    channel_number INTEGER NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (nvr_id, channel_number)
);

CREATE TABLE layouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    rows INTEGER NOT NULL,
    columns INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE layout_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layout_id INTEGER NOT NULL REFERENCES layouts (id) ON DELETE CASCADE,
    camera_id INTEGER NOT NULL REFERENCES cameras (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    stream_type TEXT NOT NULL,
    UNIQUE (layout_id, position)
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _apply_v1_initial_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_V1)


def _apply_v2_device_type(connection: sqlite3.Connection) -> None:
    """Tell an NVR apart from a standalone camera.

    Existing rows keep the default, which is what they were: everything
    registered before this column existed was registered as an NVR.
    """
    connection.execute(
        "ALTER TABLE nvrs ADD COLUMN device_type TEXT NOT NULL DEFAULT 'nvr'"
    )


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="Initial schema: nvrs, cameras, layouts, layout_items, settings",
        apply=_apply_v1_initial_schema,
    ),
    Migration(
        version=2,
        description="Add nvrs.device_type to support standalone cameras",
        apply=_apply_v2_device_type,
    ),
]


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply every pending migration to ``connection``, in version order."""
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]

    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version <= current_version:
            continue
        logger.info(
            "Applying migration %d: %s", migration.version, migration.description
        )
        with connection:
            migration.apply(connection)
            connection.execute(f"PRAGMA user_version = {migration.version}")
