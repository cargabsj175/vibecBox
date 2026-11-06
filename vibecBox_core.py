#!/usr/bin/env python3
"""Backend de descubrimiento y capacidades del host para vibecBox.

Reglas de este módulo:
  * Cero imports de frameworks de GUI (debe poder testearse sin PyQt6).
  * Cero dependencias externas: solo biblioteca estándar de Python.
  * Ninguna función escribe en stdout; se usa ``logging``.
  * Ningún subproceso se ejecuta con ``shell=True``.
"""

from __future__ import annotations

import grp
import hashlib
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping

LOG = logging.getLogger(__name__)

APP_NAME: Final[str] = "vibecBox"
QEMU_SYSTEM: Final[str] = "qemu-system-x86_64"
QEMU_IMG: Final[str] = "qemu-img"
MANAGED_BINARIES: Final[tuple[str, ...]] = (QEMU_SYSTEM, QEMU_IMG)

KVM_DEVICE: Final[Path] = Path("/dev/kvm")
MEMINFO_FILE: Final[Path] = Path("/proc/meminfo")

DOWNLOAD_TIMEOUT_S: Final[float] = 120.0
DOWNLOAD_CHUNK_SIZE: Final[int] = 1024 * 1024
USER_AGENT: Final[str] = f"{APP_NAME}/0.1"

PREBUILT_SOURCE_VERSION: Final[str] = "11.0.0.1"
PREBUILT_SOURCE_BASE: Final[str] = (
    "https://github.com/hermeticbuild/qemu-prebuilt/releases/download/"
    f"{PREBUILT_SOURCE_VERSION}"
)
SUPPORTED_ARCHES: Final[frozenset[str]] = frozenset({"x86_64", "amd64"})

_MEMTOTAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^MemTotal:\s+(\d+)\s+kB\s*$", re.MULTILINE
)


class VibecBoxError(RuntimeError):
    """Error base del backend."""


class BinaryNotFoundError(VibecBoxError):
    """El binario no está en PATH ni en el directorio gestionado."""


class DownloadError(VibecBoxError):
    """Fallo al descargar, validar o instalar un binario gestionado."""


@dataclass(frozen=True)
class DownloadSource:
    """Origen verificado de un binario gestionado."""

    url: str
    sha256: str
    archive: str | None = None  # "tar.gz", o None si es un ELF directo
    member: str | None = None   # ruta del binario dentro del archivo


# Fuente estática de PRUEBA: QEMU 11.0.0 (musl, static-pie, con KVM).
# ADVERTENCIA: este build NO incluye GTK ni SDL (-display none|curses).
# Sirve para validar la descarga y qemu-img; para la ventana de la Etapa 4
# usa el QEMU de la distro o un build propio con SDL/GTK (ver Memory.md).
_STATIC_TEST_SOURCES: Final[Mapping[str, DownloadSource]] = {
    QEMU_SYSTEM: DownloadSource(
        url=(
            f"{PREBUILT_SOURCE_BASE}/"
            f"qemu-system-bin-linux-amd64-x86_64-softmmu-"
            f"{PREBUILT_SOURCE_VERSION}.tar.gz"
        ),
        sha256="31399af8d874176f104679c6aae8c8741bcd86283dbc8de1fce8a140f67f1448",
        archive="tar.gz",
        member="bin/qemu-system-x86_64",
    ),
    QEMU_IMG: DownloadSource(
        url=(
            f"{PREBUILT_SOURCE_BASE}/"
            f"qemu-img-linux-amd64-{PREBUILT_SOURCE_VERSION}.tar.gz"
        ),
        sha256="4cab6e3f186ec6c500dd340f84b794f8320e3d58a2b2b9d2f835416589279d3e",
        archive="tar.gz",
        member="bin/qemu-img",
    ),
}

DOWNLOAD_SOURCES: Final[Mapping[str, DownloadSource]] = _STATIC_TEST_SOURCES


