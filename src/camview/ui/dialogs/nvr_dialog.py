"""NVR registration/edit dialog: form fields plus a threaded connection test."""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
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
from camview.services.hikvision import (
    DiscoveredChannel,
    DiscoveryError,
    discover_channels,
)

logger = logging.getLogger(__name__)

#: Characters that mean the user pasted a URL instead of a host.
_INVALID_HOST_CHARS = re.compile(r"[\s/:@]")


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


class _ChannelDiscoveryWorker(QThread):
    """Runs Hikvision ISAPI channel discovery off the GUI thread."""

    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(
        self, host: str, username: str, password: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._username = username
        self._password = password

    def run(self) -> None:
        try:
            channels = discover_channels(self._host, self._username, self._password)
        except DiscoveryError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(channels)


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
        self._discovery_worker: _ChannelDiscoveryWorker | None = None
        #: Populated by "Detectar canais"; consumed by MainWindow so the
        #: cameras it creates use the device's real channels and names.
        self.discovered_channels: list[DiscoveredChannel] | None = None

        self.name_edit = QLineEdit(nvr.name if nvr else "")
        self.host_edit = QLineEdit(nvr.host if nvr else "")

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(nvr.rtsp_port if nvr else 554)

        self.username_edit = QLineEdit(nvr.username if nvr else "admin")

        self.password_edit = QLineEdit(password)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        # Typing an NVR password blind is where wrong credentials come from,
        # and wrong credentials are what get this machine's IP locked out.
        self.reveal_password_action = self.password_edit.addAction(
            QIcon.fromTheme("view-visible"),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.reveal_password_action.setCheckable(True)
        self.reveal_password_action.setToolTip("Mostrar a senha")
        self.reveal_password_action.toggled.connect(self._on_reveal_password)

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

        self.detect_button = QPushButton("Detectar canais")
        self.detect_button.setToolTip(
            "Pergunta ao equipamento quais canais existem e como se chamam"
        )
        self.detect_button.clicked.connect(self._on_detect_channels)

        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)

        self.host_edit.setPlaceholderText("192.168.0.10")

        form = QFormLayout()
        form.addRow("Nome:", self.name_edit)
        form.addRow("Endereço (IP/host):", self.host_edit)
        form.addRow("Porta RTSP:", self.port_spin)
        form.addRow("Usuário:", self.username_edit)
        form.addRow("Senha:", self.password_edit)
        form.addRow("Quantidade de canais:", self.channel_count_spin)
        form.addRow("Stream padrão:", self.default_stream_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        actions = QHBoxLayout()
        actions.addWidget(self.test_button)
        actions.addWidget(self.detect_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(actions)
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

    def _on_detect_channels(self) -> None:
        host = self.host_edit.text().strip()
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not (host and username and password):
            self.test_result_label.setText(
                "Informe endereço, usuário e senha antes de detectar os canais."
            )
            return

        self.detect_button.setEnabled(False)
        self.test_result_label.setText("Detectando canais...")

        self._discovery_worker = _ChannelDiscoveryWorker(host, username, password, self)
        self._discovery_worker.succeeded.connect(self._on_discovery_succeeded)
        self._discovery_worker.failed.connect(self._on_discovery_failed)
        self._discovery_worker.finished.connect(
            lambda: self.detect_button.setEnabled(True)
        )
        self._discovery_worker.start()

    def _on_discovery_succeeded(self, channels: list[DiscoveredChannel]) -> None:
        self.discovered_channels = channels
        # The device is the authority on how many channels exist, so mirror
        # its answer into the spin box rather than leaving the two disagreeing.
        self.channel_count_spin.setValue(max(c.channel_number for c in channels))
        named = sum(1 for c in channels if not c.name.startswith("Canal "))
        self.test_result_label.setText(
            f"{len(channels)} canais detectados"
            + (f", {named} com nome configurado." if named else ".")
        )

    def _on_discovery_failed(self, message: str) -> None:
        logger.warning("Channel discovery failed: %s", message)
        self.discovered_channels = None
        self.test_result_label.setText(f"Não foi possível detectar canais: {message}")

    def _on_test_succeeded(self) -> None:
        self.test_result_label.setText("Conexão bem-sucedida.")

    def _on_test_failed(self, message: str) -> None:
        logger.warning("NVR connection test failed: %s", message)
        self.test_result_label.setText(f"Falha na conexão: {message}")

    def _on_reveal_password(self, revealed: bool) -> None:
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password
        )
        self.reveal_password_action.setIcon(
            QIcon.fromTheme("view-hidden" if revealed else "view-visible")
        )
        self.reveal_password_action.setToolTip(
            "Ocultar a senha" if revealed else "Mostrar a senha"
        )

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "CamView", "Informe um nome para o NVR.")
            return

        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "CamView", "Informe o endereço do NVR.")
            return
        if _INVALID_HOST_CHARS.search(host):
            # A pasted "rtsp://10.0.0.5:554/..." would otherwise be stored as
            # the host and produce an unexplainable connection failure later.
            QMessageBox.warning(
                self,
                "CamView",
                "O endereço deve ser apenas o IP ou o nome do equipamento "
                "(sem 'rtsp://', caminho ou espaços).",
            )
            return

        if not self.username_edit.text().strip():
            QMessageBox.warning(self, "CamView", "Informe o usuário.")
            return

        if not self.password_edit.text():
            # Not a hard block — an NVR can be registered now and given its
            # password later — but silence here turns into cells that refuse
            # to open with no obvious cause.
            proceed = QMessageBox.question(
                self,
                "CamView",
                "Nenhuma senha informada.\n\nO CamView não abrirá os streams "
                "deste NVR enquanto não houver senha armazenada. Salvar mesmo "
                "assim?",
            )
            if proceed != QMessageBox.StandardButton.Yes:
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
