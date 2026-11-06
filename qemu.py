#!/usr/bin/env python3
"""Construcción de la línea de QEMU y gestión de discos (Etapa 3).

Este módulo es backend puro: no importa PyQt6, no abre ventanas y no lanza la
VM. Solo:

  * traduce ``VMConfig`` a argumentos de ``qemu-system-x86_64`` (``BuildPlan``);
  * crea y describe discos qcow2 con ``qemu-img``.

El lanzamiento real con ``QProcess`` pertenece a la Etapa 4.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import models
import vibecBox_core as core

LOG = logging.getLogger(__name__)

DISK_TIMEOUT_S: Final[float] = 120.0
QCOW2_MAGIC: Final[bytes] = b"QFI\xfb"
BOOT_CODES: Final[dict[str, str]] = {"disk": "c", "cdrom": "d", "network": "n"}

# Nombres reales de dispositivo por modelo lógico del modelo de datos.
# (qxl no se ofrece: este QEMU no lo compila; se comprueba con -device help.)
VIDEO_DEVICES: Final[dict[str, str]] = {
    "virtio-vga": "virtio-vga",
    "virtio-gpu": "virtio-gpu",
    "std": "VGA",
}

NETWORK_BRIDGE: Final[str] = os.environ.get("VIBECBOX_BRIDGE", "br0").strip() or "br0"


class QemuConfigError(core.VibecBoxError):
    """La configuración de la VM no se puede traducir a argumentos válidos."""


class DiskError(core.VibecBoxError):
    """Fallo al crear o inspeccionar un disco con qemu-img."""


@dataclass(frozen=True)
class BuildPlan:
    """Plan de arranque reproducible y auditable, sin ejecutarlo."""

    program: str
    args: tuple[str, ...]
    accel: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    data_dir: str | None = None

    def command(self) -> list[str]:
        return [self.program, *self.args]

    def preview(self) -> str:
        """Línea lista para copiar/pegar, con quoting seguro."""
        return shlex.join(self.command())


# --------------------------------------------------------------------------- #
# Datos de runtime (BIOS/keymaps)
# --------------------------------------------------------------------------- #
def find_data_dir(
    qemu_path: str, *, explicit: str | Path | None = None
) -> Path | None:
    """Localiza ``share/qemu``; ``None`` si QEMU debe usar su prefijo compilado."""
    if explicit:
        return Path(explicit).expanduser()

    env = os.environ.get("VIBECBOX_QEMU_DATA", "").strip()
    if env:
        return Path(env).expanduser()

    try:
        program = Path(qemu_path).resolve()
    except OSError:
        return None

    candidates = [
        program.parent.parent / "share" / "qemu",  # layout gestionado (bin/../share)
        Path("/usr/share/qemu"),
        Path("/usr/local/share/qemu"),
    ]
    for candidate in candidates:
        if (candidate / "bios.bin").is_file() or (candidate / "bios-256k.bin").is_file():
            return candidate
    return None


def _is_managed(program: str) -> bool:
    try:
        return Path(program).resolve().is_relative_to(core.bin_dir().resolve())
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Argumentos
# --------------------------------------------------------------------------- #
def _escape(value: object) -> str:
    """Escapa comas para subopciones de QEMU (``file=a,,b``)."""
    return str(value).replace(",", ",,")


def plan_launch(
    vm: models.VMConfig,
    *,
    qemu_path: str | None = None,
    kvm_available: bool | None = None,
    display: str | None = None,
    data_dir: str | Path | None = None,
    check_disk: bool = True,
    usb_tablet: bool = True,
    qmp_socket: str | Path | None = None,
) -> BuildPlan:
    """Construye el plan de arranque con VirtIO y KVM (o TCG avisado)."""
    if not vm.name.strip():
        raise QemuConfigError("La VM necesita un nombre.")
    if vm.ram_mb <= 0:
        raise QemuConfigError("La RAM debe ser mayor que cero.")
    if vm.vcpus <= 0:
        raise QemuConfigError("Las vCPUs deben ser mayores que cero.")
    if vm.disk_gb <= 0:
        raise QemuConfigError("El disco debe ser mayor que cero.")
    if vm.boot_order not in BOOT_CODES:
        raise QemuConfigError(f"Orden de arranque inválido: {vm.boot_order!r}")
    if vm.video not in VIDEO_DEVICES:
        raise QemuConfigError(f"Modelo de vídeo no soportado: {vm.video!r}")

    program = qemu_path or core.require_binary(core.QEMU_SYSTEM)
    kvm = core.check_kvm() if kvm_available is None else kvm_available
    warnings: list[str] = []

    accel = "kvm" if kvm else "tcg"
    args: list[str] = [
        "-name",
        vm.name,
        "-machine",
        "q35",
        "-accel",
        accel,
    ]

    if kvm:
        args += ["-cpu", "host"]
    else:
        args += ["-cpu", "max"]
        warnings.append(
            "KVM no disponible: se usará TCG (emulación por software, mucho más lenta)."
        )

    args += ["-smp", str(vm.vcpus), "-m", f"{vm.ram_mb}M"]

    # Disco principal VirtIO.
    disk = vm.resolved_disk_path()
    if check_disk and not disk.is_file():
        warnings.append(
            f"El disco {disk} no existe todavía; créalo con create_disk()."
        )
    args += [
        "-drive",
        f"file={_escape(disk)},if=none,id=disk0,format=qcow2,cache=writeback,discard=unmap",
        "-device",
        "virtio-blk-pci,drive=disk0",
    ]

    # ISO en un CD SCSI, que sí existe en q35 (no hay bus IDE).
    if vm.iso_path:
        iso = Path(vm.iso_path).expanduser()
        if check_disk and not iso.is_file():
            warnings.append(f"La ISO {iso} no existe.")
        args += [
            "-drive",
            f"file={_escape(iso)},if=none,id=cd0,media=cdrom,readonly=on,format=raw",
            "-device",
            "virtio-scsi-pci,id=scsi0",
            "-device",
            "scsi-cd,drive=cd0,bus=scsi0.0",
        ]

    args += ["-boot", f"order={BOOT_CODES[vm.boot_order]},menu=on"]

    # Red.
    if vm.network == "user":
        args += ["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"]
    elif vm.network == "bridge":
        args += [
            "-netdev",
            f"bridge,id=net0,br={NETWORK_BRIDGE}",
            "-device",
            "virtio-net-pci,netdev=net0",
        ]
        warnings.append(
            f"Red puente sobre «{NETWORK_BRIDGE}»: requiere qemu-bridge-helper "
            "con permisos y el puente ya creado en el host."
        )

    args += ["-device", VIDEO_DEVICES[vm.video]]

    # Entropía y ratón absoluto: mejoras de UX sin privilegios.
    args += [
        "-object",
        "rng-random,filename=/dev/urandom,id=rng0",
        "-device",
        "virtio-rng-pci,rng=rng0",
    ]
    if usb_tablet:
        args += ["-device", "qemu-xhci", "-device", "usb-tablet"]

    # Monitor desactivado (no hay consola interactiva) y QMP opcional para
    # solicitar un apagado ordenado del invitado.
    args += ["-monitor", "none"]
    if qmp_socket is not None:
        args += [
            "-qmp",
            f"unix:{_escape(qmp_socket)},server=on,wait=off",
        ]

    if display:
        args += ["-display", display]

    # Datos de QEMU: solo se fuerza -L si el binario es gestionado o se indica.
    managed = _is_managed(program)
    resolved_data = find_data_dir(program, explicit=data_dir)
    if resolved_data is not None and (data_dir is not None or managed):
        args += ["-L", str(resolved_data)]
    elif managed:
        warnings.append(
            "Faltan los datos de runtime de QEMU (BIOS/keymaps) junto al binario "
            "gestionado; descarga share/qemu o define VIBECBOX_QEMU_DATA."
        )

    return BuildPlan(
        program=program,
        args=tuple(args),
        accel=accel,
        warnings=tuple(warnings),
        data_dir=str(resolved_data) if resolved_data else None,
    )


def build_args(vm: models.VMConfig, **kwargs: object) -> list[str]:
    """Atajo compatible: devuelve solo los argumentos (sin el programa)."""
    return list(plan_launch(vm, **kwargs).args)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Discos qcow2
# --------------------------------------------------------------------------- #
def _resolve_qemu_img(qemu_img: str | None) -> str:
    return qemu_img or core.require_binary(core.QEMU_IMG)


def create_disk(
    vm: models.VMConfig,
    *,
    qemu_img: str | None = None,
    force: bool = False,
    timeout: float = DISK_TIMEOUT_S,
) -> Path:
    """Crea el disco qcow2 de la VM si no existe. Idempotente por defecto."""
    if vm.disk_gb <= 0:
        raise DiskError("El tamaño de disco debe ser mayor que cero.")

    tool = _resolve_qemu_img(qemu_img)
    destination = vm.resolved_disk_path()

    if destination.exists() and destination.stat().st_size > 0 and not force:
        LOG.debug("Disco ya existente, no se recrea: %s", destination)
        return destination

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DiskError(f"No se pudo crear {destination.parent}: {exc}") from exc

    created = destination.with_name(f".{destination.name}.part")
    created.unlink(missing_ok=True)

    command = [
        tool,
        "create",
        "-f",
        "qcow2",
        "-o",
        "preallocation=metadata",
        str(created),
        f"{vm.disk_gb}G",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiskError(f"No se pudo ejecutar qemu-img: {exc}") from exc

    if completed.returncode != 0:
        created.unlink(missing_ok=True)
        detail = (completed.stderr or completed.stdout or "").strip()
        raise DiskError(f"qemu-img falló ({completed.returncode}): {detail}")

    try:
        with created.open("rb") as handle:
            if handle.read(4) != QCOW2_MAGIC:
                raise DiskError("qemu-img no generó un qcow2 válido.")
        os.replace(created, destination)
    except DiskError:
        created.unlink(missing_ok=True)
        raise
    except OSError as exc:
        created.unlink(missing_ok=True)
        raise DiskError(f"No se pudo instalar {destination}: {exc}") from exc

    LOG.info("Disco creado: %s (%s GB)", destination, vm.disk_gb)
    return destination


def disk_info(path: str | Path, *, qemu_img: str | None = None) -> dict[str, object]:
    """Metadatos del disco vía ``qemu-img info --output=json``."""
    tool = _resolve_qemu_img(qemu_img)
    target = Path(path).expanduser()
    if not target.is_file():
        raise DiskError(f"El disco no existe: {target}")

    try:
        completed = subprocess.run(
            [tool, "info", "--output=json", str(target)],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiskError(f"No se pudo ejecutar qemu-img info: {exc}") from exc

    if completed.returncode != 0:
        raise DiskError(
            f"qemu-img info falló: {(completed.stderr or completed.stdout).strip()}"
        )
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise DiskError("qemu-img info devolvió JSON inválido.") from exc
    if not isinstance(payload, dict):
        raise DiskError("qemu-img info devolvió un tipo inesperado.")
    return payload


def ensure_disk(vm: models.VMConfig, **kwargs: object) -> Path:
    """Alias explícito para el flujo de arranque de la Etapa 4."""
    return create_disk(vm, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "BOOT_CODES",
    "BuildPlan",
    "DISK_TIMEOUT_S",
    "DiskError",
    "QCOW2_MAGIC",
    "QemuConfigError",
    "VIDEO_DEVICES",
    "build_args",
    "create_disk",
    "disk_info",
    "ensure_disk",
    "find_data_dir",
    "plan_launch",
]
