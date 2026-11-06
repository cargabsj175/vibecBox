#!/usr/bin/env python3
"""Prueba de humo del backend vibecBox_core.

Uso:
    python3 test_core.py             # solo lectura, sin red
    python3 test_core.py --download  # descarga desde la fuente estática fijada
    python3 test_core.py --debug     # logs DEBUG del backend

Códigos de salida:
    0 -> comprobaciones críticas OK (binarios ausentes = WARN)
    1 -> fallo crítico (KVM, RAM o CPUs)
"""

from __future__ import annotations

import argparse
import ast
import logging
import platform
import sys
from collections.abc import Callable
from pathlib import Path

import vibecBox_core as core

OK, WARN, FAIL = "OK", "WARN", "FAIL"
Row = tuple[str, str, str]


def _gui_imports(module_path: str | Path) -> set[str]:
    """Nombres de frameworks de GUI importados por un módulo (vía AST)."""
    source = Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names & {"PyQt6", "PySide6"}


def _probe_binaries(
    download: bool, check: Callable[[str, str, str], None]
) -> None:
    for name in core.MANAGED_BINARIES:
        status = core.describe_binary(name)
        if status.available:
            version = status.version or "sin respuesta a --version"
            check(OK, name, f"{status.source}: {status.path} | {version}")
            continue

        if download:
            try:
                path = core.trigger_download(name)
            except core.DownloadError as exc:
                check(WARN, name, f"ausente; descarga fallida: {exc}")
                continue
            downloaded = core.describe_binary(name)
            check(
                OK,
                name,
                f"descargado: {path} | {downloaded.version or 'sin versión'}",
            )
            continue

        check(WARN, name, "no encontrado en PATH ni en el directorio gestionado")


def _probe_display(
    check: Callable[[str, str, str], None]
) -> core.DisplaySupport | None:
    path = core.resolve_binary(core.QEMU_SYSTEM)
    if path is None:
        check(WARN, "Vídeo QEMU", "sin qemu-system: no se puede comprobar GTK/SDL")
        return None

    support = core.display_backends(path)
    if not support.probed:
        check(
            WARN,
            "Vídeo QEMU",
            f"no se pudo consultar -display help: {support.error}",
        )
        return support

    names = ", ".join(sorted(support.backends))
    if support.has_window_backend:
        check(OK, "Vídeo QEMU", f"backends: {names} (ventana nativa GTK/SDL)")
    else:
        check(
            WARN,
            "Vídeo QEMU",
            f"backends: {names} (sin GTK/SDL: no abre ventana; usa QEMU de la distro)",
        )
    return support


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Valida el descubrimiento dinámico de vibecBox en este host."
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="descarga desde la fuente estática fijada (sha256 verificado)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="activa logs DEBUG del backend",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    rows: list[Row] = []

    def check(status: str, label: str, detail: str = "") -> None:
        rows.append((status, label, detail))

    # 1. Entorno de ejecución
    check(OK, "Python", f"{platform.python_version()} ({sys.executable})")
    check(OK, "Plataforma", platform.platform())

    # 2. Pureza del backend (sin frameworks de GUI)
    leaks: set[str] = set()
    checked: list[str] = []
    if core.__file__:
        for module_file in (
            Path(core.__file__),
            Path(core.__file__).with_name("models.py"),
            Path(core.__file__).with_name("qemu.py"),
        ):
            if module_file.is_file():
                leaks |= _gui_imports(module_file)
                checked.append(module_file.name)
    if leaks:
        check(FAIL, "Backend sin GUI", f"importa {', '.join(sorted(leaks))}")
    else:
        check(
            OK,
            "Backend sin GUI",
            f"{', '.join(checked)} sin PyQt6/PySide6",
        )

    # 3. Directorio gestionado
    managed = core.bin_dir()
    if managed.is_dir():
        check(OK, "Directorio gestionado", f"{managed} (existe)")
    else:
        check(
            OK,
            "Directorio gestionado",
            f"{managed} (se creará en la primera descarga)",
        )

    # 4. KVM
    kvm = core.kvm_status()
    if not kvm.exists:
        check(FAIL, "KVM", f"{kvm.device} no existe")
    else:
        detail = (
            f"{kvm.device} | grupo={kvm.group or '?'} | "
            f"char={'sí' if kvm.is_char_device else 'no'} "
            f"r={'sí' if kvm.readable else 'no'} "
            f"w={'sí' if kvm.writable else 'no'}"
        )
        check(OK if kvm.usable else FAIL, "KVM", detail)

    # 5. RAM
    try:
        ram_mb = core.get_ram_max()
        check(OK, "RAM máxima", f"{ram_mb} MB ({ram_mb / 1024:.1f} GiB)")
    except core.VibecBoxError as exc:
        check(FAIL, "RAM máxima", str(exc))

    # 6. CPUs
    cores = core.get_cores_max()
    check(OK if cores >= 1 else FAIL, "CPUs lógicas", f"{cores} hilos (os.cpu_count)")

    # 7. Binarios y capacidad de vídeo
    _probe_binaries(args.download, check)
    display = _probe_display(check)

    # Render del informe
    width = max(len(label) for _, label, _ in rows) + 2
    line = "=" * 84
    print(line)
    print("vibecBox :: test_core — descubrimiento de host y binarios")
    print(line)
    for status, label, detail in rows:
        print(f"[{status:<4}] {label:<{width}} {detail}")
    print("-" * 84)

    counts = {
        status: sum(1 for row in rows if row[0] == status)
        for status in (OK, WARN, FAIL)
    }
    print(f"Resultado: {counts[OK]} OK · {counts[WARN]} WARN · {counts[FAIL]} FAIL")

    if counts[FAIL]:
        print("Estado: FALLO. Revisa los elementos marcados como FAIL.")
        return 1

    missing = [
        name for name in core.MANAGED_BINARIES if core.resolve_binary(name) is None
    ]
    if missing:
        print(f"Estado: backend OK; faltan binarios: {', '.join(missing)}")
        print("Siguiente paso: instala QEMU (ej. 'sudo apt install qemu-system-x86')")
        print("o vuelve a ejecutar con --download para usar la fuente estática fijada.")
        return 0

    if display is not None and display.probed and not display.has_window_backend:
        print(
            "Aviso: el QEMU resuelto es headless (sin GTK/SDL). "
            "Instala QEMU de la distro para la ventana de la Etapa 4."
        )

    print("Estado: todo listo. Binarios resueltos y KVM disponible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
