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

        name_helper = models.name_errors("")
        duplicate_helper = models.name_errors(
            "Prueba Etapa 2", existing_names=["prueba etapa 2"]
        )
        forbidden_helper = models.name_errors("ruta/inválida")
        check(
            OK
            if name_helper and duplicate_helper and forbidden_helper
            else FAIL,
            "Helpers de validación",
            "nombre vacío, duplicado y prohibido detectados",
        )
        check(
            OK
            if models.iso_path_errors("/no/existe.iso")
            and not models.iso_path_errors(None)
            else FAIL,
            "Helper ISO",
            "inexistente detectada; sin ISO válida",
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

        # ---------------------------------------------------------------- #
        # 4. Validación en la página, degradación y aceptación
        # ---------------------------------------------------------------- #
        good_iso = tmp_path / "instalacion.iso"
        good_iso.write_bytes(b"")
        wrong_iso = tmp_path / "instalacion.txt"
        wrong_iso.write_bytes(b"")

        page = wizard.name_page
        page.name_edit.setText("VM con ISO")
        page.iso_edit.setText("/no/existe.iso")
        check(
            OK
            if not page.isComplete() and "/no/existe.iso" in page.hint.text()
            else FAIL,
            "ISO inexistente en página",
            page.hint.text(),
        )
        page.iso_edit.setText(str(wrong_iso))
        check(
            OK
            if not page.isComplete() and "extensión" in page.hint.text()
            else FAIL,
            "ISO con extensión incorrecta",
            page.hint.text(),
        )
        page.iso_edit.setText(str(good_iso))
        check(
            OK if page.isComplete() else FAIL,
            "ISO válida en página",
            str(good_iso),
        )

        page.iso_edit.clear()
        page.hint.setText("centinela")
        pure = page.isComplete()
        check(
            OK if pure and page.hint.text() == "centinela" else FAIL,
            "isComplete sin efectos",
            "hint intacto",
        )

        changes: list[int] = []
        page.completeChanged.connect(lambda: changes.append(1))
        page.name_edit.setText("VM con ISO renombrada")
        check(
            OK if changes and "ISO es opcional" in page.hint.text() else FAIL,
            "Hint actualizado por señal",
            page.hint.text(),
        )

        warnings: list[tuple[str, str]] = []
        original_warning = gui.QMessageBox.warning

        def fake_warning(_parent, title, text, *args, **kwargs):
            warnings.append((str(title), str(text)))
            return gui.QMessageBox.StandardButton.Ok

        gui.QMessageBox.warning = fake_warning
        try:
            page.name_edit.setText("VM aceptada")
            page.iso_edit.setText("/no/existe.iso")
            wizard.accept()
        finally:
            gui.QMessageBox.warning = original_warning
        check(
            OK
            if wizard.result_config is None
            and warnings
            and "ISO" in warnings[-1][1]
            else FAIL,
            "accept rechaza ISO rota",
            warnings[-1][1] if warnings else "sin aviso",
        )

        warnings.clear()
        gui.QMessageBox.warning = fake_warning
        try:
            page.iso_edit.clear()
            wizard.accept()
        finally:
            gui.QMessageBox.warning = original_warning
        accepted = wizard.result_config
        check(
            OK
            if accepted is not None
            and not warnings
            and store.get(accepted.id) is not None
            else FAIL,
            "accept guarda VM válida",
            accepted.name if accepted else "no aceptada",
        )
        if accepted is not None:
            store.delete(accepted.id)

        # Edición de una VM existente.
        existing = models.VMConfig(
            name="VM existente",
            ram_mb=1536,
            vcpus=3,
            disk_gb=40,
            iso_path=str(good_iso),
            network="bridge",
            video="virtio-gpu",
            boot_order="cdrom",
        )
        store.save(existing)
        edit = gui.VMWizard(store, host, window, existing=existing)
        check(
            OK
            if edit.name_page.name_edit.text() == existing.name
            and edit.resources_page.ram_spin.value() == 1536
            and edit.resources_page.cpu_spin.value() == 3
            and edit.storage_page.disk_spin.value() == 40
            and edit.connection_page.network.currentData() == "bridge"
            and edit.connection_page.video.currentData() == "virtio-gpu"
            and edit.connection_page.boot.currentData() == "cdrom"
            else FAIL,
            "Wizard _load",
            "nombre, recursos, disco y conexión restaurados",
        )
        check(
            OK
            if edit.existing_id == existing.id and edit.name_page.isComplete()
            else FAIL,
            "Wizard _load nombre excluido",
            "el nombre propio no bloquea la edición",
        )
        rebuilt = edit._build_config()
        check(
            OK
            if rebuilt.id == existing.id
            and rebuilt.iso_path == str(good_iso)
            and rebuilt.network == "bridge"
            else FAIL,
            "Wizard _build_config conserva id",
            rebuilt.id,
        )
        store.delete(existing.id)

        # Host con capacidades degradadas.
        degraded_host = gui.HostInfo(
            ram_max_mb=0,
            cores_max=0,
            kvm_ok=False,
            qemu_path=None,
            qemu_version=None,
            img_version=None,
            display=None,
            ram_error="MemTotal ilegible en /proc/meminfo",
        )
        degraded = gui.VMWizard(store, degraded_host, window)
        resources = degraded.resources_page
        check(
            OK
            if resources.ram_spin.minimum() <= resources.ram_spin.maximum()
            and resources.ram_spin.maximum() == gui.DEFAULT_RAM_MAX_MB
            and resources.cpu_spin.minimum() <= resources.cpu_spin.maximum()
            and resources.cpu_spin.maximum() == 1
            else FAIL,
            "Recursos degradados",
            f"RAM máx {resources.ram_spin.maximum()} MB · "
            f"CPU máx {resources.cpu_spin.maximum()}",
        )
        check(
            OK
            if resources.warning is not None
            and "MemTotal" in resources.warning.text()
            else FAIL,
            "Aviso de degradación",
            resources.warning.text() if resources.warning else "sin aviso",
        )
        degraded._load(models.VMConfig(name="VM degradada", ram_mb=2048, vcpus=4))
        check(
            OK
            if resources.ram_spin.value() == 2048
            and resources.cpu_spin.value() == 1
            else FAIL,
            "Recursos degradados en _load",
            f"{resources.ram_spin.value()} MB · "
            f"{resources.cpu_spin.value()} vCPU",
        )

        degraded_warnings: list[str] = []
        gui.QMessageBox.warning = (
            lambda _parent, _title, text, *args, **kwargs: (
                degraded_warnings.append(str(text))
                or gui.QMessageBox.StandardButton.Ok
            )
        )
        try:
            degraded.name_page.name_edit.setText("VM host degradado")
            degraded.accept()
        finally:
            gui.QMessageBox.warning = original_warning
        degraded_config = degraded.result_config
        check(
            OK
            if degraded_config is not None
            and not degraded_warnings
            and store.get(degraded_config.id) is not None
            else FAIL,
            "accept con host degradado",
            degraded_config.name if degraded_config else "no aceptada",
        )
        if degraded_config is not None:
            store.delete(degraded_config.id)

        # Diagnóstico reutilizando environment_snapshot().
        snapshot = {
            "app": "vibecBox",
            "python": "3.14.0",
            "platform": "Linux-de-prueba",
            "bin_dir": str(tmp_path / "bin"),
            "bin_dir_exists": False,
            "kvm": core.KvmStatus("/dev/kvm", True, True, True, True, "kvm"),
            "ram_max_mb": 8192,
            "cores_max": 4,
            "binaries": {
                core.QEMU_SYSTEM: core.BinaryStatus(
                    core.QEMU_SYSTEM,
                    "/usr/bin/qemu-system-x86_64",
                    "PATH",
                    "QEMU emulator version 11.0.0",
                ),
                core.QEMU_IMG: core.BinaryStatus(
                    core.QEMU_IMG, None, "missing", None
                ),
            },
            "qemu_display": core.DisplaySupport(
                frozenset({"gtk", "sdl"}), True
            ),
        }
        dialog = gui.DiagnosticsDialog(host, window, snapshot=snapshot)
        check(
            OK
            if "8192 MB" in dialog.report_text
            and "/dev/kvm" in dialog.report_text
            and "gtk" in dialog.report_text
            and "11.0.0" in dialog.report_text
            and dialog.copy_button is not None
            else FAIL,
            "Diagnóstico con snapshot",
            "RAM, KVM, vídeo y versión presentes",
        )
        dialog._copy()
        check(
            OK
            if QApplication.clipboard().text() == dialog.report_text
            else FAIL,
            "Diagnóstico copiar",
            f"{len(dialog.report_text)} caracteres",
        )

        degraded_snapshot = dict(snapshot)
        degraded_snapshot["ram_max_mb"] = None
        degraded_snapshot["qemu_display"] = None
        degraded_dialog = gui.DiagnosticsDialog(
            degraded_host, window, snapshot=degraded_snapshot
        )
        check(
            OK
            if "no disponible" in degraded_dialog.report_text
            and "MemTotal ilegible" in degraded_dialog.report_text
            else FAIL,
            "Diagnóstico degradado",
            "RAM no disponible con aviso",
        )
        dialog.deleteLater()
        degraded_dialog.deleteLater()

        # Cierre ordenado: QMP/SIGTERM antes de SIGKILL.
        from PyQt6.QtGui import QCloseEvent

        events: list[tuple[str, object]] = []

        class _FakeClosingRunner:
            def __init__(self, stops: bool) -> None:
                self.stops = stops
                self.running = True
                self.killed = False

            @property
            def is_running(self) -> bool:
                return self.running

            def stop(self, *, graceful: bool = True) -> None:
                events.append(("stop", graceful))

            def wait_for_finished(self, timeout_ms: int = 3000) -> bool:
                events.append(("wait", timeout_ms))
                if self.stops and not self.killed:
                    self.running = False
                return True

            def force_kill(self) -> None:
                events.append(("kill", self.stops))
                self.killed = True
                self.running = False

        close_window = gui.MainWindow(host, store)
        close_window.runners = {
            "graceful": _FakeClosingRunner(True),
            "stubborn": _FakeClosingRunner(False),
        }
        old_grace = gui.CLOSE_GRACEFUL_TIMEOUT_MS
        old_kill = gui.CLOSE_KILL_TIMEOUT_MS
        gui.CLOSE_GRACEFUL_TIMEOUT_MS = 150
        gui.CLOSE_KILL_TIMEOUT_MS = 150
        try:
            close_window.closeEvent(QCloseEvent())
        finally:
            gui.CLOSE_GRACEFUL_TIMEOUT_MS = old_grace
            gui.CLOSE_KILL_TIMEOUT_MS = old_kill
        stops = [event for event in events if event[0] == "stop"]
        kills = [event for event in events if event[0] == "kill"]
        kill_index = (
            events.index(("kill", False))
            if ("kill", False) in events
            else len(events)
        )
        check(
            OK
            if len(stops) == 2
            and all(event[1] is True for event in stops)
            and len(kills) == 1
            and kills[0][1] is False
            and ("wait", gui._WAIT_SLICE_MS) in events[:kill_index]
            and not close_window.runners
            else FAIL,
            "closeEvent ordenado",
            f"{len(stops)} stop · {len(kills)} SIGKILL",
        )
        close_window.deleteLater()

        # CLI y logging persistente.
        log_path = gui._log_file_path()
        options, qt_args = gui._parse_cli_args(
            ["-v", "-platform", "offscreen"]
        )
        check(
            OK
            if options.verbose
            and qt_args == ["-platform", "offscreen"]
            and log_path.parent == core.data_home()
            else FAIL,
            "CLI y ruta de log",
            str(log_path),
        )
        configured = gui._configure_logging(False)
        check(
            OK
            if configured is not None and configured.exists()
            else FAIL,
            "Log a fichero",
            str(configured),
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
