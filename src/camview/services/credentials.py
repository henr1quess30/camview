"""Keyring wrapper for NVR credentials — implemented in Phase 2.

Passwords are never persisted to SQLite; only a keyring lookup key is
stored alongside the NVR row.
"""
