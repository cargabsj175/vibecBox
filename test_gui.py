#!/usr/bin/env python3
"""Prueba de humo de la Etapa 2: modelo + GUI en modo offscreen.

Uso:
    .venv/bin/python test_gui.py          # sin abrir ventana real
    .venv/bin/python test_gui.py --debug

Códigos de salida:
    0 -> todo OK
    1 -> algún fallo
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OK, WARN, FAIL = "OK", "WARN", "FAIL"
Row = tuple[str, str, str]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test de la GUI de vibecBox.")
    parser.add_argument("--debug", action="store_true", help="logs DEBUG")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    rows: list[Row] = []

    def check(status: str, label: str, detail: str = "") -> None:
        rows.append((status, label, detail))

    with tempfile.TemporaryDirectory(prefix="vibecbox-gui-") as tmp:
        tmp_path = Path(tmp)
        os.environ["VIBECBOX_DATA_HOME"] = str(tmp_path / "data")
        os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "config")

        # Importar después de fijar el entorno: core resuelve rutas en llamada.
        import models
        import vibecBox_core as core

        check(OK, "Entorno aislado", str(tmp_path))

        # ---------------------------------------------------------------- #
        # 1. Modelo y validación
        # ---------------------------------------------------------------- #
        ram_max = core.get_ram_max()
        cores_max = core.get_cores_max()

        vm = models.VMConfig(name="Prueba Etapa 2", ram_mb=2048, vcpus=2, disk_gb=20)
        errors = vm.validate(ram_max_mb=ram_max, cores_max=cores_max)
        check(
            OK if not errors else FAIL,
            "VM válida",
            "sin errores" if not errors else "; ".join(errors),
        )

        bad = models.VMConfig(
            name="Mala",
            ram_mb=ram_max + 4096,
            vcpus=cores_max + 4,
            disk_gb=0,
            network="wifi",
            video="voodoo",
        )
        bad_errors = bad.validate(ram_max_mb=ram_max, cores_max=cores_max)
        check(
            OK if len(bad_errors) >= 4 else FAIL,
            "Validación rechaza",
            f"{len(bad_errors)} errores detectados",
        )

        duplicated = models.VMConfig(name="prueba etapa 2")
        dup_errors = duplicated.validate(
            ram_max_mb=ram_max,
            cores_max=cores_max,
            existing_names=["Prueba Etapa 2"],
        )
        check(
            OK if dup_errors else FAIL,
            "Nombre duplicado",
            dup_errors[0] if dup_errors else "no detectado",
        )

        bad_iso = models.VMConfig(name="ISO rota", iso_path="/no/existe.iso")
        iso_errors = bad_iso.validate(
            ram_max_mb=ram_max, cores_max=cores_max, check_paths=True
        )
        check(
            OK if iso_errors else FAIL,
            "ISO inexistente",
            iso_errors[0] if iso_errors else "no detectado",
        )

        # ---------------------------------------------------------------- #
        # 2. Persistencia
        # ---------------------------------------------------------------- #
        store = models.VMStore()
        saved = store.save(vm)
        mode = saved.stat().st_mode & 0o777
        check(
            OK if mode == 0o600 else FAIL,
            "Escritura atómica",
            f"{saved.name} modo {mode:o}",
        )

        leftovers = list(store.root.glob(".vm-*"))
        check(
            OK if not leftovers else FAIL,
            "Sin temporales",
            f"{len(leftovers)} restos",
        )

        loaded = store.get(vm.id)
        check(
            OK
            if loaded is not None
            and loaded.name == vm.name
            and loaded.ram_mb == vm.ram_mb
            and loaded.state == vm.state
            else FAIL,
            "Round-trip JSON",
            f"{loaded.name if loaded else 'no cargada'}",
        )

        # ---------------------------------------------------------------- #
        # 3. GUI offscreen
        # ---------------------------------------------------------------- #
        from PyQt6.QtCore import QT_VERSION_STR
        from PyQt6.QtWidgets import QApplication, QWizard

        import vibecBox_gui as gui

        check(OK, "Qt", QT_VERSION_STR)

        app = QApplication.instance() or QApplication([])
        host = gui.load_host_info()
        check(
            OK,
            "HostInfo",
            f"RAM {host.ram_max_mb} MB · {host.cores_max} CPUs · "
            f"KVM {'sí' if host.kvm_ok else 'no'}",
        )

        window = gui.MainWindow(host, store)
        check(
            OK if window.list.count() == 1 else FAIL,
            "Lista de VMs",
            f"{window.list.count()} elemento(s)",
        )
        check(
            OK if window.list.currentItem() is not None else FAIL,
            "Selección inicial",
            "primera VM seleccionada",
        )
        check(
            OK
            if window.log_view.isReadOnly() and window.log_dock.widget() is not None
            else FAIL,
            "Dock de logs",
            "QPlainTextEdit de solo lectura",
        )
        check(
            OK
            if window.act_start.isEnabled()
            and not window.act_stop.isEnabled()
            and not window.act_force.isEnabled()
            else FAIL,
            "Acciones en reposo",
            "iniciar sí · apagar/forzar no",
        )

        # Un estado «running» huérfano debe reconciliarse a stopped.
        stale = vm.copy_with(
            id="stale-running",
            name="VM huérfana",
            state=models.VMState.RUNNING,
        )
        store.save(stale)
        window._reconcile_states()
        check(
            OK
            if store.get(stale.id) is not None
            and store.get(stale.id).state == models.VMState.STOPPED
            else FAIL,
            "Reconciliación de estado",
            "running huérfano → stopped",
        )
        store.delete(stale.id)

        class _FakeRunner:
            is_running = True

        window.runners[vm.id] = _FakeRunner()  # type: ignore[assignment]
        window._update_actions()
        check(
            OK
            if not window.act_start.isEnabled()
            and window.act_stop.isEnabled()
            and window.act_force.isEnabled()
            and not window.act_edit.isEnabled()
            and not window.act_delete.isEnabled()
            else FAIL,
            "Acciones en ejecución",
            "apagar/forzar sí · iniciar/editar/eliminar no",
        )
        window.runners.pop(vm.id, None)
        window._update_actions()

        window._show_details(loaded)
        shown_name = window.detail_labels["name"].text()
        check(
            OK if shown_name == vm.name else FAIL,
            "Panel de detalles",
            shown_name,
        )

        # Asistente: nombre válido y duplicado
        wizard = gui.VMWizard(store, host, window)
        wizard.name_page.name_edit.setText("Otra VM")
        check(
            OK if wizard.name_page.isComplete() else FAIL,
            "Asistente nombre libre",
            "página completa",
        )
        wizard.name_page.name_edit.setText(vm.name)
        check(
            OK if not wizard.name_page.isComplete() else FAIL,
            "Asistente nombre duplicado",
            "bloqueado correctamente",
        )

        wizard.name_page.name_edit.setText("VM desde asistente")
        config = wizard._build_config()
        wizard_errors = config.validate(
            ram_max_mb=host.ram_max_mb, cores_max=host.cores_max
        )
        check(
            OK if not wizard_errors else FAIL,
            "Asistente configuración",
            f"{config.ram_mb} MB · {config.vcpus} vCPU · {config.network}",
        )

        deleted = app is not None
        store.delete(vm.id)
        window.refresh()
        check(
            OK if deleted and window.list.count() == 0 else FAIL,
            "Eliminar y refrescar",
            f"{window.list.count()} elemento(s)",
        )

        window.close()
        app.processEvents()

    # -------------------------------------------------------------------- #
    # Informe
    # -------------------------------------------------------------------- #
    width = max(len(label) for _, label, _ in rows) + 2
    line = "=" * 84
    print(line)
    print("vibecBox :: test_gui — modelo y GUI en modo offscreen")
    print(line)
    for status, label, detail in rows:
        print(f"[{status:<4}] {label:<{width}} {detail}")
    print("-" * 84)

    counts = {s: sum(1 for row in rows if row[0] == s) for s in (OK, WARN, FAIL)}
    print(f"Resultado: {counts[OK]} OK · {counts[WARN]} WARN · {counts[FAIL]} FAIL")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
