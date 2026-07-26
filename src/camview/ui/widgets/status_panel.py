"""Status panel: clock, machine load and how the wall is doing.

Inspired by Shinobi's sidebar summary, trimmed to what this app can
answer honestly — there is no recording here, so no disk usage; what
matters instead is how much of the machine the mosaic is costing and how
many cameras are actually up.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from camview.services.system_stats import (
    SystemMonitor,
    SystemSnapshot,
    format_bytes_per_second,
)

#: How often the machine figures are refreshed. CPU is a rate, so this is
#: also the window it is averaged over — short enough to feel live, long
#: enough not to jitter.
STATS_INTERVAL_MS = 2000
CLOCK_INTERVAL_MS = 1000

_WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def format_date(moment: datetime) -> str:
    return (
        f"{_WEEKDAYS[moment.weekday()]}, {moment.day} de "
        f"{_MONTHS[moment.month - 1]} de {moment.year}"
    )


class StatusPanel(QFrame):
    """Clock plus live figures, shown above the device tree."""

    def __init__(
        self, monitor: SystemMonitor | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._monitor = monitor or SystemMonitor()

        self._build_ui()
        self.refresh_clock()
        self.refresh_stats()

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self.refresh_clock)
        self._clock_timer.start(CLOCK_INTERVAL_MS)

        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self.refresh_stats)
        self._stats_timer.start(STATS_INTERVAL_MS)

    def _build_ui(self) -> None:
        self.date_label = QLabel()
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.date_label.setStyleSheet("color: palette(mid); font-size: 11px;")

        self.clock_label = QLabel()
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock_label.setStyleSheet("font-size: 22px; font-weight: 300;")

        rows = QGridLayout()
        rows.setContentsMargins(0, 6, 0, 0)
        rows.setHorizontalSpacing(8)
        rows.setVerticalSpacing(4)

        self.cpu_label, self.cpu_bar = self._add_row(rows, 0, "CPU")
        self.memory_label, self.memory_bar = self._add_row(rows, 1, "Memória")
        self.cameras_label, self.cameras_bar = self._add_row(rows, 2, "Câmeras")

        # Throughput gets no bar: there is no honest ceiling to fill.
        self.bandwidth_caption = QLabel("Rede")
        self.bandwidth_caption.setStyleSheet("color: palette(mid); font-size: 11px;")
        self.bandwidth_label = QLabel("—")
        self.bandwidth_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.bandwidth_label.setStyleSheet("font-size: 11px;")
        rows.addWidget(self.bandwidth_caption, 3, 0)
        rows.addWidget(self.bandwidth_label, 3, 1, 1, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(2)
        layout.addWidget(self.date_label)
        layout.addWidget(self.clock_label)
        layout.addLayout(rows)

    def _add_row(
        self, grid: QGridLayout, row: int, caption: str
    ) -> tuple[QLabel, QProgressBar]:
        caption_label = QLabel(caption)
        caption_label.setStyleSheet("color: palette(mid); font-size: 11px;")

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)

        value_label = QLabel("—")
        value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        value_label.setStyleSheet("font-size: 11px;")
        value_label.setMinimumWidth(64)

        grid.addWidget(caption_label, row, 0)
        grid.addWidget(bar, row, 1)
        grid.addWidget(value_label, row, 2)
        return value_label, bar

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def refresh_clock(self, moment: datetime | None = None) -> None:
        moment = moment or datetime.now()
        self.date_label.setText(format_date(moment))
        self.clock_label.setText(moment.strftime("%H:%M:%S"))

    def refresh_stats(self) -> None:
        self.apply_system(self._monitor.sample())

    def apply_system(self, snapshot: SystemSnapshot | None) -> None:
        """Show a reading, or dashes when the machine would not say."""
        if snapshot is None:
            self.cpu_label.setText("—")
            self.memory_label.setText("—")
            return
        # Rounded, not truncated: a bar at 11% beside a label reading 12%
        # looks like a bug even though both came from 11.6.
        self.cpu_bar.setValue(round(snapshot.cpu_percent))
        self.cpu_label.setText(f"{snapshot.cpu_percent:.0f}%")
        self.memory_bar.setValue(round(snapshot.memory_percent))
        self.memory_label.setText(f"{snapshot.memory_mb:.0f} MiB")

    def apply_streams(self, playing: int, total: int, bytes_per_second: float) -> None:
        """Show how much of the wall is live, and what it costs in traffic."""
        self.cameras_bar.setValue(int(playing / total * 100) if total else 0)
        self.cameras_label.setText(f"{playing}/{total}")
        self.bandwidth_label.setText(
            format_bytes_per_second(bytes_per_second) if total else "—"
        )
