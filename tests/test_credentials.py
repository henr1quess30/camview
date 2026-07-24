"""Tests for the keyring-backed NVR credential storage.

Uses an in-memory fake keyring backend (see ``conftest.fake_keyring``) so
these tests never touch the real OS keyring (KWallet/SecretService) and
never involve real passwords.
"""

from __future__ import annotations

from collections.abc import Iterator

import keyring
import keyring.backend
import pytest
from keyring.errors import KeyringError

from camview.services.credentials import (
    CredentialsError,
    delete_nvr_password,
    get_nvr_password,
    set_nvr_password,
)


class _BrokenKeyringBackend(keyring.backend.KeyringBackend):
    """Keyring backend that always fails, simulating an unavailable keyring."""

    priority = 1  # type: ignore[assignment]

    def get_password(self, service: str, username: str) -> str | None:
        raise KeyringError("keyring service unavailable")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeyringError("keyring service unavailable")

    def delete_password(self, service: str, username: str) -> None:
        raise KeyringError("keyring service unavailable")


@pytest.fixture
def broken_keyring() -> Iterator[None]:
    original = keyring.get_keyring()
    keyring.set_keyring(_BrokenKeyringBackend())
    try:
        yield
    finally:
        keyring.set_keyring(original)


class TestNvrPasswordStorage:
    def test_set_then_get_roundtrip(self, fake_keyring: keyring.backend.KeyringBackend) -> None:
        set_nvr_password(1, "test-password-123")
        assert get_nvr_password(1) == "test-password-123"

    def test_get_missing_returns_none(self, fake_keyring: keyring.backend.KeyringBackend) -> None:
        assert get_nvr_password(999) is None

    def test_different_nvrs_are_isolated(self, fake_keyring: keyring.backend.KeyringBackend) -> None:
        set_nvr_password(1, "password-one")
        set_nvr_password(2, "password-two")
        assert get_nvr_password(1) == "password-one"
        assert get_nvr_password(2) == "password-two"

    def test_set_overwrites_existing_password(
        self, fake_keyring: keyring.backend.KeyringBackend
    ) -> None:
        set_nvr_password(1, "old-password")
        set_nvr_password(1, "new-password")
        assert get_nvr_password(1) == "new-password"

    def test_delete_removes_password(self, fake_keyring: keyring.backend.KeyringBackend) -> None:
        set_nvr_password(1, "test-password")
        delete_nvr_password(1)
        assert get_nvr_password(1) is None

    def test_delete_missing_password_is_a_no_op(
        self, fake_keyring: keyring.backend.KeyringBackend
    ) -> None:
        delete_nvr_password(999)  # must not raise


class TestKeyringUnavailable:
    def test_set_password_raises_credentials_error(
        self, broken_keyring: None
    ) -> None:
        with pytest.raises(CredentialsError):
            set_nvr_password(1, "test-password")

    def test_get_password_raises_credentials_error(
        self, broken_keyring: None
    ) -> None:
        with pytest.raises(CredentialsError):
            get_nvr_password(1)

    def test_delete_password_raises_credentials_error(
        self, broken_keyring: None
    ) -> None:
        with pytest.raises(CredentialsError):
            delete_nvr_password(1)
