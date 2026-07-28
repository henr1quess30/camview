"""CamView — lightweight NVR/camera live-viewing client."""

from importlib.metadata import PackageNotFoundError, version as _installed_version

#: The version declared in the source tree. Also the fallback for
#: installations with no package metadata — the Flatpak copies the
#: package in rather than pip-installing it. Kept in step with
#: pyproject.toml by a test, because the update check compares against
#: this: a stale value here would announce an update forever.
_FALLBACK_VERSION = "0.4.0"


def _as_numbers(text: str) -> tuple[int, ...]:
    """``"0.3.0"`` -> ``(0, 3, 0)``; unparseable parts sort lowest."""
    parts: list[int] = []
    for part in text.split("."):
        digits = "".join(c for c in part if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _running_version() -> str:
    """The version actually running, from whichever source knows better.

    An editable install records its version at ``pip install`` time and
    never revisits it, so a dev tree that has since been released reports
    something older than the code it is running — and the update check
    then announces a release the user already has. The source tree is
    never behind itself, so the later of the two is the honest answer.
    """
    try:
        installed = _installed_version("camview")
    except PackageNotFoundError:  # pragma: no cover - depends on install method
        return _FALLBACK_VERSION
    if _as_numbers(installed) >= _as_numbers(_FALLBACK_VERSION):
        return installed
    return _FALLBACK_VERSION


__version__ = _running_version()
