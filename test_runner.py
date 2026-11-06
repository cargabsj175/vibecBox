#!/usr/bin/env python3
"""Pruebas de la Etapa 4: QProcess, QMP y ciclo de vida real.

Sí lanza QEMU, pero siempre con ``-display none`` y un disco sin SO invitado:
no se abre ninguna ventana ni se instala nada. Requiere ``qemu-system-x86_64``
y PyQt6 (se ejecuta con el Python de ``.venv``).

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python test_runner.py
    QT_QPA_PLATFORM=offscreen .venv/bin/python test_runner.py --debug
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OK, WARN, FAIL = "OK", "WARN", "FAIL"
Row = tuple[str, str, str]


def has_pair(args: Sequence[str], flag: str, value: str) -> bool:
    return any(
        args[index] == flag and index + 1 < len(args) and args[index + 1] == value
        for index in range(len(args))
    )


def has_prefix(args: Sequence[str], flag: str, prefix: str) -> bool:
    return any(
        args[index] == flag
        and index + 1 < len(args)
        and args[index + 1].startswith(prefix)
        for index in range(len(args))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tests del runner QProcess/QMP.")
    parser.add_argument("--debug", action="store_true", help="logs DEBUG")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.ERROR,
        format="%(levelname)s %(name)s: %(message)s",
    )

    rows: list[Row] = []

    def check(status: str, label: str, detail: str = "") -> None:
        rows.append((status, label, detail))

    try:
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6.QtWidgets import QApplication
    except ModuleNotFoundError:
        print("PyQt6 no está instalado; usa .venv/bin/python test_runner.py")
        return 2

    app = QApplication.instance() or QApplication([])

    def wait_for(predicate: Callable[[], bool], timeout_ms: int = 10_000) -> bool:
        if predicate():
            return True
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(20)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        poll.start()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(timeout_ms)
        loop.exec()
        poll.stop()
        deadline.stop()
        return predicate()

    with tempfile.TemporaryDirectory(prefix="vibecbox-runner-") as tmp:
        root = Path(tmp)
        os.environ["VIBECBOX_DATA_HOME"] = str(root / "data")

        import models
        import qemu
        import runner
        import vibecBox_core as core

        if core.resolve_binary(core.QEMU_SYSTEM) is None:
            check(WARN, "QEMU disponible", "no encontrado; tests de proceso omitidos")
        else:
            qemu_path = core.require_binary(core.QEMU_SYSTEM)
            vm = models.VMConfig(
                name="Runner",
                ram_mb=512,
                vcpus=1,
                disk_gb=1,
            )
            qemu.create_disk(vm)

            class Host:
                kvm_ok = core.check_kvm()

            host = Host()

            # ------------------------------------------------------------ #
            # 1. Plan con QMP y sin monitor interactivo
            # ------------------------------------------------------------ #
            qmp = root / "data" / "run" / "prueba.qmp"
            plan = qemu.plan_launch(
                vm,
                qemu_path=qemu_path,
                kvm_available=True,
                display="none",
                qmp_socket=qmp,
            )
            check(
                OK
                if has_pair(plan.args, "-monitor", "none")
                and has_prefix(plan.args, "-qmp", f"unix:{qmp},server=on,wait=off")
                and has_pair(plan.args, "-display", "none")
                else FAIL,
                "Argumentos QMP",
                "-monitor none · -qmp unix:…,server=on,wait=off",
            )

            # ------------------------------------------------------------ #
            # 2. Arranque real y parada ordenada (QMP → SIGTERM)
            # ------------------------------------------------------------ #
            graceful = runner.VMRunner(vm, host, display="none")
            states: list[models.VMState] = []
            lines: list[str] = []
            finished: list[object] = []
            started: list[str] = []
            graceful.state_changed.connect(lambda _i, s: states.append(s))
            graceful.log_line.connect(lambda _i, line: lines.append(line))
            graceful.finished.connect(lambda _i, _c, s: finished.append(s))
            graceful.started.connect(started.append)

            graceful.start()
            ready = wait_for(lambda: bool(started) and graceful.qmp_path.exists(), 15_000)
            check(
                OK if ready and graceful.pid > 0 else FAIL,
                "QEMU iniciado",
                f"PID {graceful.pid} · QMP {'presente' if graceful.qmp_path.exists() else 'ausente'}",
            )

            # No hay SO invitado: system_powerdown no apagará nada, así que el
            # plazo de gracia debe caer a SIGTERM. Se acorta para el test.
            original_grace = runner.GRACEFUL_TIMEOUT_MS
            runner.GRACEFUL_TIMEOUT_MS = 1_200
            try:
                graceful.stop(graceful=True)
                ended = wait_for(lambda: bool(finished), 15_000)
            finally:
                runner.GRACEFUL_TIMEOUT_MS = original_grace

            check(
                OK if ended and finished[-1] == models.VMState.STOPPED else FAIL,
                "Parada ordenada",
                f"estado final {getattr(finished[-1], 'value', finished[-1]) if finished else 'sin fin'}",
            )
            check(
                OK
                if any("Solicitando apagado ordenado" in line for line in lines)
                and any(
                    "QMP aceptó" in line or "QMP no disponible" in line for line in lines
                )
                else FAIL,
                "Ruta QMP/SIGTERM",
                next(
                    (line for line in lines if "QMP" in line or "SIGTERM" in line),
                    "sin traza",
                )[:60],
            )
            check(
                OK
                if models.VMState.RUNNING in states
                and states[-1] == models.VMState.STOPPED
                else FAIL,
                "Transiciones de estado",
                " → ".join(state.value for state in states),
            )
            check(
                OK if not graceful.qmp_path.exists() else FAIL,
                "Limpieza del socket",
                "QMP eliminado",
            )
            graceful.deleteLater()

            # ------------------------------------------------------------ #
            # 3. Parada forzada (SIGKILL) sin marcar ERROR
            # ------------------------------------------------------------ #
            forced = runner.VMRunner(vm, host, display="none")
            forced_states: list[models.VMState] = []
            forced_finished: list[object] = []
            forced_started: list[str] = []
            forced.state_changed.connect(lambda _i, s: forced_states.append(s))
            forced.finished.connect(lambda _i, _c, s: forced_finished.append(s))
            forced.started.connect(forced_started.append)
            forced.start()
            wait_for(lambda: bool(forced_started), 15_000)
            forced.force_kill()
            ended = wait_for(lambda: bool(forced_finished), 10_000)
            check(
                OK
                if ended
                and forced_finished[-1] == models.VMState.STOPPED
                and models.VMState.RUNNING in forced_states
                else FAIL,
                "Forzar parada",
                f"SIGKILL · estado {getattr(forced_finished[-1], 'value', forced_finished[-1]) if forced_finished else 'sin fin'}",
            )
            forced.deleteLater()

            # ------------------------------------------------------------ #
            # 4. Binario inexistente → FailedToStart → ERROR
            # ------------------------------------------------------------ #
            broken = runner.VMRunner(
                vm, host, display="none", qemu_path=str(root / "no-existe-qemu")
            )
            broken_finished: list[object] = []
            broken_states: list[models.VMState] = []
            broken.finished.connect(lambda _i, _c, s: broken_finished.append(s))
            broken.state_changed.connect(lambda _i, s: broken_states.append(s))
            broken.start()
            ended = wait_for(lambda: bool(broken_finished), 10_000)
            check(
                OK
                if ended
                and broken_finished[-1] == models.VMState.ERROR
                and models.VMState.ERROR in broken_states
                else FAIL,
                "Fallo de arranque",
                "FailedToStart → ERROR",
            )
            broken.deleteLater()

        # ---------------------------------------------------------------- #
        # 5. Formato de log
        # ---------------------------------------------------------------- #
        line = runner.format_log_line("Mi VM", "hola QEMU")
        check(
            OK
            if line.startswith("[") and "Mi VM: hola QEMU" in line
            else FAIL,
            "Formato de log",
            line,
        )

    # -------------------------------------------------------------------- #
    # Informe
    # -------------------------------------------------------------------- #
    width = max(len(label) for _, label, _ in rows) + 2
    line = "=" * 84
    print(line)
    print("vibecBox :: test_runner — QProcess, QMP y ciclo de vida")
    print(line)
    for status, label, detail in rows:
        print(f"[{status:<4}] {label:<{width}} {detail}")
    print("-" * 84)

    counts = {s: sum(1 for row in rows if row[0] == s) for s in (OK, WARN, FAIL)}
    print(f"Resultado: {counts[OK]} OK · {counts[WARN]} WARN · {counts[FAIL]} FAIL")
    app.processEvents()
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
