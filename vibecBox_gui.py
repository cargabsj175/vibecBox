#!/usr/bin/env python3
"""GUI de vibecBox (PyQt6) — Etapas 2 y 4.

La vista no construye argumentos de QEMU: edita ``VMConfig`` y delega el
lanzamiento en ``qemu.plan_launch`` y ``runner.VMRunner``. La ventana de la VM
la abre QEMU (GTK/SDL); aquí solo se supervisa el proceso y su salida.

Ejecutar:
    .venv/bin/python vibecBox_gui.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import cast

try:
    from PyQt6.QtCore import QCoreApplication, QSettings, QSize, Qt, QT_VERSION_STR
    from PyQt6.QtGui import QAction, QColor, QFontDatabase, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDockWidget,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QStackedLayout,
        QStyle,
        QToolBar,
        QVBoxLayout,
        QWidget,
        QWizard,
        QWizardPage,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - depende del entorno
    raise SystemExit(
        "PyQt6 no está instalado.\n"
        "Crea el entorno del proyecto:\n"
        "  python3 -m venv --system-site-packages .venv\n"
        "  .venv/bin/pip install -r requirements.txt"
    ) from exc

import models
import qemu
import runner
import vibecBox_core as core

LOG = logging.getLogger(__name__)

MIN_QT_VERSION = (6, 4)
DEFAULT_RAM_MAX_MB = 4096
CLOSE_GRACEFUL_TIMEOUT_MS = 2000
CLOSE_KILL_TIMEOUT_MS = 2000
_WAIT_SLICE_MS = 50
LOG_FILE_NAME = "vibecBox.log"
LOG_FILE_MAX_BYTES = 1_048_576
LOG_FILE_BACKUPS = 3
STATE_COLORS = {
    models.VMState.STOPPED: "#8a94a6",
    models.VMState.RUNNING: "#2e9e5b",
    models.VMState.PAUSED: "#c98a1b",
    models.VMState.ERROR: "#c0392b",
}

STYLE = """
QMainWindow { background: #f4f6fa; }
QToolBar { background: #ffffff; border-bottom: 1px solid #dfe4ec; spacing: 6px; padding: 4px; }
QToolBar QToolButton { padding: 6px 10px; border-radius: 6px; }
QToolBar QToolButton:hover { background: #e8effc; }
QListWidget {
    background: #ffffff; border: 1px solid #dfe4ec; border-radius: 8px;
    padding: 4px; outline: 0;
}
QListWidget::item { padding: 8px 10px; border-radius: 6px; }
QListWidget::item:selected { background: #e8effc; color: #1c2b4a; }
QGroupBox {
    background: #ffffff; border: 1px solid #dfe4ec; border-radius: 8px;
    margin-top: 12px; padding: 12px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #3b4a63; }
QLabel#ValueLabel { font-weight: 400; color: #24324a; }
QLabel#EmptyLabel { color: #7b8798; font-size: 14px; }
QStatusBar { background: #ffffff; border-top: 1px solid #dfe4ec; }
QStatusBar QLabel { color: #4a586e; padding: 0 8px; }
"""


# --------------------------------------------------------------------------- #
# Información del host
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HostInfo:
    ram_max_mb: int
    cores_max: int
    kvm_ok: bool
    qemu_path: str | None
    qemu_version: str | None
    img_version: str | None
    display: core.DisplaySupport | None
    ram_error: str | None = None

    @property
    def effective_ram_max_mb(self) -> int:
        """RAM máxima usable; cae a un valor seguro si la lectura falló."""
        value = self.ram_max_mb
        if isinstance(value, int) and value >= models.MIN_RAM_MB:
            return value
        return DEFAULT_RAM_MAX_MB

    @property
    def effective_cores_max(self) -> int:
        """CPUs lógicas usables; cae a 1 si el dato no es válido."""
        value = self.cores_max
        if isinstance(value, int) and value >= 1:
            return value
        return 1

    @property
    def resources_degraded(self) -> bool:
        return (
            self.ram_error is not None
            or self.effective_ram_max_mb != self.ram_max_mb
            or self.effective_cores_max != self.cores_max
        )


def load_host_info() -> HostInfo:
    """Lee las capacidades reales; nunca impide abrir la ventana."""
    ram_max = 4096
    ram_error: str | None = None
    try:
        ram_max = core.get_ram_max()
    except core.VibecBoxError as exc:
        ram_error = str(exc)

    qemu = core.describe_binary(core.QEMU_SYSTEM)
    qemu_img = core.describe_binary(core.QEMU_IMG)
    display = core.display_backends(qemu.path) if qemu.path else None

    return HostInfo(
        ram_max_mb=ram_max,
        cores_max=core.get_cores_max(),
        kvm_ok=core.check_kvm(),
        qemu_path=qemu.path,
        qemu_version=qemu.version,
        img_version=qemu_img.version,
        display=display,
        ram_error=ram_error,
    )


# --------------------------------------------------------------------------- #
# Iconos
# --------------------------------------------------------------------------- #
def state_icon(state: models.VMState) -> QIcon:
    size = 12
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(STATE_COLORS[state]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


def standard_icon(widget: QWidget, pixmap: QStyle.StandardPixmap) -> QIcon:
    return widget.style().standardIcon(pixmap)


# --------------------------------------------------------------------------- #
# Asistente de creación / edición
# --------------------------------------------------------------------------- #
class NamePage(QWizardPage):
    def __init__(self, wizard: "VMWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle(self.tr("Nombre e instalación"))
        self.setSubTitle(
            self.tr(
                "Identifica la máquina y, si quieres, elige una ISO de arranque."
            )
        )

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.tr("p. ej. Ubuntu 24.04"))
        self.name_edit.setMaxLength(models.MAX_NAME_LENGTH)
        self.name_edit.textChanged.connect(self._refresh)

        self.iso_edit = QLineEdit()
        self.iso_edit.setPlaceholderText(
            self.tr("Sin ISO (arranque desde disco)")
        )
        self.iso_edit.textChanged.connect(self._refresh)

        browse = QPushButton(self.tr("Examinar…"))
        browse.clicked.connect(self._pick_iso)
        clear = QPushButton(self.tr("Quitar"))
        clear.clicked.connect(self.iso_edit.clear)

        iso_row = QHBoxLayout()
        iso_row.addWidget(self.iso_edit, 1)
        iso_row.addWidget(browse)
        iso_row.addWidget(clear)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("EmptyLabel")

        layout = QFormLayout(self)
        layout.addRow(self.tr("Nombre:"), self.name_edit)
        layout.addRow(self.tr("Imagen ISO:"), iso_row)
        layout.addRow(self.hint)
        self._refresh()

    def _pick_iso(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Seleccionar imagen ISO"),
            str(Path.home()),
            self.tr("Imágenes ISO (*.iso);;Todos los ficheros (*)"),
        )
        if path:
            self.iso_edit.setText(path)

    def initializePage(self) -> None:
        if not self.name_edit.text():
            self.name_edit.setText(
                self.tr("VM {number}").format(
                    number=len(self._wizard.store.list()) + 1
                )
            )
            self.name_edit.selectAll()
        self._refresh()

    def _validation_errors(self) -> list[str]:
        names = self._wizard.store.names(exclude_id=self._wizard.existing_id)
        errors = models.name_errors(
            self.name_edit.text(), existing_names=names
        )
        errors.extend(
            models.iso_path_errors(
                self.iso_edit.text().strip() or None, check_paths=True
            )
        )
        return errors

    def _refresh(self) -> None:
        """Actualiza el hint y notifica al asistente; no valida por sí solo."""
        errors = self._validation_errors()
        self.hint.setText(
            "\n".join(errors)
            if errors
            else self.tr(
                "La ISO es opcional: también puedes arrancar desde el disco."
            )
        )
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return not self._validation_errors()

    def validatePage(self) -> bool:
        errors = self._validation_errors()
        if errors:
            QMessageBox.warning(
                self, self.tr("Nombre no válido"), "\n• ".join(errors)
            )
            return False
        return True


class ResourcesPage(QWizardPage):
    def __init__(self, wizard: "VMWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle(self.tr("Memoria y procesadores"))
        self.setSubTitle(
            self.tr("Ajusta los recursos según las capacidades reales del host.")
        )

        host = wizard.host
        ram_max = host.effective_ram_max_mb
        cores_max = host.effective_cores_max

        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(models.MIN_RAM_MB, ram_max)
        self.ram_spin.setSingleStep(256)
        self.ram_spin.setSuffix(self.tr(" MB"))
        self.ram_spin.setValue(min(2048, ram_max))

        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, cores_max)
        self.cpu_spin.setValue(min(2, cores_max))
        self.cpu_spin.setSuffix(self.tr(" vCPU"))

        self.ram_range = QLabel(
            self.tr("Disponible en el host: {ram} MB ({gib:.1f} GiB).").format(
                ram=ram_max, gib=ram_max / 1024
            )
        )
        self.cpu_range = QLabel(
            self.tr("Hilos lógicos del host: {cores}.").format(cores=cores_max)
        )
        for label in (self.ram_range, self.cpu_range):
            label.setObjectName("EmptyLabel")

        self.warning: QLabel | None = None
        if host.resources_degraded:
            notices: list[str] = []
            if host.ram_error:
                notices.append(
                    self.tr(
                        "RAM: {error} Se asumen {ram} MB."
                    ).format(error=host.ram_error, ram=ram_max)
                )
            elif ram_max != host.ram_max_mb:
                notices.append(
                    self.tr(
                        "RAM del host no válida ({value}); se asumen {ram} MB."
                    ).format(value=host.ram_max_mb, ram=ram_max)
                )
            if cores_max != host.cores_max:
                notices.append(
                    self.tr(
                        "CPUs del host no válidas ({value}); se asume {cores}."
                    ).format(value=host.cores_max, cores=cores_max)
                )
            self.warning = QLabel("\n".join(notices))
            self.warning.setWordWrap(True)
            self.warning.setStyleSheet("color: #c0392b; font-weight: 600;")

        layout = QFormLayout(self)
        layout.addRow(self.tr("Memoria RAM:"), self.ram_spin)
        layout.addRow(self.ram_range)
        layout.addRow(self.tr("Procesadores:"), self.cpu_spin)
        layout.addRow(self.cpu_range)
        if self.warning is not None:
            layout.addRow(self.warning)

    def isComplete(self) -> bool:
        return True


class StoragePage(QWizardPage):
    def __init__(self, wizard: "VMWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle(self.tr("Disco virtual"))
        self.setSubTitle(
            self.tr(
                "Se creará un disco qcow2 la primera vez que arranques la VM."
            )
        )

        self.disk_spin = QSpinBox()
        self.disk_spin.setRange(models.MIN_DISK_GB, models.MAX_DISK_GB)
        self.disk_spin.setSuffix(self.tr(" GB"))
        self.disk_spin.setValue(20)
        self.disk_spin.valueChanged.connect(self._update_path)

        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setObjectName("ValueLabel")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout = QFormLayout(self)
        layout.addRow(self.tr("Tamaño:"), self.disk_spin)
        layout.addRow(self.tr("Fichero:"), self.path_label)

    def initializePage(self) -> None:
        self._update_path()

    def _update_path(self) -> None:
        preview = models.VMConfig(
            name=self._wizard.current_name(),
            id=self._wizard.existing_id or "preview0000",
        )
        self.path_label.setText(str(preview.default_disk_path()))

    def isComplete(self) -> bool:
        return True


class ConnectionPage(QWizardPage):
    def __init__(self, wizard: "VMWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle(self.tr("Red, vídeo y arranque"))
        self.setSubTitle(
            self.tr("Valores VirtIO recomendados para el mejor rendimiento.")
        )

        self.network = QComboBox()
        self.network.addItem(self.tr("NAT (modo usuario)"), "user")
        self.network.addItem(
            self.tr("Puente (bridge, requiere configuración)"), "bridge"
        )
        self.network.addItem(self.tr("Sin red"), "none")

        self.video = QComboBox()
        self.video.addItem(self.tr("virtio-vga (recomendado)"), "virtio-vga")
        self.video.addItem(self.tr("virtio-gpu"), "virtio-gpu")
        self.video.addItem(self.tr("VGA estándar"), "std")

        self.boot = QComboBox()
        self.boot.addItem(self.tr("Disco duro"), "disk")
        self.boot.addItem(self.tr("CD/ISO"), "cdrom")
        self.boot.addItem(self.tr("Red (PXE)"), "network")

        layout = QFormLayout(self)
        layout.addRow(self.tr("Red:"), self.network)
        layout.addRow(self.tr("Vídeo:"), self.video)
        layout.addRow(self.tr("Arranque:"), self.boot)

    def isComplete(self) -> bool:
        return True


class VMWizard(QWizard):
    """Asistente de creación o edición; devuelve ``result_config``."""

    def __init__(
        self,
        store: models.VMStore,
        host: HostInfo,
        parent: QWidget | None = None,
        existing: models.VMConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.host = host
        self.existing = existing
        self.existing_id = existing.id if existing else None
        self.result_config: models.VMConfig | None = None

        self.setWindowTitle(
            self.tr("Editar máquina virtual")
            if existing
            else self.tr("Nueva máquina virtual")
        )
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.resize(640, 460)

        self.name_page = NamePage(self)
        self.resources_page = ResourcesPage(self)
        self.storage_page = StoragePage(self)
        self.connection_page = ConnectionPage(self)

        for page in (
            self.name_page,
            self.resources_page,
            self.storage_page,
            self.connection_page,
        ):
            self.addPage(page)

        self.setButtonText(
            QWizard.WizardButton.FinishButton, self.tr("Guardar")
        )
        self.setButtonText(
            QWizard.WizardButton.NextButton, self.tr("Siguiente")
        )
        self.setButtonText(QWizard.WizardButton.BackButton, self.tr("Atrás"))
        self.setButtonText(
            QWizard.WizardButton.CancelButton, self.tr("Cancelar")
        )

        if existing is not None:
            self._load(existing)

    def _load(self, vm: models.VMConfig) -> None:
        ram_max = self.host.effective_ram_max_mb
        cores_max = self.host.effective_cores_max
        self.name_page.name_edit.setText(vm.name)
        self.name_page.iso_edit.setText(vm.iso_path or "")
        self.resources_page.ram_spin.setValue(
            max(models.MIN_RAM_MB, min(vm.ram_mb, ram_max))
        )
        self.resources_page.cpu_spin.setValue(max(1, min(vm.vcpus, cores_max)))
        self.storage_page.disk_spin.setValue(
            max(models.MIN_DISK_GB, min(vm.disk_gb, models.MAX_DISK_GB))
        )
        self._select(self.connection_page.network, vm.network)
        self._select(self.connection_page.video, vm.video)
        self._select(self.connection_page.boot, vm.boot_order)

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def current_name(self) -> str:
        return self.name_page.name_edit.text().strip() or "vm"

    def _build_config(self) -> models.VMConfig:
        base = self.existing.copy_with() if self.existing else models.VMConfig(name="vm")
        iso = self.name_page.iso_edit.text().strip() or None
        name = self.current_name()

        disk_path = base.disk_path
        if not disk_path:
            preview = models.VMConfig(name=name, id=base.id)
            disk_path = str(preview.default_disk_path())

        return base.copy_with(
            name=name,
            ram_mb=self.resources_page.ram_spin.value(),
            vcpus=self.resources_page.cpu_spin.value(),
            disk_gb=self.storage_page.disk_spin.value(),
            disk_path=disk_path,
            iso_path=iso,
            network=str(self.connection_page.network.currentData()),
            video=str(self.connection_page.video.currentData()),
            boot_order=str(self.connection_page.boot.currentData()),
        )

    def accept(self) -> None:
        config = self._build_config()
        errors = config.validate(
            ram_max_mb=self.host.effective_ram_max_mb,
            cores_max=self.host.effective_cores_max,
            existing_names=self.store.names(exclude_id=self.existing_id),
            check_paths=True,
        )
        if errors:
            QMessageBox.warning(
                self,
                self.tr("Configuración no válida"),
                "\n• ".join(errors),
            )
            return
        self.store.save(config)
        self.result_config = config
        super().accept()


# --------------------------------------------------------------------------- #
# Diagnóstico del host
# --------------------------------------------------------------------------- #
class DiagnosticsDialog(QDialog):
    def __init__(
        self,
        host: HostInfo,
        parent: QWidget | None = None,
        *,
        snapshot: dict[str, object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.setWindowTitle(self.tr("Diagnóstico del host"))
        self.resize(720, 520)

        if snapshot is None:
            snapshot = core.environment_snapshot()
        self.report_text = self._format_report(snapshot, host)

        self.text = QPlainTextEdit(self.report_text)
        self.text.setReadOnly(True)

        self.copy_button = QPushButton(self.tr("Copiar"))
        self.copy_button.clicked.connect(self._copy)
        self.copy_status = QLabel()
        self.copy_status.setObjectName("EmptyLabel")
        self.copy_status.setVisible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addWidget(self.copy_status)
        layout.addWidget(buttons)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.report_text)
        self.copy_status.setText(self.tr("Reporte copiado al portapapeles."))
        self.copy_status.setVisible(True)

    def _format_report(
        self, snapshot: dict[str, object], host: HostInfo
    ) -> str:
        lines: list[str] = []
        lines.append(
            self.tr("Aplicación:        {app}").format(
                app=snapshot.get("app", core.APP_NAME)
            )
        )
        lines.append(
            self.tr("Python:            {version}").format(
                version=snapshot.get("python", sys.version.split()[0])
            )
        )
        lines.append(
            self.tr("Plataforma:        {platform}").format(
                platform=snapshot.get("platform", self.tr("desconocida"))
            )
        )
        lines.append("")

        kvm = snapshot.get("kvm")
        if isinstance(kvm, core.KvmStatus):
            state = self.tr("utilizable") if kvm.usable else self.tr("NO utilizable")
            lines.append(
                self.tr(
                    "KVM:               {device} | grupo={group} | "
                    "char={char} r={readable} w={writable} | {state}"
                ).format(
                    device=kvm.device,
                    group=kvm.group or "?",
                    char=self.tr("sí") if kvm.is_char_device else self.tr("no"),
                    readable=self.tr("sí") if kvm.readable else self.tr("no"),
                    writable=self.tr("sí") if kvm.writable else self.tr("no"),
                    state=state,
                )
            )
        else:
            lines.append(self.tr("KVM:               no comprobado"))

        ram_mb = snapshot.get("ram_max_mb")
        if isinstance(ram_mb, int):
            lines.append(
                self.tr("RAM máxima:        {ram} MB ({gib:.1f} GiB)").format(
                    ram=ram_mb, gib=ram_mb / 1024
                )
            )
        else:
            lines.append(self.tr("RAM máxima:        no disponible"))
        cores = snapshot.get("cores_max")
        lines.append(
            self.tr("CPUs lógicas:      {cores}").format(
                cores=cores
                if isinstance(cores, int)
                else self.tr("no disponible")
            )
        )

        bin_dir = snapshot.get("bin_dir")
        if bin_dir:
            state = (
                self.tr("existe")
                if snapshot.get("bin_dir_exists")
                else self.tr("se creará en la primera descarga")
            )
            lines.append(
                self.tr("Directorio bin.:   {path} ({state})").format(
                    path=bin_dir, state=state
                )
            )

        binaries = snapshot.get("binaries")
        if isinstance(binaries, dict):
            for name in core.MANAGED_BINARIES:
                status = binaries.get(name)
                if isinstance(status, core.BinaryStatus):
                    lines.append(
                        self.tr(
                            "{name}: {source} {path} | {version}"
                        ).format(
                            name=name,
                            source=status.source,
                            path=status.path or self.tr("no encontrado"),
                            version=status.version
                            or self.tr("versión desconocida"),
                        )
                    )
                else:
                    lines.append(
                        self.tr("{name}: no comprobado").format(name=name)
                    )
        else:
            lines.append(self.tr("Binarios:          no comprobados"))

        display = snapshot.get("qemu_display")
        if display is None:
            lines.append(
                self.tr("Backends de vídeo: no comprobados (falta qemu-system)")
            )
        elif isinstance(display, core.DisplaySupport):
            if not display.probed:
                lines.append(
                    self.tr(
                        "Backends de vídeo: error al consultar ({error})"
                    ).format(error=display.error)
                )
            else:
                lines.append(
                    self.tr("Backends de vídeo: {backends}").format(
                        backends=", ".join(sorted(display.backends))
                    )
                )
                lines.append(
                    self.tr("Ventana nativa:    {state}").format(
                        state=self.tr("sí (GTK/SDL)")
                        if display.has_window_backend
                        else self.tr("NO (headless)")
                    )
                )
        else:
            lines.append(self.tr("Backends de vídeo: no comprobados"))

        if ram_mb is None and host.ram_error:
            lines.append(
                self.tr("Aviso RAM:         {error}").format(
                    error=host.ram_error
                )
            )
        lines.append("")
        lines.append(
            self.tr(
                "Etapa actual: 4 (lanzamiento con QProcess y ciclo de vida real)."
            )
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Ventana principal
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, host: HostInfo, store: models.VMStore | None = None) -> None:
        super().__init__()
        self.host = host
        self.store = store or models.VMStore()
        self._settings = QSettings("vibecBox", "vibecBox")
        self.runners: dict[str, runner.VMRunner] = {}

        self.setWindowTitle(
            self.tr("vibecBox — vivi is not VirtualBox")
        )
        self.resize(1080, 680)
        self.setMinimumSize(880, 520)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self._build_log_dock()
        self._build_statusbar()

        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        self._reconcile_states()
        self.refresh()

    # -- construcción ------------------------------------------------------ #
    def _build_actions(self) -> None:
        self.act_new = QAction(
            standard_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder),
            self.tr("&Nueva"),
            self,
        )
        self.act_new.setShortcut("Ctrl+N")
        self.act_new.setStatusTip(self.tr("Crear una máquina virtual"))
        self.act_new.triggered.connect(self.new_vm)

        self.act_edit = QAction(
            standard_icon(self, QStyle.StandardPixmap.SP_FileDialogDetailedView),
            self.tr("&Configuración"),
            self,
        )
        self.act_edit.setShortcut("Ctrl+E")
        self.act_edit.setStatusTip(self.tr("Editar la máquina seleccionada"))
        self.act_edit.triggered.connect(self.edit_vm)

        self.act_start = QAction(
            standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay),
            self.tr("&Iniciar"),
            self,
        )
        self.act_start.setShortcut("Ctrl+Return")
        self.act_start.setStatusTip(self.tr("Iniciar la máquina seleccionada"))
        self.act_start.triggered.connect(self.start_vm)

        self.act_stop = QAction(
            standard_icon(self, QStyle.StandardPixmap.SP_MediaStop),
            self.tr("Apa&gar"),
            self,
        )
        self.act_stop.setShortcut("Ctrl+.")
        self.act_stop.setStatusTip(
            self.tr("Pedir un apagado ordenado al invitado")
        )
        self.act_stop.triggered.connect(self.stop_vm)

        self.act_force = QAction(self.tr("Forzar parada"), self)
        self.act_force.setShortcut("Ctrl+Shift+.")
        self.act_force.setStatusTip(
            self.tr("Terminar QEMU inmediatamente (SIGKILL)")
        )
        self.act_force.triggered.connect(self.force_stop_vm)

        self.act_delete = QAction(
            standard_icon(self, QStyle.StandardPixmap.SP_TrashIcon),
            self.tr("E&liminar"),
            self,
        )
        self.act_delete.setShortcut("Ctrl+Delete")
        self.act_delete.setStatusTip(
            self.tr("Eliminar la máquina seleccionada")
        )
        self.act_delete.triggered.connect(self.delete_vm)

        self.act_refresh = QAction(
            standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload),
            self.tr("A&ctualizar"),
            self,
        )
        self.act_refresh.setShortcut("F5")
        self.act_refresh.triggered.connect(self.refresh)

        self.act_diagnostics = QAction(
            self.tr("&Diagnóstico del host"), self
        )
        self.act_diagnostics.triggered.connect(self.show_diagnostics)

        self.act_quit = QAction(self.tr("&Salir"), self)
        self.act_quit.setShortcut("Ctrl+Q")
        self.act_quit.triggered.connect(self.close)

        self.act_about = QAction(self.tr("&Acerca de vibecBox"), self)
        self.act_about.triggered.connect(self.show_about)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu(self.tr("&Archivo"))
        file_menu.addAction(self.act_new)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        machine_menu = bar.addMenu(self.tr("&Máquina"))
        machine_menu.addAction(self.act_edit)
        machine_menu.addAction(self.act_start)
        machine_menu.addSeparator()
        machine_menu.addAction(self.act_stop)
        machine_menu.addAction(self.act_force)
        machine_menu.addSeparator()
        machine_menu.addAction(self.act_delete)

        view_menu = bar.addMenu(self.tr("&Ver"))
        view_menu.addAction(self.act_refresh)
        self.view_menu = view_menu

        help_menu = bar.addMenu(self.tr("A&yuda"))
        help_menu.addAction(self.act_diagnostics)
        help_menu.addSeparator()
        help_menu.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar(self.tr("Principal"), self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addAction(self.act_new)
        toolbar.addAction(self.act_edit)
        toolbar.addSeparator()
        toolbar.addAction(self.act_start)
        toolbar.addAction(self.act_stop)
        toolbar.addAction(self.act_force)
        toolbar.addSeparator()
        toolbar.addAction(self.act_delete)
        self.addToolBar(toolbar)

    def _build_central(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QGroupBox(self.tr("Máquinas virtuales"))
        left_layout = QVBoxLayout(left)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setIconSize(QSize(12, 12))
        self.list.itemSelectionChanged.connect(self._update_actions)
        self.list.itemDoubleClicked.connect(lambda _item: self.edit_vm())

        self.empty = QLabel(
            self.tr(
                "No hay máquinas virtuales.\n\nPulsa «Nueva» para crear la primera."
            )
        )
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("EmptyLabel")
        self.empty.setWordWrap(True)

        self.stack = QWidget()
        stack_layout = QStackedLayout(self.stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.addWidget(self.list)
        stack_layout.addWidget(self.empty)
        self._stack_layout = stack_layout

        left_layout.addWidget(self.stack)
        splitter.addWidget(left)

        right = QGroupBox(self.tr("Detalles"))
        right_layout = QVBoxLayout(right)

        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        details_host = QWidget()
        self.details_form = QFormLayout(details_host)
        self.details_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.details_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self.detail_labels: dict[str, QLabel] = {}
        for key, title in (
            ("name", "Nombre"),
            ("state", "Estado"),
            ("ram", "Memoria"),
            ("vcpus", "Procesadores"),
            ("disk", "Disco"),
            ("disk_path", "Fichero de disco"),
            ("iso", "ISO de instalación"),
            ("network", "Red"),
            ("video", "Vídeo"),
            ("boot", "Arranque"),
            ("created", "Creada"),
            ("id", "Identificador"),
        ):
            value = QLabel("—")
            value.setObjectName("ValueLabel")
            value.setWordWrap(True)
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.detail_labels[key] = value
            self.details_form.addRow(self.tr(title) + ":", value)

        details_scroll.setWidget(details_host)
        right_layout.addWidget(details_scroll, 1)

        buttons = QHBoxLayout()
        self.btn_start = QPushButton(self.tr("Iniciar"))
        self.btn_start.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_start.clicked.connect(self.start_vm)
        self.btn_stop = QPushButton(self.tr("Apagar"))
        self.btn_stop.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaStop))
        self.btn_stop.clicked.connect(self.stop_vm)
        self.btn_force = QPushButton(self.tr("Forzar"))
        self.btn_force.clicked.connect(self.force_stop_vm)
        self.btn_edit = QPushButton(self.tr("Configuración"))
        self.btn_edit.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.btn_edit.clicked.connect(self.edit_vm)
        self.btn_delete = QPushButton(self.tr("Eliminar"))
        self.btn_delete.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_TrashIcon))
        self.btn_delete.clicked.connect(self.delete_vm)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_start)
        buttons.addWidget(self.btn_stop)
        buttons.addWidget(self.btn_force)
        buttons.addWidget(self.btn_edit)
        buttons.addWidget(self.btn_delete)
        right_layout.addLayout(buttons)

        splitter.addWidget(right)
        splitter.setSizes([320, 760])
        self.setCentralWidget(splitter)

    def _build_log_dock(self) -> None:
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.log_view.setObjectName("LogView")
        self.log_view.setPlaceholderText(
            self.tr(
                "Aquí aparecerá la salida de QEMU al iniciar una máquina virtual."
            )
        )

        clear = QPushButton(self.tr("Limpiar"))
        clear.clicked.connect(self.log_view.clear)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.log_view, 1)
        layout.addWidget(clear)

        dock = QDockWidget(self.tr("Salida de QEMU"), self)
        dock.setObjectName("LogDock")
        dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.log_dock = dock
        if getattr(self, "view_menu", None) is not None:
            self.view_menu.addSeparator()
            self.view_menu.addAction(dock.toggleViewAction())

    def _build_statusbar(self) -> None:
        bar = self.statusBar()
        kvm = self.tr("sí") if self.host.kvm_ok else self.tr("NO")
        bar.addWidget(QLabel(self.tr("KVM: {state}").format(state=kvm)))
        bar.addWidget(
            QLabel(
                self.tr("RAM: {ram} MB").format(
                    ram=self.host.effective_ram_max_mb
                )
            )
        )
        bar.addWidget(
            QLabel(
                self.tr("CPUs: {cores}").format(
                    cores=self.host.effective_cores_max
                )
            )
        )

        version = (
            self.host.qemu_version or self.tr("QEMU no encontrado")
        ).replace("QEMU emulator version ", "")
        source = (
            self.tr("PATH")
            if self.host.qemu_path and self.host.qemu_path.startswith("/usr")
            else self.tr("managed")
        )
        qemu_label = QLabel(
            self.tr("QEMU {version} ({source})").format(
                version=version, source=source
            )
        )
        bar.addPermanentWidget(qemu_label)

        if self.host.display is not None and not self.host.display.has_window_backend:
            warning = QLabel(
                self.tr("⚠ Sin GTK/SDL: no se podrá abrir ventana")
            )
            warning.setStyleSheet("color: #c0392b; font-weight: 600;")
            warning.setToolTip(
                self.tr(
                    "El QEMU resuelto es headless. Instala QEMU de la distro "
                    "para poder abrir la ventana de la VM en la Etapa 4."
                )
            )
            bar.addPermanentWidget(warning)

    # -- estado ------------------------------------------------------------ #
    def refresh(self) -> None:
        selected = self.selected_vm()
        selected_id = selected.id if selected else None

        vms = self.store.list()
        self.list.clear()
        for vm in vms:
            item = QListWidgetItem(
                state_icon(vm.state),
                self.tr("{name}   ·   {state}").format(
                    name=vm.name, state=self.tr(vm.state.label)
                ),
            )
            item.setData(Qt.ItemDataRole.UserRole, vm.id)
            item.setToolTip(
                self.tr("{ram} MB · {vcpus} vCPU · {disk} GB").format(
                    ram=vm.ram_mb, vcpus=vm.vcpus, disk=vm.disk_gb
                )
            )
            self.list.addItem(item)

        self._stack_layout.setCurrentWidget(self.list if vms else self.empty)

        if selected_id:
            self._select_id(selected_id)
        elif vms:
            self.list.setCurrentRow(0)

        self._update_actions()
        self.statusBar().showMessage(
            self.tr("{count} máquina(s) virtual(es)").format(count=len(vms)),
            4000,
        )

    def selected_vm(self) -> models.VMConfig | None:
        item = self.list.currentItem()
        if item is None:
            return None
        vm_id = item.data(Qt.ItemDataRole.UserRole)
        return self.store.get(str(vm_id)) if vm_id else None

    def _select_id(self, vm_id: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == vm_id:
                self.list.setCurrentRow(row)
                return

    def _reconcile_states(self) -> None:
        """Al arrancar no hay procesos hijos: ningún estado «vivo» es real."""
        for vm in self.store.list():
            if vm.state != models.VMState.STOPPED:
                LOG.info("Reconciliando %s: %s → stopped", vm.name, vm.state.value)
                try:
                    self.store.save(vm.copy_with(state=models.VMState.STOPPED))
                except (OSError, ValueError) as exc:
                    LOG.warning("No se pudo reconciliar %s: %s", vm.name, exc)

    def _is_running(self, vm_id: str) -> bool:
        active = self.runners.get(vm_id)
        return active is not None and active.is_running

    def _update_actions(self) -> None:
        vm = self.selected_vm()
        present = vm is not None
        running = present and self._is_running(vm.id)

        self.act_start.setEnabled(present and not running)
        self.act_stop.setEnabled(running)
        self.act_force.setEnabled(running)
        self.act_edit.setEnabled(present and not running)
        self.act_delete.setEnabled(present and not running)

        self.btn_start.setEnabled(present and not running)
        self.btn_stop.setEnabled(running)
        self.btn_force.setEnabled(running)
        self.btn_edit.setEnabled(present and not running)
        self.btn_delete.setEnabled(present and not running)

        self._show_details(vm)

    def _show_details(self, vm: models.VMConfig | None) -> None:
        if vm is None:
            for label in self.detail_labels.values():
                label.setText("—")
            return

        network = {
            "user": self.tr("NAT (modo usuario)"),
            "bridge": self.tr("Puente (bridge)"),
            "none": self.tr("Sin red"),
        }.get(vm.network, vm.network)
        boot = {
            "disk": self.tr("Disco duro"),
            "cdrom": self.tr("CD/ISO"),
            "network": self.tr("Red (PXE)"),
        }.get(vm.boot_order, vm.boot_order)

        values = {
            "name": vm.name,
            "state": self.tr(vm.state.label),
            "ram": self.tr("{ram} MB").format(ram=vm.ram_mb),
            "vcpus": self.tr("{vcpus} vCPU").format(vcpus=vm.vcpus),
            "disk": self.tr("{disk} GB (qcow2)").format(disk=vm.disk_gb),
            "disk_path": str(vm.resolved_disk_path()),
            "iso": vm.iso_path or "—",
            "network": network,
            "video": vm.video,
            "boot": boot,
            "created": vm.created_at,
            "id": vm.id,
        }
        for key, value in values.items():
            self.detail_labels[key].setText(value)

    # -- acciones ---------------------------------------------------------- #
    def new_vm(self) -> None:
        wizard = VMWizard(self.store, self.host, self)
        if wizard.exec() == QWizard.DialogCode.Accepted and wizard.result_config:
            self.refresh()
            self._select_id(wizard.result_config.id)

    def edit_vm(self) -> None:
        vm = self.selected_vm()
        if vm is None:
            return
        if self._is_running(vm.id):
            self.statusBar().showMessage(
                self.tr("Apaga la máquina antes de cambiar su configuración."),
                5000,
            )
            return
        wizard = VMWizard(self.store, self.host, self, existing=vm)
        if wizard.exec() == QWizard.DialogCode.Accepted and wizard.result_config:
            self.refresh()
            self._select_id(wizard.result_config.id)

    def delete_vm(self) -> None:
        vm = self.selected_vm()
        if vm is None:
            return
        if self._is_running(vm.id):
            self.statusBar().showMessage(
                self.tr("Apaga la máquina antes de eliminarla."), 5000
            )
            return
        answer = QMessageBox.question(
            self,
            self.tr("Eliminar máquina virtual"),
            self.tr(
                "¿Eliminar «{name}»?\n\nSe borrará su configuración. "
                "El disco qcow2 no se elimina automáticamente."
            ).format(name=vm.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(vm.id)
        self.refresh()

    def start_vm(self) -> None:
        vm = self.selected_vm()
        if vm is None:
            return

        if self._is_running(vm.id):
            return

        if self.host.display is None or not self.host.display.probed:
            QMessageBox.warning(
                self,
                self.tr("No se pudo comprobar el vídeo"),
                self.tr(
                    "No se pudo consultar `-display help` en el QEMU resuelto.\n"
                    "Instala QEMU de la distro y vuelve a intentarlo."
                ),
            )
            return

        if not self.host.display.has_window_backend:
            QMessageBox.warning(
                self,
                self.tr("QEMU sin ventana nativa"),
                self.tr(
                    "El QEMU resuelto no incluye GTK ni SDL, así que no puede "
                    "abrir su propia ventana.\n\n"
                    "Instala QEMU de la distro (con GTK/SDL) o define un binario "
                    "con backend de ventana."
                ),
            )
            return

        backends = self.host.display.backends
        display = "gtk,window-close=on" if "gtk" in backends else "sdl"

        try:
            disk = qemu.create_disk(vm)
        except (qemu.DiskError, core.VibecBoxError) as exc:
            QMessageBox.critical(
                self,
                self.tr("No se pudo preparar el disco"),
                self.tr(
                    "La máquina «{name}» no puede arrancar:\n\n{error}"
                ).format(name=vm.name, error=exc),
            )
            return

        active = runner.VMRunner(vm, self.host, display=display, parent=self)
        active.started.connect(self._on_vm_started)
        active.state_changed.connect(self._on_vm_state)
        active.log_line.connect(self._on_log)
        active.finished.connect(self._on_vm_finished)
        self.runners[vm.id] = active

        self.statusBar().showMessage(
            self.tr("Iniciando «{name}» con {display}…").format(
                name=vm.name, display=display.split(",")[0].upper()
            ),
            5000,
        )
        self.log_view.appendPlainText(
            runner.format_log_line(
                vm.name,
                self.tr("Disco listo: {path}").format(path=disk),
            )
        )
        active.start()
        self._update_actions()

    def stop_vm(self) -> None:
        vm = self.selected_vm()
        if vm is None:
            return
        active = self.runners.get(vm.id)
        if active is None or not active.is_running:
            return
        active.stop(graceful=True)
        self.statusBar().showMessage(
            self.tr("Apagado ordenado solicitado a «{name}»…").format(
                name=vm.name
            ),
            5000,
        )
        self._update_actions()

    def force_stop_vm(self) -> None:
        vm = self.selected_vm()
        if vm is None:
            return
        active = self.runners.get(vm.id)
        if active is None or not active.is_running:
            return
        answer = QMessageBox.question(
            self,
            self.tr("Forzar parada"),
            self.tr(
                "¿Terminar «{name}» inmediatamente?\n\n"
                "Se enviará SIGKILL a QEMU: el invitado no podrá cerrar sus "
                "ficheros de forma ordenada y el disco puede quedar "
                "inconsistente."
            ).format(name=vm.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        active.force_kill()
        self.statusBar().showMessage(
            self.tr("Forzando parada de «{name}»…").format(name=vm.name),
            5000,
        )
        self._update_actions()

    # -- señales del runner ------------------------------------------------ #
    def _on_vm_started(self, vm_id: str) -> None:
        vm = self.store.get(vm_id)
        if vm is not None:
            self.statusBar().showMessage(
                self.tr("«{name}» en ejecución.").format(name=vm.name), 5000
            )
        self.refresh()

    def _on_vm_state(self, vm_id: str, state: models.VMState) -> None:
        vm = self.store.get(vm_id)
        if vm is None:
            return
        if vm.state != state:
            try:
                self.store.save(vm.copy_with(state=state))
            except (OSError, ValueError) as exc:
                LOG.warning("No se pudo guardar el estado de %s: %s", vm.name, exc)
        self.refresh()

    def _on_log(self, vm_id: str, line: str) -> None:
        vm = self.store.get(vm_id)
        name = vm.name if vm is not None else vm_id
        self.log_view.appendPlainText(runner.format_log_line(name, line))

    def _on_vm_finished(
        self, vm_id: str, exit_code: int, state: models.VMState
    ) -> None:
        active = self.runners.pop(vm_id, None)
        if active is not None:
            active.deleteLater()
        vm = self.store.get(vm_id)
        if vm is not None:
            self.statusBar().showMessage(
                self.tr("«{name}» terminó con estado {state}.").format(
                    name=vm.name, state=self.tr(state.label)
                ),
                6000,
            )
        self.refresh()

    def show_diagnostics(self) -> None:
        DiagnosticsDialog(self.host, self).exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            self.tr("Acerca de vibecBox"),
            self.tr(
                "<h3>vibecBox</h3>"
                "<p><i>vivi is not VirtualBox</i></p>"
                "<p>GUI de escritorio que imita a VirtualBox y ejecuta "
                "QEMU/KVM nativo, sin libvirt y sin VNC/SPICE embebido.</p>"
            )
            + self.tr("<p>Qt {qt} · Python {python}</p>").format(
                qt=QT_VERSION_STR, python=sys.version.split()[0]
            ),
        )

    # -- ciclo de vida ----------------------------------------------------- #
    @staticmethod
    def _wait_runners(runners: list[object], timeout_ms: int) -> None:
        """Espera acotada procesando eventos para que QMP pueda responder."""
        deadline = time.monotonic() + timeout_ms / 1000
        pending = [active for active in runners if active.is_running]
        while pending and time.monotonic() < deadline:
            QApplication.processEvents()
            for active in list(pending):
                if not active.is_running:
                    pending.remove(active)
                else:
                    active.wait_for_finished(_WAIT_SLICE_MS)

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self._settings.setValue("window/geometry", self.saveGeometry())
        active_runners = list(self.runners.values())
        if active_runners:
            LOG.info("Cerrando %d VM(s) en ejecución", len(active_runners))
            for active in active_runners:
                if active.is_running:
                    active.stop(graceful=True)
            self._wait_runners(active_runners, CLOSE_GRACEFUL_TIMEOUT_MS)
            for active in active_runners:
                if active.is_running:
                    LOG.warning(
                        "QEMU no se apagó a tiempo al cerrar; enviando SIGKILL."
                    )
                    active.force_kill()
            self._wait_runners(active_runners, CLOSE_KILL_TIMEOUT_MS)
            self.runners.clear()
        super().closeEvent(event)


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #
def _check_qt_version() -> str | None:
    try:
        parts = tuple(int(part) for part in QT_VERSION_STR.split(".")[:2])
    except ValueError:
        return None
    if parts < MIN_QT_VERSION:
        return QCoreApplication.translate(
            "main",
            "Se requiere Qt {required} o superior; encontrado {found}.",
        ).format(
            required=".".join(map(str, MIN_QT_VERSION)),
            found=QT_VERSION_STR,
        )
    return None


def _log_file_path() -> Path:
    """Ruta del log persistente bajo el directorio de datos de la app."""
    return core.data_home() / LOG_FILE_NAME


def _configure_logging(verbose: bool) -> Path | None:
    """Configura consola y log rotado; devuelve la ruta del fichero o ``None``."""
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(
        logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )

    log_path = _log_file_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUPS,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.basicConfig(
            level=logging.DEBUG, handlers=[console], force=True
        )
        LOG.warning("No se pudo abrir el log en fichero: %s", exc)
        return None

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(
        level=logging.DEBUG, handlers=[console, file_handler], force=True
    )
    LOG.info("Registro en %s", log_path)
    return log_path


def _parse_cli_args(
    argv: list[str] | None,
) -> tuple[argparse.Namespace, list[str]]:
    """Separa las opciones de vibecBox de los argumentos que consume Qt."""
    parser = argparse.ArgumentParser(
        prog="vibecBox",
        description="GUI de vibecBox para ejecutar QEMU/KVM nativo.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="muestra también los logs DEBUG en la consola",
    )
    return parser.parse_known_args(sys.argv[1:] if argv is None else argv)


def main(argv: list[str] | None = None) -> int:
    options, qt_args = _parse_cli_args(argv)
    _configure_logging(options.verbose)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("vibecBox")
    app.setApplicationDisplayName("vibecBox")
    app.setStyleSheet(STYLE)

    problem = _check_qt_version()
    if problem:
        QMessageBox.critical(
            None,
            QCoreApplication.translate("main", "Versión de Qt no compatible"),
            problem,
        )
        return 2

    host = load_host_info()
    window = MainWindow(host)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
