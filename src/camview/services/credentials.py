"""Keyring-backed storage for NVR passwords.

Passwords never touch SQLite; only a (service, username) pair derived
from the NVR's id is looked up in the OS keyring (KWallet/SecretService
on KDE Plasma).
"""

from __future__ import annotations

import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

logger = logging.getLogger(__name__)

_SERVICE_NAME = "camview"


class CredentialsError(Exception):
    """Raised when the OS keyring is unavailable or a keyring operation fails."""


def _keyring_username(nvr_id: int) -> str:
    return f"nvr:{nvr_id}"


def set_nvr_password(nvr_id: int, password: str) -> None:
    """Store (or overwrite) the RTSP password for the given NVR."""
    try:
        keyring.set_password(_SERVICE_NAME, _keyring_username(nvr_id), password)
    except KeyringError as exc:
        logger.error("Failed to store password for NVR %d: %s", nvr_id, exc)
        raise CredentialsError(
            "Não foi possível salvar a senha no keyring do sistema."
        ) from exc


def get_nvr_password(nvr_id: int) -> str | None:
    """Return the stored RTSP password for the given NVR, or ``None`` if unset."""
    try:
        return keyring.get_password(_SERVICE_NAME, _keyring_username(nvr_id))
    except KeyringError as exc:
        logger.error("Failed to read password for NVR %d: %s", nvr_id, exc)
        raise CredentialsError(
            "Não foi possível ler a senha do keyring do sistema."
        ) from exc


def delete_nvr_password(nvr_id: int) -> None:
    """Remove the stored RTSP password for the given NVR, if any."""
    try:
        keyring.delete_password(_SERVICE_NAME, _keyring_username(nvr_id))
    except PasswordDeleteError:
        # Already absent - deleting a password that was never set is a no-op.
        pass
    except KeyringError as exc:
        logger.error("Failed to delete password for NVR %d: %s", nvr_id, exc)
        raise CredentialsError(
            "Não foi possível remover a senha do keyring do sistema."
        ) from exc
