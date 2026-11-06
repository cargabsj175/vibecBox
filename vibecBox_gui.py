#!/usr/bin/env python3
"""GUI de vibecBox (PyQt6) — Etapas 2 y 4.

La vista no construye argumentos de QEMU: edita ``VMConfig`` y delega el
lanzamiento en ``qemu.plan_launch`` y ``runner.VMRunner``. La ventana de la VM
la abre QEMU (GTK/SDL); aquí solo se supervisa el proceso y su salida.

Ejecutar:
    .venv/bin/python vibecBox_gui.py
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

try:
    from PyQt6.QtCore import QSettings, QSize, Qt, QT_VERSION_STR
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
        self.setTitle("Nombre e instalación")
        self.setSubTitle("Identifica la máquina y, si quieres, elige una ISO de arranque.")

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("p. ej. Ubuntu 24.04")
        self.name_edit.setMaxLength(models.MAX_NAME_LENGTH)
        self.name_edit.textChanged.connect(self.completeChanged)

        self.iso_edit = QLineEdit()
        self.iso_edit.setPlaceholderText("Sin ISO (arranque desde disco)")
        self.iso_edit.textChanged.connect(self.completeChanged)

        browse = QPushButton("Examinar…")
        browse.clicked.connect(self._pick_iso)
        clear = QPushButton("Quitar")
        clear.clicked.connect(self.iso_edit.clear)

        iso_row = QHBoxLayout()
        iso_row.addWidget(self.iso_edit, 1)
        iso_row.addWidget(browse)
        iso_row.addWidget(clear)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("EmptyLabel")

        layout = QFormLayout(self)
        layout.addRow("Nombre:", self.name_edit)
        layout.addRow("Imagen ISO:", iso_row)
        layout.addRow(self.hint)

    def _pick_iso(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar imagen ISO",
            str(Path.home()),
            "Imágenes ISO (*.iso);;Todos los ficheros (*)",
        )
        if path:
            self.iso_edit.setText(path)

    def initializePage(self) -> None:
        if self.name_edit.text():
            return
        self.name_edit.setText(f"VM {len(self._wizard.store.list()) + 1}")
        self.name_edit.selectAll()

    def _name_error(self) -> str | None:
        name = self.name_edit.text().strip()
        if not name:
            return "Escribe un nombre para la máquina."
        if len(name) > models.MAX_NAME_LENGTH:
            return f"Máximo {models.MAX_NAME_LENGTH} caracteres."
        if any(ch in name for ch in ("/", "\\", "\0")):
            return "El nombre no puede contener '/', '\\' ni caracteres nulos."
        existing = self._wizard.store.names(exclude_id=self._wizard.existing_id)
        if name.lower() in {item.lower() for item in existing}:
            return "Ya existe una VM con ese nombre."
        return None

    def isComplete(self) -> bool:
        error = self._name_error()
        self.hint.setText(error or "La ISO es opcional: también puedes arrancar desde el disco.")
        return error is None

    def validatePage(self) -> bool:
        error = self._name_error()
        if error:
            QMessageBox.warning(self, "Nombre no válido", error)
            return False
        return True


class ResourcesPage(QWizardPage):
    def __init__(self, wizard: "VMWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle("Memoria y procesadores")
        self.setSubTitle("Ajusta los recursos según las capacidades reales del host.")

        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(models.MIN_RAM_MB, wizard.host.ram_max_mb)
        self.ram_spin.setSingleStep(256)
        self.ram_spin.setSuffix(" MB")
        self.ram_spin.setValue(min(2048, wizard.host.ram_max_mb))

        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, wizard.host.cores_max)
        self.cpu_spin.setValue(min(2, wizard.host.cores_max))
        self.cpu_spin.setSuffix(" vCPU")

        self.ram_range = QLabel(
            f"Disponible en el host: {wizard.host.ram_max_mb} MB "
            f"({wizard.host.ram_max_mb / 1024:.1f} GiB)."
        )
        self.cpu_range = QLabel(f"Hilos lógicos del host: {wizard.host.cores_max}.")
        for label in (self.ram_range, self.cpu_range):
            label.setObjectName("EmptyLabel")

        layout = QFormLayout(self)
        layout.addRow("Memoria RAM:", self.ram_spin)
        layout.addRow(self.ram_range)
        layout.addRow("Procesadores:", self.cpu_spin)
        layout.addRow(self.cpu_range)

    def isComplete(self) -> bool:
        return True


class StoragePage(QWizardPage):
    def __init__(self, wizard: "VMWizard") -> None:
        super().__init__()
        self._wizard = wizard
        self.setTitle("Disco virtual")
        self.setSubTitle("Se creará un disco qcow2 la primera vez que arranques la VM.")

        self.disk_spin = QSpinBox()
        self.disk_spin.setRange(models.MIN_DISK_GB, models.MAX_DISK_GB)
        self.disk_spin.setSuffix(" GB")
        self.disk_spin.setValue(20)
        self.disk_spin.valueChanged.connect(self._update_path)

        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setObjectName("ValueLabel")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout = QFormLayout(self)
        layout.addRow("Tamaño:", self.disk_spin)
        layout.addRow("Fichero:", self.path_label)

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
        self.setTitle("Red, vídeo y arranque")
        self.setSubTitle("Valores VirtIO recomendados para el mejor rendimiento.")

        self.network = QComboBox()
        self.network.addItem("NAT (modo usuario)", "user")
        self.network.addItem("Puente (bridge, requiere configuración)", "bridge")
        self.network.addItem("Sin red", "none")

        self.video = QComboBox()
        self.video.addItem("virtio-vga (recomendado)", "virtio-vga")
        self.video.addItem("virtio-gpu", "virtio-gpu")
        self.video.addItem("VGA estándar", "std")

        self.boot = QComboBox()
        self.boot.addItem("Disco duro", "disk")
        self.boot.addItem("CD/ISO", "cdrom")
        self.boot.addItem("Red (PXE)", "network")

        layout = QFormLayout(self)
        layout.addRow("Red:", self.network)
        layout.addRow("Vídeo:", self.video)
        layout.addRow("Arranque:", self.boot)

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

        self.setWindowTitle("Editar máquina virtual" if existing else "Nueva máquina virtual")
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

        self.setButtonText(QWizard.WizardButton.FinishButton, "Guardar")
        self.setButtonText(QWizard.WizardButton.NextButton, "Siguiente")
        self.setButtonText(QWizard.WizardButton.BackButton, "Atrás")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancelar")

        if existing is not None:
            self._load(existing)

    def _load(self, vm: models.VMConfig) -> None:
        self.name_page.name_edit.setText(vm.name)
        self.name_page.iso_edit.setText(vm.iso_path or "")
        self.resources_page.ram_spin.setValue(
            max(models.MIN_RAM_MB, min(vm.ram_mb, self.host.ram_max_mb))
        )
        self.resources_page.cpu_spin.setValue(
            max(1, min(vm.vcpus, self.host.cores_max))
        )
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
            ram_max_mb=self.host.ram_max_mb,
            cores_max=self.host.cores_max,
            existing_names=self.store.names(exclude_id=self.existing_id),
            check_paths=True,
        )
        if errors:
            QMessageBox.warning(self, "Configuración no válida", "\n• ".join(errors))
            return
        self.store.save(config)
        self.result_config = config
        super().accept()


# --------------------------------------------------------------------------- #
# Diagnóstico del host
# --------------------------------------------------------------------------- #
class DiagnosticsDialog(QDialog):
    def __init__(self, host: HostInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Diagnóstico del host")
        self.resize(720, 520)

        lines = [
            f"Python:            {sys.version.split()[0]}",
            f"KVM utilizable:    {'sí' if host.kvm_ok else 'NO'}",
            f"RAM máxima:        {host.ram_max_mb} MB",
            f"CPUs lógicas:      {host.cores_max}",
            f"qemu-system:       {host.qemu_path or 'no encontrado'}",
            f"QEMU versión:      {host.qemu_version or 'desconocida'}",
            f"qemu-img versión:  {host.img_version or 'desconocida'}",
        ]
        if host.display is None:
            lines.append("Backends de vídeo: no comprobados (falta qemu-system)")
        elif not host.display.probed:
            lines.append(f"Backends de vídeo: error al consultar ({host.display.error})")
        else:
            lines.append(
                "Backends de vídeo: " + ", ".join(sorted(host.display.backends))
            )
            lines.append(
                "Ventana nativa:    "
                + ("sí (GTK/SDL)" if host.display.has_window_backend else "NO (headless)")
            )
        if host.ram_error:
            lines.append(f"Aviso RAM:         {host.ram_error}")
        lines.append("")
        lines.append("Etapa actual: 4 (lanzamiento con QProcess y ciclo de vida real).")

        text = QPlainTextEdit("\n".join(lines))
        text.setReadOnly(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)


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

        self.setWindowTitle("vibecBox — vivi is not VirtualBox")
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
        self.act_new = QAction(standard_icon(self, QStyle.StandardPixmap.SP_FileDialogNewFolder), "&Nueva", self)
        self.act_new.setShortcut("Ctrl+N")
        self.act_new.setStatusTip("Crear una máquina virtual")
        self.act_new.triggered.connect(self.new_vm)

        self.act_edit = QAction(standard_icon(self, QStyle.StandardPixmap.SP_FileDialogDetailedView), "&Configuración", self)
        self.act_edit.setShortcut("Ctrl+E")
        self.act_edit.setStatusTip("Editar la máquina seleccionada")
        self.act_edit.triggered.connect(self.edit_vm)

        self.act_start = QAction(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay), "&Iniciar", self)
        self.act_start.setShortcut("Ctrl+Return")
        self.act_start.setStatusTip("Iniciar la máquina seleccionada")
        self.act_start.triggered.connect(self.start_vm)

        self.act_stop = QAction(standard_icon(self, QStyle.StandardPixmap.SP_MediaStop), "Apa&gar", self)
        self.act_stop.setShortcut("Ctrl+.")
        self.act_stop.setStatusTip("Pedir un apagado ordenado al invitado")
        self.act_stop.triggered.connect(self.stop_vm)

        self.act_force = QAction("Forzar parada", self)
        self.act_force.setShortcut("Ctrl+Shift+.")
        self.act_force.setStatusTip("Terminar QEMU inmediatamente (SIGKILL)")
        self.act_force.triggered.connect(self.force_stop_vm)

        self.act_delete = QAction(standard_icon(self, QStyle.StandardPixmap.SP_TrashIcon), "E&liminar", self)
        self.act_delete.setShortcut("Ctrl+Delete")
        self.act_delete.setStatusTip("Eliminar la máquina seleccionada")
        self.act_delete.triggered.connect(self.delete_vm)

        self.act_refresh = QAction(standard_icon(self, QStyle.StandardPixmap.SP_BrowserReload), "A&ctualizar", self)
        self.act_refresh.setShortcut("F5")
        self.act_refresh.triggered.connect(self.refresh)

        self.act_diagnostics = QAction("&Diagnóstico del host", self)
        self.act_diagnostics.triggered.connect(self.show_diagnostics)

        self.act_quit = QAction("&Salir", self)
        self.act_quit.setShortcut("Ctrl+Q")
        self.act_quit.triggered.connect(self.close)

        self.act_about = QAction("&Acerca de vibecBox", self)
        self.act_about.triggered.connect(self.show_about)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&Archivo")
        file_menu.addAction(self.act_new)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        machine_menu = bar.addMenu("&Máquina")
        machine_menu.addAction(self.act_edit)
        machine_menu.addAction(self.act_start)
        machine_menu.addSeparator()
        machine_menu.addAction(self.act_stop)
        machine_menu.addAction(self.act_force)
        machine_menu.addSeparator()
        machine_menu.addAction(self.act_delete)

        view_menu = bar.addMenu("&Ver")
        view_menu.addAction(self.act_refresh)
        self.view_menu = view_menu

        help_menu = bar.addMenu("A&yuda")
        help_menu.addAction(self.act_diagnostics)
        help_menu.addSeparator()
        help_menu.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Principal", self)
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

        left = QGroupBox("Máquinas virtuales")
        left_layout = QVBoxLayout(left)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setIconSize(QSize(12, 12))
        self.list.itemSelectionChanged.connect(self._update_actions)
        self.list.itemDoubleClicked.connect(lambda _item: self.edit_vm())

        self.empty = QLabel(
            "No hay máquinas virtuales.\n\nPulsa «Nueva» para crear la primera."
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

        right = QGroupBox("Detalles")
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
            self.details_form.addRow(f"{title}:", value)

        details_scroll.setWidget(details_host)
        right_layout.addWidget(details_scroll, 1)

        buttons = QHBoxLayout()
        self.btn_start = QPushButton("Iniciar")
        self.btn_start.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_start.clicked.connect(self.start_vm)
        self.btn_stop = QPushButton("Apagar")
        self.btn_stop.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_MediaStop))
        self.btn_stop.clicked.connect(self.stop_vm)
        self.btn_force = QPushButton("Forzar")
        self.btn_force.clicked.connect(self.force_stop_vm)
        self.btn_edit = QPushButton("Configuración")
        self.btn_edit.setIcon(standard_icon(self, QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.btn_edit.clicked.connect(self.edit_vm)
        self.btn_delete = QPushButton("Eliminar")
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
            "Aquí aparecerá la salida de QEMU al iniciar una máquina virtual."
        )

        clear = QPushButton("Limpiar")
        clear.clicked.connect(self.log_view.clear)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.log_view, 1)
        layout.addWidget(clear)

        dock = QDockWidget("Salida de QEMU", self)
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
        kvm = "sí" if self.host.kvm_ok else "NO"
        bar.addWidget(QLabel(f"KVM: {kvm}"))
        bar.addWidget(QLabel(f"RAM: {self.host.ram_max_mb} MB"))
        bar.addWidget(QLabel(f"CPUs: {self.host.cores_max}"))

        version = (self.host.qemu_version or "QEMU no encontrado").replace(
            "QEMU emulator version ", ""
        )
        source = "PATH" if self.host.qemu_path and self.host.qemu_path.startswith("/usr") else "managed"
        qemu_label = QLabel(f"QEMU {version} ({source})")
        bar.addPermanentWidget(qemu_label)

        if self.host.display is not None and not self.host.display.has_window_backend:
            warning = QLabel("⚠ Sin GTK/SDL: no se podrá abrir ventana")
            warning.setStyleSheet("color: #c0392b; font-weight: 600;")
            warning.setToolTip(
                "El QEMU resuelto es headless. Instala QEMU de la distro para "
                "poder abrir la ventana de la VM en la Etapa 4."
            )
            bar.addPermanentWidget(warning)

    # -- estado ------------------------------------------------------------ #
    def refresh(self) -> None:
        selected = self.selected_vm()
        selected_id = selected.id if selected else None

        vms = self.store.list()
        self.list.clear()
        for vm in vms:
            item = QListWidgetItem(state_icon(vm.state), f"{vm.name}   ·   {vm.state.label}")
            item.setData(Qt.ItemDataRole.UserRole, vm.id)
            item.setToolTip(f"{vm.ram_mb} MB · {vm.vcpus} vCPU · {vm.disk_gb} GB")
            self.list.addItem(item)

        self._stack_layout.setCurrentWidget(self.list if vms else self.empty)

        if selected_id:
            self._select_id(selected_id)
        elif vms:
            self.list.setCurrentRow(0)

        self._update_actions()
        self.statusBar().showMessage(f"{len(vms)} máquina(s) virtual(es)", 4000)

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
            "user": "NAT (modo usuario)",
            "bridge": "Puente (bridge)",
            "none": "Sin red",
        }.get(vm.network, vm.network)
        boot = {"disk": "Disco duro", "cdrom": "CD/ISO", "network": "Red (PXE)"}.get(
            vm.boot_order, vm.boot_order
        )

        values = {
            "name": vm.name,
            "state": vm.state.label,
            "ram": f"{vm.ram_mb} MB",
            "vcpus": f"{vm.vcpus} vCPU",
            "disk": f"{vm.disk_gb} GB (qcow2)",
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
                "Apaga la máquina antes de cambiar su configuración.", 5000
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
                "Apaga la máquina antes de eliminarla.", 5000
            )
            return
        answer = QMessageBox.question(
            self,
            "Eliminar máquina virtual",
            f"¿Eliminar «{vm.name}»?\n\nSe borrará su configuración. "
            "El disco qcow2 no se elimina automáticamente.",
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
                "No se pudo comprobar el vídeo",
                "No se pudo consultar `-display help` en el QEMU resuelto.\n"
                "Instala QEMU de la distro y vuelve a intentarlo.",
            )
            return

        if not self.host.display.has_window_backend:
            QMessageBox.warning(
                self,
                "QEMU sin ventana nativa",
                "El QEMU resuelto no incluye GTK ni SDL, así que no puede abrir "
                "su propia ventana.\n\n"
                "Instala QEMU de la distro (con GTK/SDL) o define un binario "
                "con backend de ventana.",
            )
            return

        backends = self.host.display.backends
        display = "gtk,window-close=on" if "gtk" in backends else "sdl"

        try:
            disk = qemu.create_disk(vm)
        except (qemu.DiskError, core.VibecBoxError) as exc:
            QMessageBox.critical(
                self,
                "No se pudo preparar el disco",
                f"La máquina «{vm.name}» no puede arrancar:\n\n{exc}",
            )
            return

        active = runner.VMRunner(vm, self.host, display=display, parent=self)
        active.started.connect(self._on_vm_started)
        active.state_changed.connect(self._on_vm_state)
        active.log_line.connect(self._on_log)
        active.finished.connect(self._on_vm_finished)
        self.runners[vm.id] = active

        self.statusBar().showMessage(
            f"Iniciando «{vm.name}» con {display.split(',')[0].upper()}…", 5000
        )
        self.log_view.appendPlainText(
            runner.format_log_line(vm.name, f"Disco listo: {disk}")
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
            f"Apagado ordenado solicitado a «{vm.name}»…", 5000
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
            "Forzar parada",
            f"¿Terminar «{vm.name}» inmediatamente?\n\n"
            "Se enviará SIGKILL a QEMU: el invitado no podrá cerrar sus "
            "ficheros de forma ordenada y el disco puede quedar inconsistente.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        active.force_kill()
        self.statusBar().showMessage(f"Forzando parada de «{vm.name}»…", 5000)
        self._update_actions()

    # -- señales del runner ------------------------------------------------ #
    def _on_vm_started(self, vm_id: str) -> None:
        vm = self.store.get(vm_id)
        if vm is not None:
            self.statusBar().showMessage(f"«{vm.name}» en ejecución.", 5000)
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
                f"«{vm.name}» terminó con estado {state.label}.", 6000
            )
        self.refresh()

    def show_diagnostics(self) -> None:
        DiagnosticsDialog(self.host, self).exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "Acerca de vibecBox",
            "<h3>vibecBox</h3>"
            "<p><i>vivi is not VirtualBox</i></p>"
            "<p>GUI de escritorio que imita a VirtualBox y ejecuta QEMU/KVM "
            "nativo, sin libvirt y sin VNC/SPICE embebido.</p>"
            f"<p>Qt {QT_VERSION_STR} · Python {sys.version.split()[0]}</p>",
        )

    # -- ciclo de vida ----------------------------------------------------- #
    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self._settings.setValue("window/geometry", self.saveGeometry())
        if self.runners:
            LOG.info("Cerrando %d VM(s) en ejecución", len(self.runners))
            for active in list(self.runners.values()):
                active.force_kill()
            for active in list(self.runners.values()):
                active.wait_for_finished(3000)
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
        return (
            f"Se requiere Qt {'.'.join(map(str, MIN_QT_VERSION))} o superior; "
            f"encontrado {QT_VERSION_STR}."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("vibecBox")
    app.setApplicationDisplayName("vibecBox")
    app.setStyleSheet(STYLE)

    problem = _check_qt_version()
    if problem:
        QMessageBox.critical(None, "Versión de Qt no compatible", problem)
        return 2

    host = load_host_info()
    window = MainWindow(host)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
