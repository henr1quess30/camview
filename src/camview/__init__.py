"""CamView — lightweight NVR/camera live-viewing client."""

from importlib.metadata import PackageNotFoundError, version as _installed_version

#: Fallback for installations with no package metadata — the Flatpak
#: copies the package in rather than pip-installing it. Kept in step with
#: pyproject.toml by a test, because the update check compares against
#: this: a stale value here would announce an update forever.
_FALLBACK_VERSION = "0.2.0"

try:
    __version__ = _installed_version("camview")
except PackageNotFoundError:  # pragma: no cover - depends on install method
    __version__ = _FALLBACK_VERSION
