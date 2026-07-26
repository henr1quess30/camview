"""Tests for the machine-usage readings and the status panel.

The system figures come from ``/proc``, so the tests point the reader at
a temporary directory holding fake files — no dependency on what this
machine happens to be doing while the suite runs.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fakes import FakeInstance
from PySide6.QtWidgets import QApplication

from camview.database.repositories import SettingsRepository
from camview.models.nvr import Nvr
from camview.models.settings import AppSettings
from camview.services.credentials import set_nvr_password
from camview.services.rtsp import generate_missing_channel_cameras
from camview.services.settings import save_settings
from camview.services.system_stats import (
    SystemMonitor,
    SystemSnapshot,
    format_bytes_per_second,
)
from camview.ui.main_window import MainWindow
from camview.ui.widgets.status_panel import StatusPanel, format_date

TEST_PASSWORD = "test-password"


def write_proc(
    root: Path, cpu_ticks: int = 0, rss_kb: int = 512_000, total_kb: int = 8_000_000
) -> Path:
    """A minimal ``/proc`` holding just what SystemMonitor reads."""
    (root / "self").mkdir(parents=True, exist_ok=True)
    # Field 14/15 (utime/stime) come after the parenthesised name.
    fields = ["0"] * 50
    fields[11] = str(cpu_ticks)  # utime, counting from after ")"
    fields[12] = "0"  # stime
    (root / "self" / "stat").write_text(f"1 (camview app) S {' '.join(fields)}")
    (root / "self" / "status").write_text(f"Name:\tcamview\nVmRSS:\t{rss_kb} kB\n")
    (root / "meminfo").write_text(f"MemTotal:       {total_kb} kB\nMemFree: 100 kB\n")
    return root


class TestSystemMonitor:
    def test_the_first_sample_reports_no_cpu_yet(self, tmp_path: Path) -> None:
        """CPU is a rate: one reading cannot be one."""
        monitor = SystemMonitor(proc_root=write_proc(tmp_path), cpu_count=4)

        snapshot = monitor.sample(now=100.0)

        assert snapshot is not None
        assert snapshot.cpu_percent == 0.0

    def test_cpu_is_the_share_of_the_whole_machine(self, tmp_path: Path) -> None:
        root = write_proc(tmp_path, cpu_ticks=0)
        monitor = SystemMonitor(proc_root=root, cpu_count=4)
        monitor.sample(now=100.0)

        # Two seconds of wall time, two seconds of CPU (200 ticks at 100Hz):
        # one core fully busy out of four.
        write_proc(root, cpu_ticks=200)
        snapshot = monitor.sample(now=102.0)

        assert snapshot is not None
        assert snapshot.cpu_percent == pytest.approx(25.0, abs=1.0)

    def test_cpu_never_exceeds_a_hundred_percent(self, tmp_path: Path) -> None:
        root = write_proc(tmp_path, cpu_ticks=0)
        monitor = SystemMonitor(proc_root=root, cpu_count=1)
        monitor.sample(now=100.0)

        write_proc(root, cpu_ticks=10_000)
        snapshot = monitor.sample(now=100.5)

        assert snapshot is not None
        assert snapshot.cpu_percent == 100.0

    def test_memory_is_reported_in_mib_and_percent(self, tmp_path: Path) -> None:
        root = write_proc(tmp_path, rss_kb=1_024_000, total_kb=8_192_000)
        monitor = SystemMonitor(proc_root=root)

        snapshot = monitor.sample(now=1.0)

        assert snapshot is not None
        assert snapshot.memory_mb == pytest.approx(1000.0, abs=1.0)
        assert snapshot.memory_percent == pytest.approx(12.5, abs=0.1)

    def test_a_process_name_with_spaces_does_not_break_parsing(
        self, tmp_path: Path
    ) -> None:
        """The comm field is parenthesised and may contain anything."""
        root = write_proc(tmp_path)
        fields = ["0"] * 50
        fields[11] = "100"
        (root / "self" / "stat").write_text(
            f"1 (some (weird) name) S {' '.join(fields)}"
        )
        monitor = SystemMonitor(proc_root=root, cpu_count=1)
        monitor.sample(now=0.0)

        write_proc(root, cpu_ticks=200)
        snapshot = monitor.sample(now=1.0)

        assert snapshot is not None

    def test_missing_proc_files_report_nothing(self, tmp_path: Path) -> None:
        assert SystemMonitor(proc_root=tmp_path / "nope").sample() is None

    def test_a_machine_without_meminfo_still_reports_size(
        self, tmp_path: Path
    ) -> None:
        root = write_proc(tmp_path)
        (root / "meminfo").unlink()

        snapshot = SystemMonitor(proc_root=root).sample(now=1.0)

        assert snapshot is not None
        assert snapshot.memory_mb > 0
        assert snapshot.memory_percent == 0.0


class TestFormatting:
    @pytest.mark.parametrize(
        ("rate", "expected"),
        [
            (0, "0 B/s"),
            (512, "512 B/s"),
            (2048, "2 KiB/s"),
            (233_000, "228 KiB/s"),
            (3_500_000, "3,3 MiB/s"),
        ],
    )
    def test_throughput_reads_naturally(self, rate: float, expected: str) -> None:
        assert format_bytes_per_second(rate) == expected

    def test_the_date_is_written_in_portuguese(self) -> None:
        assert format_date(datetime(2026, 7, 26)) == "domingo, 26 de julho de 2026"


class TestStatusPanel:
    def test_it_shows_the_clock_it_is_given(self, qapp: QApplication) -> None:
        panel = StatusPanel()

        panel.refresh_clock(datetime(2026, 7, 26, 19, 17, 19))

        assert panel.clock_label.text() == "19:17:19"
        assert "26 de julho" in panel.date_label.text()

    def test_it_shows_machine_figures(self, qapp: QApplication) -> None:
        panel = StatusPanel()

        panel.apply_system(
            SystemSnapshot(cpu_percent=14.2, memory_mb=748.9, memory_percent=9.1)
        )

        assert panel.cpu_label.text() == "14%"
        assert panel.cpu_bar.value() == 14
        assert panel.memory_label.text() == "749 MiB"

    def test_unavailable_figures_show_dashes(self, qapp: QApplication) -> None:
        panel = StatusPanel()
        panel.apply_system(
            SystemSnapshot(cpu_percent=10, memory_mb=100, memory_percent=5)
        )

        panel.apply_system(None)

        assert panel.cpu_label.text() == "—"

    def test_it_shows_how_much_of_the_wall_is_live(self, qapp: QApplication) -> None:
        panel = StatusPanel()

        panel.apply_streams(playing=9, total=12, bytes_per_second=1_500_000)

        assert panel.cameras_label.text() == "9/12"
        assert panel.cameras_bar.value() == 75
        assert panel.bandwidth_label.text() == "1,4 MiB/s"

    def test_an_empty_mosaic_reports_no_traffic(self, qapp: QApplication) -> None:
        panel = StatusPanel()

        panel.apply_streams(playing=0, total=0, bytes_per_second=0)

        assert panel.cameras_label.text() == "0/0"
        assert panel.bandwidth_label.text() == "—"


class TestPanelInTheWindow:
    @pytest.fixture
    def window(
        self,
        qapp: QApplication,
        db_connection: sqlite3.Connection,
        fake_keyring: object,
        fake_instance: FakeInstance,
    ) -> MainWindow:
        win = MainWindow(connection=db_connection)
        nvr = win._nvr_repository.create(
            Nvr(name="NVR", host="192.0.2.10", username="admin", channel_count=4)
        )
        set_nvr_password(nvr.id, TEST_PASSWORD)  # type: ignore[arg-type]
        for camera in generate_missing_channel_cameras(nvr.id, 4):  # type: ignore[arg-type]
            win._camera_repository.create(camera)
        win.device_tree.refresh()
        return win

    def test_the_panel_is_shown_by_default(self, window: MainWindow) -> None:
        assert window.status_panel is not None
        assert window._settings.show_status_panel is True

    def test_it_can_be_turned_off(
        self, window: MainWindow, db_connection: sqlite3.Connection
    ) -> None:
        save_settings(
            SettingsRepository(db_connection), AppSettings(show_status_panel=False)
        )

        reopened = MainWindow(connection=db_connection)

        assert reopened.status_panel.isVisible() is False

    def test_the_panel_follows_the_mosaic(self, window: MainWindow) -> None:
        nvr = window._nvr_repository.list_all()[0]
        window._open_nvr_mosaic(nvr.id)  # type: ignore[arg-type]

        window._refresh_stream_stats()

        assert window.status_panel.cameras_label.text().endswith("/4")
        window.video_grid.clear()

    def test_traffic_is_a_rate_not_a_total(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from camview.ui import main_window as mw

        window._open_camera_at(
            window._camera_repository.list_by_nvr(
                window._nvr_repository.list_all()[0].id
            )[0].id,
            0,
        )
        window.video_grid.tile_at(0)._connect()  # type: ignore[union-attr]

        monkeypatch.setattr(mw, "bytes_received", lambda _player: 1_000_000)
        window._stream_sampled_at = window._stream_sampled_at - 2.0
        window._stream_bytes = 0

        window._refresh_stream_stats()

        assert "KiB/s" in window.status_panel.bandwidth_label.text()
        window.video_grid.clear()

    def test_a_restarted_stream_does_not_report_negative_traffic(
        self, window: MainWindow, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counters reset when a stream reconnects."""
        from camview.ui import main_window as mw

        window._open_camera_at(
            window._camera_repository.list_by_nvr(
                window._nvr_repository.list_all()[0].id
            )[0].id,
            0,
        )
        window.video_grid.tile_at(0)._connect()  # type: ignore[union-attr]
        window._stream_bytes = 5_000_000

        monkeypatch.setattr(mw, "bytes_received", lambda _player: 10)
        window._refresh_stream_stats()

        assert window.status_panel.bandwidth_label.text() == "0 B/s"
        window.video_grid.clear()