# --------------------------------------------------------------------------- #
# Rutas
# --------------------------------------------------------------------------- #
def data_home() -> Path:
    """Directorio de datos de vibecBox, respetando XDG."""
    override = os.environ.get("VIBECBOX_DATA_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def bin_dir() -> Path:
    """Directorio de binarios gestionados: ``~/.local/share/vibecBox/bin``."""
    override = os.environ.get("VIBECBOX_BIN_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return data_home() / "bin"


def is_executable_file(path: Path | str) -> bool:
    """True si ``path`` es un fichero regular con permiso de ejecución."""
    try:
        st = Path(path).stat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and os.access(path, os.X_OK)


# --------------------------------------------------------------------------- #
# Descubrimiento
# --------------------------------------------------------------------------- #
def _env_name(binary_name: str, suffix: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]", "_", binary_name).upper()
    return f"VIBECBOX_{suffix}_{slug}"


def _env_value(binary_name: str, suffix: str) -> str | None:
    value = os.environ.get(_env_name(binary_name, suffix), "").strip()
    return value or None


def _default_source(binary_name: str) -> DownloadSource | None:
    if platform.machine().lower() not in SUPPORTED_ARCHES:
        return None
    return DOWNLOAD_SOURCES.get(binary_name)


def _resolve_download_source(
    binary_name: str,
    *,
    url: str | None,
    sha256: str | None,
    archive: str | None,
    member: str | None,
) -> DownloadSource:
    """Fuente fijada por defecto, o URL propia con sha256 obligatorio."""
    custom_url = (url or _env_value(binary_name, "DOWNLOAD_URL") or "").strip()
    if custom_url:
        custom_sha = (sha256 or _env_value(binary_name, "DOWNLOAD_SHA256") or "").strip()
        if not custom_sha:
            raise DownloadError(
                f"La URL personalizada de '{binary_name}' no tiene sha256. "
                f"Define {_env_name(binary_name, 'DOWNLOAD_SHA256')}."
            )
        if archive is None and custom_url.endswith((".tar.gz", ".tgz")):
            archive = "tar.gz"
        if member is None and archive == "tar.gz":
            member = f"bin/{binary_name}"
        return DownloadSource(custom_url, custom_sha, archive, member)

    source = _default_source(binary_name)
    if source is None:
        raise DownloadError(
            f"No hay fuente estática para '{binary_name}' en "
            f"{platform.machine()}. Configura VIBECBOX_DOWNLOAD_URL_*."
        )

    if sha256:
        return DownloadSource(
            source.url, sha256, archive or source.archive, member or source.member
        )
    return source


def _require_elf(path: Path, origin: str) -> None:
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        raise DownloadError(f"No se pudo leer {path}: {exc}") from exc
    if magic != b"\x7fELF":
        raise DownloadError(f"{origin} no contiene un ELF de Linux válido")


def _extract_member(
    archive_path: Path, member: str, dest_dir: Path, binary_name: str
) -> Path:
    """Extrae un único miembro del tar.gz (nunca extractall)."""
    relative = Path(member)
    if relative.is_absolute() or ".." in relative.parts:
        raise DownloadError(f"Ruta insegura dentro del archivo: {member!r}")

    wanted = member.lstrip("./")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{binary_name}.extract-", dir=dest_dir)
    out_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            with tarfile.open(archive_path, "r:gz") as tar:
                info = next(
                    (i for i in tar.getmembers() if i.name.lstrip("./") == wanted),
                    None,
                )
                if info is None:
                    raise DownloadError(f"{member!r} no existe en {archive_path.name}")
                if not info.isfile():
                    raise DownloadError(f"{member!r} no es un fichero regular")
                extracted = tar.extractfile(info)
                if extracted is None:
                    raise DownloadError(f"No se pudo extraer {member!r}")
                shutil.copyfileobj(extracted, out)
        return out_path
    except DownloadError:
        out_path.unlink(missing_ok=True)
        raise
    except (tarfile.TarError, OSError) as exc:
        out_path.unlink(missing_ok=True)
        raise DownloadError(f"Archivo inválido {archive_path.name}: {exc}") from exc


def resolve_binary(binary_name: str, *, allow_download: bool = False) -> str | None:
    """Resuelve un binario en este orden: ruta explícita → PATH → gestionado.

    La descarga solo se intenta si ``allow_download`` es True. Devuelve la ruta
    absoluta o ``None``; no lanza excepción por ausencia (la GUI decide).
    """
    name = (binary_name or "").strip()
    if not name:
        raise ValueError("binary_name no puede estar vacío")

    # 1) Ruta explícita
    if os.sep in name or (os.altsep and os.altsep in name):
        candidate = Path(name).expanduser()
        if is_executable_file(candidate):
            return str(candidate.resolve())
        LOG.warning("Ruta explícita no ejecutable: %s", candidate)
        return None

    # 2) PATH del sistema
    on_path = shutil.which(name)
    if on_path:
        resolved = str(Path(on_path).resolve())
        LOG.debug("'%s' resuelto en PATH: %s", name, resolved)
        return resolved

    # 3) Directorio gestionado por vibecBox
    managed = bin_dir() / name
    if is_executable_file(managed):
        resolved = str(managed.resolve())
        LOG.debug("'%s' resuelto en directorio gestionado: %s", name, resolved)
        return resolved

    # 4) Descarga explícita
    if allow_download:
        try:
            return trigger_download(name)
        except DownloadError as exc:
            LOG.error("No se pudo descargar '%s': %s", name, exc)
            return None

    LOG.warning("'%s' no encontrado ni en PATH ni en %s", name, bin_dir())
    return None


def require_binary(binary_name: str, *, allow_download: bool = False) -> str:
    """Como :func:`resolve_binary`, pero lanza si no se encuentra."""
    path = resolve_binary(binary_name, allow_download=allow_download)
    if path is None:
        raise BinaryNotFoundError(
            f"'{binary_name}' no está en PATH ni en {bin_dir()}. "
            "Instala QEMU o define VIBECBOX_DOWNLOAD_URL_* y usa allow_download=True."
        )
    return path


def binary_version(binary_path: str, *, timeout: float = 5.0) -> str | None:
    """Primera línea de ``<binario> --version``, o ``None`` si no responde."""
    try:
        completed = subprocess.run(
            [binary_path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.debug("No se pudo ejecutar %s --version: %s", binary_path, exc)
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0 or not output:
        return None
    return output.splitlines()[0].strip()


@dataclass(frozen=True)
class BinaryStatus:
    """Estado de un binario para diagnóstico y para la futura GUI."""

    name: str
    path: str | None
    source: str  # "PATH" | "managed" | "missing"
    version: str | None = None

    @property
    def available(self) -> bool:
        return self.path is not None


def describe_binary(binary_name: str, *, probe: bool = True) -> BinaryStatus:
    """Resuelve el binario y, opcionalmente, consulta su versión."""
    path = resolve_binary(binary_name)
    if path is None:
        return BinaryStatus(name=binary_name, path=None, source="missing")

    try:
        in_managed = Path(path).is_relative_to(bin_dir().resolve())
    except (OSError, ValueError):
        in_managed = False

    return BinaryStatus(
        name=binary_name,
        path=path,
        source="managed" if in_managed else "PATH",
        version=binary_version(path) if probe else None,
    )


# --------------------------------------------------------------------------- #
# Descarga de binarios gestionados
# --------------------------------------------------------------------------- #
def trigger_download(
    binary_name: str,
    *,
    url: str | None = None,
    expected_sha256: str | None = None,
    member: str | None = None,
    archive: str | None = None,
    timeout: float = DOWNLOAD_TIMEOUT_S,
) -> str:
    """Descarga, verifica e instala un binario en ``bin_dir()`` (0o755).

    El sha256 es obligatorio. Soporta ELF directo y ``.tar.gz`` del que se
    extrae únicamente el miembro indicado. La instalación es atómica.
    """
    name = Path(binary_name).name
    if not name or name != binary_name:
        raise DownloadError(f"Nombre de binario inválido: {binary_name!r}")

    source = _resolve_download_source(
        name, url=url, sha256=expected_sha256, archive=archive, member=member
    )

    target_dir = bin_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"No se pudo crear {target_dir}: {exc}") from exc

    destination = target_dir / name
    fd, tmp_name = tempfile.mkstemp(prefix=f".{name}.part-", dir=target_dir)
    tmp_path = Path(tmp_name)
    digest = hashlib.sha256()
    total = 0
    extra_tmp: Path | None = None

    LOG.info("Descargando %s desde %s", name, source.url)
    try:
        with os.fdopen(fd, "wb") as out:
            request = urllib.request.Request(
                source.url, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", None)
                if status is not None and status != 200:
                    raise DownloadError(f"HTTP {status} al descargar {source.url}")
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    total += len(chunk)

        if total == 0:
            raise DownloadError(f"La descarga de {source.url} está vacía")

        actual = digest.hexdigest()
        if actual != source.sha256.strip().lower():
            raise DownloadError(
                f"sha256 no coincide para {name}: esperado {source.sha256}, "
                f"obtenido {actual}"
            )

        install_path = tmp_path
        if source.archive == "tar.gz":
            if not source.member:
                raise DownloadError(f"Falta 'member' para el archivo de {name}")
            extra_tmp = _extract_member(tmp_path, source.member, target_dir, name)
            install_path = extra_tmp

        _require_elf(install_path, source.url)
        os.chmod(install_path, 0o755)
        os.replace(install_path, destination)
    except DownloadError:
        raise
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise DownloadError(f"Fallo al descargar '{name}': {exc}") from exc
    finally:
        for leftover in (tmp_path, extra_tmp):
            if leftover is None:
                continue
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                LOG.debug("No se pudo limpiar %s", leftover)

    LOG.info("Instalado %s en %s", name, destination)
    return str(destination)


# --------------------------------------------------------------------------- #
# Capacidades del host
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class KvmStatus:
    device: str
    exists: bool
    is_char_device: bool
    readable: bool
    writable: bool
    group: str | None

    @property
    def usable(self) -> bool:
        return self.exists and self.is_char_device and self.readable and self.writable


def kvm_status(device: Path = KVM_DEVICE) -> KvmStatus:
    """Inspecciona ``/dev/kvm`` sin abrirlo."""
    try:
        st = device.stat()
    except OSError:
        return KvmStatus(str(device), False, False, False, False, None)

    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)

    return KvmStatus(
        device=str(device),
        exists=True,
        is_char_device=stat.S_ISCHR(st.st_mode),
        readable=os.access(device, os.R_OK),
        writable=os.access(device, os.W_OK),
        group=group,
    )


def check_kvm() -> bool:
    """True si ``/dev/kvm`` existe, es un char device y es r/w para el usuario."""
    return kvm_status().usable


def get_ram_max() -> int:
    """RAM total del host en MB (``MemTotal`` de ``/proc/meminfo``)."""
    try:
        meminfo = MEMINFO_FILE.read_text(encoding="ascii", errors="replace")
    except OSError as exc:
        raise VibecBoxError(f"No se pudo leer {MEMINFO_FILE}: {exc}") from exc

    match = _MEMTOTAL_PATTERN.search(meminfo)
    if match is None:
        raise VibecBoxError(f"{MEMINFO_FILE} no contiene una línea MemTotal válida")

    kib = int(match.group(1))
    if kib <= 0:
        raise VibecBoxError(f"MemTotal inválido en {MEMINFO_FILE}: {kib} kB")
    return kib // 1024


def get_cores_max() -> int:
    """CPUs lógicas del host según ``os.cpu_count()`` (mínimo 1)."""
    return max(1, os.cpu_count() or 1)


@dataclass(frozen=True)
class DisplaySupport:
    backends: frozenset[str]
    probed: bool
    error: str | None = None

    @property
    def has_window_backend(self) -> bool:
        """True si QEMU puede abrir su propia ventana (GTK/SDL)."""
        return bool(self.backends & {"gtk", "sdl"})


def display_backends(binary_path: str, *, timeout: float = 10.0) -> DisplaySupport:
    """Interroga ``-display help`` para conocer los backends compilados."""
    try:
        completed = subprocess.run(
            [binary_path, "-display", "help"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DisplaySupport(frozenset(), False, str(exc))

    text = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    backends: set[str] = set()
    in_list = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not in_list:
            if line.lower().startswith("available display backend types"):
                in_list = True
            continue
        if not line or " " in line:
            break
        backends.add(line.lower())

    if not backends:
        return DisplaySupport(
            frozenset(), False, "salida de -display help no reconocida"
        )
    return DisplaySupport(frozenset(backends), True)


# --------------------------------------------------------------------------- #
# Diagnóstico agregado (reutilizable por la GUI en Etapa 2)
# --------------------------------------------------------------------------- #
def environment_snapshot(*, probe_binaries: bool = True) -> dict[str, object]:
    """Resumen serializable del host y de los binarios gestionados."""
    kvm = kvm_status()
    binaries = {
        name: describe_binary(name, probe=probe_binaries)
        for name in MANAGED_BINARIES
    }
    try:
        ram_mb: int | None = get_ram_max()
    except VibecBoxError as exc:
        LOG.warning("%s", exc)
        ram_mb = None

    qemu = binaries[QEMU_SYSTEM]
    display = display_backends(qemu.path) if qemu.path is not None else None

    managed = bin_dir()
    return {
        "app": APP_NAME,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "bin_dir": str(managed),
        "bin_dir_exists": managed.is_dir(),
        "kvm": kvm,
        "ram_max_mb": ram_mb,
        "cores_max": get_cores_max(),
        "binaries": binaries,
        "qemu_display": display,
    }


__all__ = [
    "APP_NAME",
    "MANAGED_BINARIES",
    "PREBUILT_SOURCE_VERSION",
    "QEMU_IMG",
    "QEMU_SYSTEM",
    "SUPPORTED_ARCHES",
    "BinaryNotFoundError",
    "BinaryStatus",
    "DisplaySupport",
    "DownloadError",
    "DownloadSource",
    "KvmStatus",
    "VibecBoxError",
    "bin_dir",
    "binary_version",
    "check_kvm",
    "data_home",
    "describe_binary",
    "display_backends",
    "environment_snapshot",
    "get_cores_max",
    "get_ram_max",
    "is_executable_file",
    "kvm_status",
    "require_binary",
    "resolve_binary",
    "trigger_download",
]
