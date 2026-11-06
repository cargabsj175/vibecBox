#!/usr/bin/env python3
"""Ejecución y ciclo de vida de QEMU con QProcess (Etapa 4).

La ventana de la VM la abre QEMU (GTK/SDL): este módulo solo lanza el proceso,
captura su salida, escucha QMP para pedir un apagado ordenado y aplica SIGTERM
o SIGKILL si el invitado no responde. No captura framebuffers ni usa VNC/SPICE.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalSocket

import models
import qemu
import vibecBox_core as core

LOG = logging.getLogger(__name__)

GRACEFUL_TIMEOUT_MS = 12_000
KILL_TIMEOUT_MS = 5_000
QMP_TIMEOUT_MS = 3_000


class _QmpPowerdown(QObject):
    """Sesión QMP mínima: capabilities + system_powerdown."""

    done = pyqtSignal(bool, str)

    def __init__(self, socket_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._buffer = bytearray()
        self._power_sent = False
        self._finished = False

        self.socket = QLocalSocket(self)
        self.socket.connected.connect(self._on_connected)
        self.socket.readyRead.connect(self._on_ready)
        self.socket.errorOccurred.connect(self._on_error)

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self._finish(False, "tiempo de espera QMP"))
        self._timeout.start(QMP_TIMEOUT_MS)

        self.socket.connectToServer(str(socket_path))

    def _write(self, payload: dict[str, object]) -> None:
        self.socket.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.socket.flush()

    def _on_connected(self) -> None:
        self._write({"execute": "qmp_capabilities"})

    def _on_ready(self) -> None:
        self._buffer.extend(bytes(self.socket.readAll()))
        while b"\n" in self._buffer:
            raw, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict) or "QMP" in message:
                continue
            if "error" in message:
                self._finish(False, str(message.get("error")))
                return
            if "return" in message and not self._power_sent:
                self._write({"execute": "system_powerdown"})
                self._power_sent = True
                QTimer.singleShot(150, lambda: self._finish(True, "system_powerdown"))

    def _on_error(self, _error: QLocalSocket.LocalSocketError) -> None:
        self._finish(False, self.socket.errorString() or "error de socket QMP")

    def _finish(self, ok: bool, detail: str) -> None:
        if self._finished:
            return
        self._finished = True
        self._timeout.stop()
        try:
            self.socket.disconnectFromServer()
        except (RuntimeError, OSError):
            pass
        self.done.emit(ok, detail)


class VMRunner(QObject):
    """Un proceso QEMU y su estado, sin lógica de presentación."""

    started = pyqtSignal(str)
    state_changed = pyqtSignal(str, object)
    log_line = pyqtSignal(str, str)
    finished = pyqtSignal(str, int, object)

    def __init__(
        self,
        vm: models.VMConfig,
        host: object,
        *,
        display: str | None = "gtk,window-close=on",
        qemu_path: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.vm = vm
        self.display = display
        self._finalized = False
        self._stopping = False
        self._buffer = ""

        run_dir = core.data_home() / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.qmp_path = run_dir / f"{vm.id}.qmp"
        self.qmp_path.unlink(missing_ok=True)

        self.plan = qemu.plan_launch(
            vm,
            qemu_path=qemu_path,
            kvm_available=bool(getattr(host, "kvm_ok", False)),
            display=display,
            qmp_socket=self.qmp_path,
        )

        self.process = QProcess(self)
        self.process.setProgram(self.plan.program)
        self.process.setArguments(list(self.plan.args))
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.started.connect(self._on_started)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)
        self.process.readyReadStandardOutput.connect(self._drain)

        self._grace_timer = QTimer(self)
        self._grace_timer.setSingleShot(True)
        self._grace_timer.timeout.connect(self._on_grace_timeout)

        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._on_kill_timeout)

        self._qmp: _QmpPowerdown | None = None

    # -- estado ------------------------------------------------------------ #
    @property
    def is_running(self) -> bool:
        return self.process.state() != QProcess.ProcessState.NotRunning

    @property
    def pid(self) -> int:
        return int(self.process.processId())

    def start(self) -> None:
        if self.is_running:
            return
        self._finalized = False
        self._stopping = False
        self._buffer = ""
        self.log_line.emit(self.vm.id, f"$ {self.plan.preview()}")
        for warning in self.plan.warnings:
            self.log_line.emit(self.vm.id, f"Aviso: {warning}")
        self.process.start()

    def stop(self, *, graceful: bool = True) -> None:
        """Pide apagado ordenado (QMP); cae a SIGTERM y luego SIGKILL."""
        if not self.is_running or self._finalized:
            return
        self._stopping = True
        if graceful:
            self.log_line.emit(
                self.vm.id, "Solicitando apagado ordenado (QMP system_powerdown)…"
            )
            self._qmp = _QmpPowerdown(self.qmp_path, self)
            self._qmp.done.connect(self._on_qmp_done)
            self._grace_timer.start(GRACEFUL_TIMEOUT_MS)
        else:
            self._terminate()

    def force_kill(self) -> None:
        self._grace_timer.stop()
        self._kill_timer.stop()
        if self.is_running:
            self._stopping = True
            self.log_line.emit(self.vm.id, "Forzando SIGKILL a QEMU.")
            self.process.kill()

    def wait_for_finished(self, timeout_ms: int = 3000) -> bool:
        if not self.is_running:
            return True
        return self.process.waitForFinished(timeout_ms)

    # -- señales de QProcess ----------------------------------------------- #
    def _on_started(self) -> None:
        self.log_line.emit(self.vm.id, f"QEMU iniciado (PID {self.pid}).")
        self.state_changed.emit(self.vm.id, models.VMState.RUNNING)
        self.started.emit(self.vm.id)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if self._finalized or self._stopping:
            return
        message = self.process.errorString() or str(error)
        self.log_line.emit(self.vm.id, f"Error de QEMU: {message}")
        if error == QProcess.ProcessError.FailedToStart:
            self._finalize(-1, models.VMState.ERROR, message)

    def _on_finished(
        self, exit_code: int, exit_status: QProcess.ExitStatus
    ) -> None:
        state = models.VMState.STOPPED if exit_code == 0 else models.VMState.ERROR
        detail = (
            "salida normal"
            if exit_status == QProcess.ExitStatus.NormalExit
            else "proceso terminado por señal"
        )
        self._flush()
        self.log_line.emit(
            self.vm.id, f"QEMU terminó (código {exit_code}, {detail})."
        )
        # Una parada pedida por nosotros (QMP, SIGTERM o SIGKILL) es un final
        # esperado, no un error de la VM.
        if self._stopping:
            state = models.VMState.STOPPED
        self._finalize(exit_code, state, detail)

    def _finalize(self, exit_code: int, state: models.VMState, detail: str) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._grace_timer.stop()
        self._kill_timer.stop()
        self.qmp_path.unlink(missing_ok=True)
        self.state_changed.emit(self.vm.id, state)
        self.finished.emit(self.vm.id, exit_code, state)

    # -- QMP y señales ----------------------------------------------------- #
    def _on_qmp_done(self, ok: bool, detail: str) -> None:
        if not self.is_running:
            return
        if ok:
            self.log_line.emit(
                self.vm.id, "QMP aceptó system_powerdown; esperando al invitado."
            )
            return
        self.log_line.emit(
            self.vm.id, f"QMP no disponible ({detail}); enviando SIGTERM."
        )
        self._terminate()

    def _on_grace_timeout(self) -> None:
        if self.is_running:
            self.log_line.emit(
                self.vm.id, "El invitado no se apagó a tiempo; enviando SIGTERM."
            )
            self._terminate()

    def _terminate(self) -> None:
        if not self.is_running:
            return
        self.process.terminate()
        self._kill_timer.start(KILL_TIMEOUT_MS)

    def _on_kill_timeout(self) -> None:
        if self.is_running:
            self.log_line.emit(self.vm.id, "SIGTERM ignorado; enviando SIGKILL.")
            self.process.kill()

    # -- salida ------------------------------------------------------------ #
    def _drain(self) -> None:
        data = bytes(self.process.readAllStandardOutput())
        if not data:
            return
        self._buffer += data.decode("utf-8", errors="replace")
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            self._emit_line(line)

    def _flush(self) -> None:
        if self._buffer.strip():
            self._emit_line(self._buffer)
        self._buffer = ""

    def _emit_line(self, line: str) -> None:
        text = line.rstrip("\r")
        if text:
            self.log_line.emit(self.vm.id, text)


def format_log_line(vm_name: str, line: str) -> str:
    """Prefijo común para el panel de logs de la GUI."""
    stamp = datetime.now().strftime("%H:%M:%S")
    return f"[{stamp}] {vm_name}: {line}"


__all__ = ["GRACEFUL_TIMEOUT_MS", "KILL_TIMEOUT_MS", "VMRunner", "format_log_line"]
