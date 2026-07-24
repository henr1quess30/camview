"""Owns the single global ``vlc.Instance`` — implemented in Phase 3.

Contract: the instance is constructed lazily, on first use, never at
import time, so importing this module never requires a working libvlc
install. Construction failures are turned into the user-facing
"VLC not installed" error path rather than a crash.
"""
