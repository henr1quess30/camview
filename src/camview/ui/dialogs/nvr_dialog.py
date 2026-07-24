"""NVR registration/edit dialog: form fields plus a threaded connection test."""

from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from camview.models.camera import StreamType
from camview.models.nvr import Nvr
from camview.services.connectivity import check_tcp_connection

logger = logging.getLogger(__name__)


class _ConnectionTestWorker(QThread):
    """Runs the TCP reachability check off the GUI thread."""

    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, host: str, port: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._port = port

    def run(self) -> None:
        try:
            check_tcp_connection(self._host, self._port)
        except OSError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()


class NvrDialog(QDialog):
    """Add or edit an NVR's registration details.

    Pass an existing ``nvr`` (and its current ``password``, fetched from
    keyring by the caller) to pre-fill the form in edit mode; omit both
    to add a new NVR.
    """

    def __init__(
        self,
        nvr: Nvr | None = None,
        password: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editar NVR" if nvr is not None else "Adicionar NVR")
        self.setMinimumWidth(380)
        self._test_worker: _ConnectionTestWorker | None = None

        self.name_edit = QLineEdit(nvr.name if nvr else "")
        self.host_edit = QLineEdit(nvr.host if nvr else "")

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(nvr.rtsp_port if nvr else 554)

        self.username_edit = QLineEdit(nvr.username if nvr else "admin")

        self.password_edit = QLineEdit(password)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.channel_count_spin = QSpinBox()
        self.channel_count_spin.setRange(1, 128)
        self.channel_count_spin.setValue(nvr.channel_count if nvr else 4)

        self.default_stream_combo = QComboBox()
        self.default_stream_combo.addItem("Principal", StreamType.MAIN)
        self.default_stream_combo.addItem("Substream", StreamType.SUB)
        if nvr and nvr.default_stream == StreamType.SUB:
            self.default_stream_combo.setCurrentIndex(1)

        self.test_button = QPushButton("Testar conexão")
        self.test_button.clicked.connect(self._on_test_connection)
        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Nome", self.name_edit)
        form.addRow("Endereço (IP/host)", self.host_edit)
        form.addRow("Porta RTSP", self.port_spin)
        form.addRow("Usuário", self.username_edit)
        form.addRow("Senha", self.password_edit)
        form.addRow("Quantidade de canais", self.channel_count_spin)
        form.addRow("Stream padrão", self.default_stream_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.test_button)
        layout.addWidget(self.test_result_label)
        layout.addWidget(buttons)

    def _on_test_connection(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            self.test_result_label.setText("Informe o endereço antes de testar.")
            return

        self.test_button.setEnabled(False)
        self.test_result_label.setText("Testando conexão...")

        self._test_worker = _ConnectionTestWorker(host, self.port_spin.value(), self)
        self._test_worker.succeeded.connect(self._on_test_succeeded)
        self._test_worker.failed.connect(self._on_test_failed)
        self._test_worker.finished.connect(lambda: self.test_button.setEnabled(True))
        self._test_worker.start()

    def _on_test_succeeded(self) -> None:
        self.test_result_label.setText("Conexão bem-sucedida.")

    def _on_test_failed(self, message: str) -> None:
        logger.warning("NVR connection test failed: %s", message)
        self.test_result_label.setText(f"Falha na conexão: {message}")

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "CamView", "Informe um nome para o NVR.")
            return
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "CamView", "Informe o endereço do NVR.")
            return
        if not self.username_edit.text().strip():
            QMessageBox.warning(self, "CamView", "Informe o usuário.")
            return
        self.accept()

    def result_nvr(self, existing: Nvr | None = None) -> Nvr:
        """Build an ``Nvr`` from the current form values.

        Pass the original ``Nvr`` when editing so its id/timestamps carry over.
        """
        return Nvr(
            id=existing.id if existing else None,
            name=self.name_edit.text().strip(),
            host=self.host_edit.text().strip(),
            rtsp_port=self.port_spin.value(),
            username=self.username_edit.text().strip(),
            channel_count=self.channel_count_spin.value(),
            # QComboBox userData round-trips a StreamType (str, Enum) as a
            # plain str, since Qt's variant marshalling follows the str
            # mix-in rather than preserving the Python enum type.
            default_stream=StreamType(self.default_stream_combo.currentData()),
            created_at=existing.created_at if existing else None,
            updated_at=existing.updated_at if existing else None,
        )

    def result_password(self) -> str:
        return self.password_edit.text()
