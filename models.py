#!/usr/bin/env python3
"""Modelo de datos de vibecBox (Python puro, sin PyQt6).

Este módulo es parte del backend: define ``VMConfig``, su validación contra las
capacidades del host y la persistencia JSON en ``~/.local/share/vibecBox/vms``.
No importa frameworks de GUI ni imprime por stdout.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final, Iterable

import vibecBox_core as core

LOG = logging.getLogger(__name__)

MIN_RAM_MB: Final[int] = 256
MIN_DISK_GB: Final[int] = 1
MAX_DISK_GB: Final[int] = 4096
MAX_NAME_LENGTH: Final[int] = 64

NETWORK_MODES: Final[tuple[str, ...]] = ("user", "bridge", "none")
VIDEO_MODELS: Final[tuple[str, ...]] = ("virtio-vga", "virtio-gpu", "std")
BOOT_ORDERS: Final[tuple[str, ...]] = ("disk", "cdrom", "network")

_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9._-]+")
_NAME_FORBIDDEN: Final[frozenset[str]] = frozenset({"/", "\\", "\0"})


class VMValidationError(ValueError):
    """La configuración de la VM no cumple las reglas del proyecto."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors: tuple[str, ...] = tuple(errors)
        super().__init__("; ".join(self.errors))


class VMState(str, Enum):
    """Estados visibles de una VM. El lanzamiento real llega en la Etapa 4."""

    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            VMState.STOPPED: "Apagada",
            VMState.RUNNING: "En ejecución",
            VMState.PAUSED: "Pausada",
            VMState.ERROR: "Error",
        }[self]


