"""Settings dialog: playback, reconnection, startup and logging.

The dialog only edits an :class:`AppSettings` value — persisting it and
applying it to players is ``MainWindow``'s job, which keeps this widget
testable without a database.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from camview.config import get_default_log_dir
from camview.models.settings import AppSettings, MosaicStream

#: Editable shortcuts, in the order they appear in the dialog.
SHORTCUT_LABELS: dict[str, str] = {
    "shortcut_next_camera": "Próxima câmera:",
    "shortcut_previous_camera": "Câmera anterior:",
    "shortcut_zoom_in": "Aproximar:",
    "shortcut_zoom_out": "Afastar:",
    "shortcut_zoom_reset": "Zoom normal:",
}

MOSAIC_STREAM_LABELS: dict[MosaicStream, str] = {
    MosaicStream.SUB: "Substream (menos banda e CPU)",
    MosaicStream.MAIN: "Stream principal (mais nitidez e fluidez)",
    MosaicStream.NVR_DEFAULT: "Seguir o padrão de cada NVR",
}


class SettingsDialog(QDialog):
    """Edits an :class:`AppSettings`; read the result from :meth:`result_settings`."""

    def __init__(
        self, settings: AppSettings, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configurações")
        self._settings = settings
        self._build_ui()
        self._load(settings)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        playback = QGroupBox("Reprodução")
        playback_form = QFormLayout(playback)

        self.network_caching_spin = QSpinBox()
        self.network_caching_spin.setRange(0, 10_000)
        self.network_caching_spin.setSingleStep(100)
        self.network_caching_spin.setSuffix(" ms")
        self.network_caching_spin.setToolTip(
            "Buffer antes de exibir. Menor = menos atraso, porém menos "
            "tolerante a oscilação de rede."
        )
        playback_form.addRow("Latência (buffer):", self.network_caching_spin)

        self.transport_combo = QComboBox()
        self.transport_combo.addItem("TCP (mais confiável)", True)
        self.transport_combo.addItem("UDP (menor latência)", False)
        playback_form.addRow("Transporte RTSP:", self.transport_combo)

        self.mosaic_stream_combo = QComboBox()
        for stream, label in MOSAIC_STREAM_LABELS.items():
            self.mosaic_stream_combo.addItem(label, stream.value)
        self.mosaic_stream_combo.setToolTip(
            "Vale para as células do mosaico. Cada célula ainda pode ser "
            "trocada individualmente pelo botão direito."
        )
        playback_form.addRow("Stream do mosaico:", self.mosaic_stream_combo)

        self.mute_check = QCheckBox("Sem áudio")
        playback_form.addRow("", self.mute_check)
        root.addWidget(playback)

        reconnect = QGroupBox("Reconexão")
        reconnect_form = QFormLayout(reconnect)
        self.reconnect_check = QCheckBox("Reconectar automaticamente")
        self.reconnect_check.toggled.connect(self._update_enabled_state)
        reconnect_form.addRow("", self.reconnect_check)

        self.max_delay_spin = QSpinBox()
        self.max_delay_spin.setRange(1, 600)
        self.max_delay_spin.setSuffix(" s")
        self.max_delay_spin.setToolTip(
            "Teto do intervalo entre tentativas (2s, 5s, 10s, ... até este valor)."
        )
        reconnect_form.addRow("Intervalo máximo:", self.max_delay_spin)
        root.addWidget(reconnect)

        startup = QGroupBox("Ao abrir")
        startup_form = QFormLayout(startup)
        self.maximized_check = QCheckBox("Iniciar com a janela maximizada")
        startup_form.addRow("", self.maximized_check)
        self.restore_layout_check = QCheckBox("Reabrir o último layout")
        startup_form.addRow("", self.restore_layout_check)
        self.status_panel_check = QCheckBox(
            "Mostrar o painel de status (relógio, CPU, memória, rede)"
        )
        startup_form.addRow("", self.status_panel_check)
        self.update_check = QCheckBox("Avisar quando houver uma versão nova")
        self.update_check.setToolTip(
            "Consulta as releases publicadas no GitHub. Nada é baixado nem "
            "instalado automaticamente."
        )
        startup_form.addRow("", self.update_check)
        root.addWidget(startup)

        shortcuts = QGroupBox("Atalhos de teclado")
        shortcuts_form = QFormLayout(shortcuts)
        # QKeySequenceEdit records the actual key press, so the stored
        # string is always something Qt can parse back.
        self.shortcut_edits: dict[str, QKeySequenceEdit] = {}
        for field, label in SHORTCUT_LABELS.items():
            edit = QKeySequenceEdit()
            edit.setMaximumSequenceLength(1)
            self.shortcut_edits[field] = edit
            shortcuts_form.addRow(label, edit)
        hint = QLabel("Valem com uma câmera em tela cheia ou com a célula selecionada.")
        hint.setStyleSheet("color: palette(mid);")
        hint.setWordWrap(True)
        shortcuts_form.addRow("", hint)
        root.addWidget(shortcuts)

        logging_box = QGroupBox("Logs")
        logging_layout = QVBoxLayout(logging_box)
        picker = QHBoxLayout()
        self.log_dir_edit = QLineEdit()
        self.log_dir_edit.setPlaceholderText(str(get_default_log_dir()))
        browse = QPushButton("Escolher...")
        browse.clicked.connect(self._choose_log_dir)
        reset = QPushButton("Padrão")
        reset.clicked.connect(self.log_dir_edit.clear)
        picker.addWidget(self.log_dir_edit)
        picker.addWidget(browse)
        picker.addWidget(reset)
        logging_layout.addLayout(picker)
        hint = QLabel("A mudança de diretório passa a valer na próxima abertura.")
        hint.setStyleSheet("color: palette(mid);")
        logging_layout.addWidget(hint)
        root.addWidget(logging_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults
        ).clicked.connect(lambda: self._load(AppSettings()))
        root.addWidget(buttons)

    def _update_enabled_state(self, reconnect_enabled: bool) -> None:
        self.max_delay_spin.setEnabled(reconnect_enabled)

    def _choose_log_dir(self) -> None:
        current = self.log_dir_edit.text() or str(get_default_log_dir())
        chosen = QFileDialog.getExistingDirectory(
            self, "Diretório de logs", current
        )
        if chosen:
            self.log_dir_edit.setText(chosen)

    def _load(self, settings: AppSettings) -> None:
        self.network_caching_spin.setValue(settings.network_caching_ms)
        self.transport_combo.setCurrentIndex(
            0 if settings.rtsp_transport_tcp else 1
        )
        self.mosaic_stream_combo.setCurrentIndex(
            self.mosaic_stream_combo.findData(settings.mosaic_stream.value)
        )
        self.mute_check.setChecked(settings.mute_audio)
        self.reconnect_check.setChecked(settings.reconnect_enabled)
        self.max_delay_spin.setValue(settings.max_reconnect_delay_s)
        self.maximized_check.setChecked(settings.start_maximized)
        self.restore_layout_check.setChecked(settings.restore_last_layout)
        self.status_panel_check.setChecked(settings.show_status_panel)
        self.update_check.setChecked(settings.check_for_updates)
        self.log_dir_edit.setText("" if settings.log_dir is None else str(settings.log_dir))
        for field, edit in self.shortcut_edits.items():
            edit.setKeySequence(QKeySequence(getattr(settings, field)))
        self._update_enabled_state(settings.reconnect_enabled)

    def result_settings(self) -> AppSettings:
        """The edited settings. Only meaningful once the dialog was accepted."""
        log_dir = self.log_dir_edit.text().strip()
        defaults = AppSettings()
        shortcuts = {
            field: edit.keySequence().toString() or getattr(defaults, field)
            for field, edit in self.shortcut_edits.items()
        }
        return AppSettings(
            **shortcuts,
            network_caching_ms=self.network_caching_spin.value(),
            rtsp_transport_tcp=bool(self.transport_combo.currentData()),
            mute_audio=self.mute_check.isChecked(),
            reconnect_enabled=self.reconnect_check.isChecked(),
            max_reconnect_delay_s=self.max_delay_spin.value(),
            # Rebuilt from the stored value: the combo hands back a plain
            # str, since MosaicStream inherits from str (same trap the NVR
            # dialog's stream combo hit in Phase 2).
            mosaic_stream=MosaicStream(self.mosaic_stream_combo.currentData()),
            start_maximized=self.maximized_check.isChecked(),
            restore_last_layout=self.restore_layout_check.isChecked(),
            show_status_panel=self.status_panel_check.isChecked(),
            check_for_updates=self.update_check.isChecked(),
            log_dir=Path(log_dir) if log_dir else None,
        )
