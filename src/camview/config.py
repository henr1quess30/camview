"""Filesystem locations for CamView's data and logs (XDG base dirs)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def get_data_dir() -> Path:
    """Directory holding CamView's SQLite database, created if missing."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    data_dir = root / "camview"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_db_path() -> Path:
    """Path to the CamView SQLite database file."""
    return get_data_dir() / "camview.db"


def get_default_log_dir() -> Path:
    """Default directory for CamView log files, created if missing."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    log_dir = root / "camview" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Runtime configuration for a CamView session.

    Only the log directory is wired up so far; playback/reconnect/UI
    settings are added in Phase 7 once the settings table exists.
    """

    log_dir: Path = field(default_factory=get_default_log_dir)