def slugify(name: str) -> str:
    """Nombre seguro para ficheros (sin rutas ni caracteres raros)."""
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:48] or "vm"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class VMConfig:
    """Definición declarativa de una VM. No conoce QEMU ni Qt."""

    name: str
    ram_mb: int = 2048
    vcpus: int = 2
    disk_gb: int = 20
    disk_path: str = ""
    iso_path: str | None = None
    network: str = "user"
    video: str = "virtio-vga"
    boot_order: str = "disk"
    notes: str = ""
    state: VMState = VMState.STOPPED
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_now)

    # -- derivados --------------------------------------------------------- #
    @property
    def slug(self) -> str:
        return slugify(self.name)

    def default_disk_path(self, root: Path | None = None) -> Path:
        base = root if root is not None else core.data_home() / "disks"
        return base / f"{self.slug}-{self.id[:8]}.qcow2"

    def resolved_disk_path(self) -> Path:
        return Path(self.disk_path).expanduser() if self.disk_path else self.default_disk_path()

    # -- validación -------------------------------------------------------- #
    def validate(
        self,
        *,
        ram_max_mb: int,
        cores_max: int,
        existing_names: Iterable[str] = (),
        check_paths: bool = False,
    ) -> list[str]:
        """Devuelve la lista de errores; vacía si la configuración es válida."""
        errors: list[str] = []
        name = (self.name or "").strip()

        if not name:
            errors.append("El nombre no puede estar vacío.")
        elif len(name) > MAX_NAME_LENGTH:
            errors.append(f"El nombre no puede superar {MAX_NAME_LENGTH} caracteres.")
        elif any(ch in name for ch in _NAME_FORBIDDEN):
            errors.append("El nombre no puede contener '/', '\\' ni caracteres nulos.")
        elif name.lower() in {other.strip().lower() for other in existing_names}:
            errors.append(f"Ya existe una VM llamada «{name}».")

        if not isinstance(self.ram_mb, int):
            errors.append("La RAM debe ser un número entero de MB.")
        elif self.ram_mb < MIN_RAM_MB:
            errors.append(f"La RAM mínima es {MIN_RAM_MB} MB.")
        elif self.ram_mb > ram_max_mb:
            errors.append(f"La RAM no puede superar los {ram_max_mb} MB del host.")

        if not isinstance(self.vcpus, int):
            errors.append("Las vCPUs deben ser un número entero.")
        elif not 1 <= self.vcpus <= cores_max:
            errors.append(f"Las vCPUs deben estar entre 1 y {cores_max}.")

        if not isinstance(self.disk_gb, int):
            errors.append("El tamaño de disco debe ser un entero en GB.")
        elif not MIN_DISK_GB <= self.disk_gb <= MAX_DISK_GB:
            errors.append(
                f"El disco debe estar entre {MIN_DISK_GB} y {MAX_DISK_GB} GB."
            )

        if self.network not in NETWORK_MODES:
            errors.append(f"Red inválida: {self.network!r}.")
        if self.video not in VIDEO_MODELS:
            errors.append(f"Vídeo inválido: {self.video!r}.")
        if self.boot_order not in BOOT_ORDERS:
            errors.append(f"Orden de arranque inválido: {self.boot_order!r}.")

        if self.iso_path:
            iso = Path(self.iso_path).expanduser()
            if not iso.exists():
                errors.append(f"La ISO no existe: {iso}")
            elif not iso.is_file():
                errors.append(f"La ISO no es un fichero: {iso}")
            elif check_paths and iso.suffix.lower() != ".iso":
                errors.append("El fichero de instalación debería tener extensión .iso.")

        return errors

    def assert_valid(
        self,
        *,
        ram_max_mb: int,
        cores_max: int,
        existing_names: Iterable[str] = (),
        check_paths: bool = False,
    ) -> None:
        errors = self.validate(
            ram_max_mb=ram_max_mb,
            cores_max=cores_max,
            existing_names=existing_names,
            check_paths=check_paths,
        )
        if errors:
            raise VMValidationError(errors)

    # -- serialización ----------------------------------------------------- #
    def to_dict(self) -> dict[str, object]:
        return {
            "schema": 1,
            "id": self.id,
            "name": self.name,
            "ram_mb": self.ram_mb,
            "vcpus": self.vcpus,
            "disk_gb": self.disk_gb,
            "disk_path": self.disk_path,
            "iso_path": self.iso_path,
            "network": self.network,
            "video": self.video,
            "boot_order": self.boot_order,
            "notes": self.notes,
            "state": self.state.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "VMConfig":
        if not isinstance(data, dict):
            raise VMValidationError(["El fichero de VM no contiene un objeto JSON."])

        try:
            state = VMState(str(data.get("state", VMState.STOPPED.value)))
        except ValueError:
            state = VMState.STOPPED

        disk_path = str(data.get("disk_path") or "")
        iso_value = data.get("iso_path")
        iso_path = str(iso_value).strip() if iso_value else None

        return cls(
            name=str(data.get("name") or ""),
            ram_mb=int(data.get("ram_mb", 2048)),
            vcpus=int(data.get("vcpus", 2)),
            disk_gb=int(data.get("disk_gb", 20)),
            disk_path=disk_path,
            iso_path=iso_path,
            network=str(data.get("network", "user")),
            video=str(data.get("video", "virtio-vga")),
            boot_order=str(data.get("boot_order", "disk")),
            notes=str(data.get("notes") or ""),
            state=state,
            id=str(data.get("id") or uuid.uuid4().hex),
            created_at=str(data.get("created_at") or _now()),
        )

    def copy_with(self, **changes: object) -> "VMConfig":
        return replace(self, **changes)


class VMStore:
    """Persistencia de una VM por fichero JSON, con escritura atómica."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser() if root else core.data_home() / "vms"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, vm: VMConfig) -> Path:
        return self.root / f"{vm.id}.json"

    # -- lectura ----------------------------------------------------------- #
    def list(self) -> list[VMConfig]:
        if not self.root.is_dir():
            return []
        vms: list[VMConfig] = []
        for entry in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
                vms.append(VMConfig.from_dict(data))
            except (OSError, ValueError, TypeError) as exc:
                LOG.warning("VM ilegible %s: %s", entry, exc)
        vms.sort(key=lambda vm: (vm.created_at, vm.name.lower()))
        return vms

    def get(self, vm_id: str) -> VMConfig | None:
        path = self.root / f"{vm_id}.json"
        try:
            return VMConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    # -- escritura --------------------------------------------------------- #
    def save(self, vm: VMConfig) -> Path:
        self.ensure()
        destination = self.path_for(vm)
        payload = json.dumps(vm.to_dict(), indent=2, ensure_ascii=False) + "\n"

        fd, temp_name = tempfile.mkstemp(prefix=".vm-", suffix=".json", dir=self.root)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)
        return destination

    def delete(self, vm_id: str) -> None:
        (self.root / f"{vm_id}.json").unlink(missing_ok=True)

    def names(self, *, exclude_id: str | None = None) -> list[str]:
        return [
            vm.name
            for vm in self.list()
            if exclude_id is None or vm.id != exclude_id
        ]


__all__ = [
    "BOOT_ORDERS",
    "MAX_DISK_GB",
    "MAX_NAME_LENGTH",
    "MIN_DISK_GB",
    "MIN_RAM_MB",
    "NETWORK_MODES",
    "VIDEO_MODELS",
    "VMConfig",
    "VMState",
    "VMStore",
    "VMValidationError",
    "slugify",
]
