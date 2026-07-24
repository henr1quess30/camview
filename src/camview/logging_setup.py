"""Logging configuration for CamView.

Convention: every module logs via ``logging.getLogger(f"camview.{__name__}")``
or simply ``logging.getLogger(__name__)`` since packages already live under
the ``camview`` namespace. This module configures the root ``camview``
logger once, at application startup.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def configure_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """Configure the ``camview`` logger with console + rotating file output."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("camview")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_dir / "camview.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
