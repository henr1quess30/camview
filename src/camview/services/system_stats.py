"""How much of this machine CamView is using, read straight from ``/proc``.

Deliberately no third-party dependency: the numbers wanted here are two
files away on Linux, which is the only platform this app targets. Every
read is defensive — a status panel that cannot be drawn must never take
the window down with it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

logger = logging.getLogger(__name__)

_CLOCK_TICKS_PER_SECOND = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """A reading of this process's footprint.

    ``cpu_percent`` is share of the whole machine (all cores), which is
    what a system monitor shows and therefore what a user compares
    against.
    """

    cpu_percent: float
    memory_mb: float
    memory_percent: float


def _read_first_value(path: Path, key: str) -> int | None:
    """Value of a ``Key:   1234 kB`` line, in kB."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(key):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        logger.debug("Could not read %s from %s", key, path, exc_info=True)
    return None


def _read_cpu_seconds(stat_path: Path) -> float | None:
    """CPU time this process has used, in seconds.

    ``/proc/<pid>/stat`` is one line whose second field is the executable
    name in parentheses — and that name may itself contain spaces, so the
    fields after it are located from the closing parenthesis, never by
    splitting the whole line.
    """
    try:
        raw = stat_path.read_text(encoding="utf-8")
        fields = raw[raw.rindex(")") + 2 :].split()
        utime, stime = int(fields[11]), int(fields[12])
    except (OSError, ValueError, IndexError):
        logger.debug("Could not read CPU time from %s", stat_path, exc_info=True)
        return None
    return (utime + stime) / _CLOCK_TICKS_PER_SECOND


class SystemMonitor:
    """Samples this process's CPU and memory use.

    CPU is a rate, so the first sample only establishes a baseline and
    reports 0%; every later one covers the interval since the previous.
    """

    def __init__(
        self,
        proc_root: Path = Path("/proc"),
        cpu_count: int | None = None,
    ) -> None:
        self._stat_path = proc_root / "self" / "stat"
        self._status_path = proc_root / "self" / "status"
        self._meminfo_path = proc_root / "meminfo"
        self._cpu_count = cpu_count or os.cpu_count() or 1
        self._previous: tuple[float, float] | None = None

    def sample(self, now: float | None = None) -> SystemSnapshot | None:
        """Read the current footprint, or ``None`` if ``/proc`` won't say."""
        now = monotonic() if now is None else now
        cpu_seconds = _read_cpu_seconds(self._stat_path)
        rss_kb = _read_first_value(self._status_path, "VmRSS:")
        total_kb = _read_first_value(self._meminfo_path, "MemTotal:")
        if cpu_seconds is None or rss_kb is None:
            return None

        cpu_percent = 0.0
        if self._previous is not None:
            previous_seconds, previous_time = self._previous
            elapsed = now - previous_time
            if elapsed > 0:
                used = (cpu_seconds - previous_seconds) / elapsed
                cpu_percent = max(0.0, min(100.0, used / self._cpu_count * 100))
        self._previous = (cpu_seconds, now)

        memory_mb = rss_kb / 1024
        memory_percent = (rss_kb / total_kb * 100) if total_kb else 0.0
        return SystemSnapshot(
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            memory_percent=min(100.0, memory_percent),
        )


def format_bytes_per_second(rate: float) -> str:
    """Human-readable throughput, e.g. ``1,8 MiB/s``."""
    if rate >= 1024 * 1024:
        return f"{rate / 1024 / 1024:.1f} MiB/s".replace(".", ",")
    if rate >= 1024:
        return f"{rate / 1024:.0f} KiB/s"
    return f"{max(0, int(rate))} B/s"
