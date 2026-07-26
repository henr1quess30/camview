"""Checking whether a newer CamView has been released.

Deliberately only *checks*. Downloading and replacing the running app is
left to whatever installed it — Flatpak, the AUR, a package manager —
because those already do it well and are what the user trusts.

The request is a single unauthenticated GET to GitHub's public API. It
carries no information about the user beyond the fact that some CamView
somewhere looked for a release, and it can be turned off in the settings.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Where releases are published.
GITHUB_OWNER = "henr1quess30"
GITHUB_REPO = "camview"
RELEASES_API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
RELEASES_PAGE = "https://github.com/{owner}/{repo}/releases/latest"

_TIMEOUT_SECONDS = 5.0
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class Release:
    """A published version, as the update check found it."""

    version: str
    url: str


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``v1.2.3`` / ``1.2.3`` -> ``(1, 2, 3)``; anything else -> ``None``."""
    match = _VERSION_RE.search(text or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_newer(candidate: str, current: str) -> bool:
    """Is ``candidate`` a later version than ``current``?

    Unparseable versions are never "newer": a malformed tag must not
    nag the user forever.
    """
    new = parse_version(candidate)
    running = parse_version(current)
    if new is None or running is None:
        return False
    return new > running


def fetch_latest_release(
    owner: str = GITHUB_OWNER, repo: str = GITHUB_REPO
) -> Release | None:
    """The most recent published release, or ``None`` if it can't be read.

    ``None`` covers every uninteresting case — no releases yet, a private
    repository, no network, GitHub rate-limiting this address — none of
    which the user needs to hear about.
    """
    url = RELEASES_API.format(owner=owner, repo=repo)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CamView",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("Update check did not complete: %s", exc)
        return None

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return None
    return Release(
        version=tag.lstrip("vV"),
        url=str(payload.get("html_url") or RELEASES_PAGE.format(owner=owner, repo=repo)),
    )


def find_update(current_version: str) -> Release | None:
    """The published release worth telling the user about, if any."""
    release = fetch_latest_release()
    if release is None or not is_newer(release.version, current_version):
        return None
    logger.info("Update available: %s (running %s)", release.version, current_version)
    return release
