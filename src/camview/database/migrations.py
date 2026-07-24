"""Schema migrations, stepped via ``PRAGMA user_version``.

Each entry in ``MIGRATIONS`` is applied, in order, exactly once: any
migration whose version is greater than the database's current
``user_version`` runs inside a transaction, after which ``user_version``
is updated to match. Migrations are only ever appended, never edited
in place, so a given database's history stays reproducible.

Phase 1 appends migration 1 with the full ``nvrs`` / ``cameras`` /
``layouts`` / ``layout_items`` / ``settings`` schema.
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


MIGRATIONS: list[Migration] = []


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
