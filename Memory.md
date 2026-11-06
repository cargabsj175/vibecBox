# Memory.md — Roadmap y estado de vibecBox

> Fuente de verdad del progreso. Actualizar al cerrar cada tarea.
> Última actualización: 2026-11-06

## 0. Snapshot del host de desarrollo

| Dato | Valor |
|---|---|
| SO / arquitectura | Linux x86_64 |
| Python | 3.14.0 (`/usr/bin/python3`) |
| RAM total | 32 779 624 kB ≈ **32 011 MB** (31,3 GiB) |
| CPUs lógicas | **16** (`os.cpu_count()`) |
| `/dev/kvm` | presente, `crw-rw----+ root:kvm`; ACL concede r/w al usuario |
| `qemu-system-x86_64` | **PATH** `/usr/bin` (11.0.0, GTK/SDL/egl-headless/dbus); el gestionado queda como fallback |
| `qemu-img` | **PATH** `/usr/bin` (11.0.0) |
| `~/.local/share/vibecBox/bin/` | existe; fallback `static-pie` (headless) de la Etapa 1 |
| PyQt6 | **6.12.0** en `.venv` (creado con `--system-site-packages`) |

Consecuencia: el PATH gana (QEMU de distro con GTK/SDL) y el directorio
gestionado queda como fallback. La GUI usa el Qt6 del venv; si la distro publica
un PyQt6 para Python 3.14, el venv lo verá gracias a `--system-site-packages`.

## 1. Etapas

### Etapa 1 — Core de descubrimiento `[COMPLETADA 2026-11-06]`

**Objetivo:** conocer el host y localizar QEMU sin GUI y sin dependencias externas.

Entregables:

- [x] `vibecBox_core.py` con `resolve_binary`, `check_kvm`, `get_ram_max`, `get_cores_max`.
- [x] `trigger_download()`: descarga atómica, validación ELF, `sha256` obligatorio,
  extracción de un solo miembro y `chmod 0o755`.
- [x] `test_core.py`: reporte legible, `--download`, exit code 0/1.
- [x] Fuente estática de prueba fijada por versión y sha256 (11.0.0.1).
- [x] Ejecutar `python3 test_core.py --download` y anexar la salida real.
- [x] Instalar QEMU de la distro (con GTK/SDL) para la Etapa 4.

**Aceptación:** el test detecta KVM, RAM y CPUs; resuelve ambos binarios por PATH
o por el directorio gestionado; sin trazas de excepción.

**Hallazgo (2026-11-06):** el build estático de prueba es `static-pie` real y
expone `kvm`, pero solo compila `-display none` y `curses`. No sirve para la
ventana nativa de la Etapa 4. Decisión: usarlo solo para el test de descarga;
para VMs reales, QEMU de la distro (GTK/SDL) o build propio con SDL.

**Evidencia de validación (2026-11-06):**

```text
$ python3 test_core.py --download
====================================================================================
vibecBox :: test_core — descubrimiento de host y binarios
====================================================================================
[OK  ] Python                  3.14.0 (/usr/bin/python3)
[OK  ] Plataforma              Linux-7.2.0-n27-x86_64-with-glibc2.44
[OK  ] Backend sin GUI         vibecBox_core no importa PyQt6/PySide6
[OK  ] Directorio gestionado   /home/cargabsj175/.local/share/vibecBox/bin (se creará en la primera descarga)
[OK  ] KVM                     /dev/kvm | grupo=kvm | char=sí r=sí w=sí
[OK  ] RAM máxima              32011 MB (31.3 GiB)
[OK  ] CPUs lógicas            16 hilos (os.cpu_count)
[OK  ] qemu-system-x86_64      descargado: /home/cargabsj175/.local/share/vibecBox/bin/qemu-system-x86_64 | QEMU emulator version 11.0.0
[OK  ] qemu-img                descargado: /home/cargabsj175/.local/share/vibecBox/bin/qemu-img | qemu-img version 11.0.0
[WARN] Vídeo QEMU              backends: curses, none (sin GTK/SDL: no abre ventana; usa QEMU de la distro)
------------------------------------------------------------------------------------
Resultado: 9 OK · 1 WARN · 0 FAIL
Aviso: el QEMU resuelto es headless (sin GTK/SDL). Instala QEMU de la distro para la ventana de la Etapa 4.
Estado: todo listo. Binarios resueltos y KVM disponible.
```

Comprobación posterior de los artefactos instalados:

```text
-rwxr-xr-x 1 cargabsj175 cargabsj175 31204448 qemu-system-x86_64  (static-pie, ELF x86-64, 755)
-rwxr-xr-x 1 cargabsj175 cargabsj175  3376704 qemu-img           (static-pie, ELF x86-64, 755)
```

Y `python3 test_core.py` sin `--download` los resuelve como `managed:`, sin red.

**Resolución (2026-11-06):** se instaló QEMU de la distro (`/usr/bin`, 11.0.0).
El primer arranque falló por `libnuma.so.1` ausente; tras instalarla, `ldd` no
reporta librerías faltantes y `-display help` expone
`none, gtk, sdl, egl-headless, curses, dbus`. El PATH gana al directorio
gestionado, así que el pipeline de descarga queda como fallback y la ventana
nativa (Etapa 4) ya es posible.

```text
$ python3 test_core.py
[OK  ] qemu-system-x86_64      PATH: /usr/bin/qemu-system-x86_64 | QEMU emulator version 11.0.0
[OK  ] qemu-img                PATH: /usr/bin/qemu-img | qemu-img version 11.0.0
[OK  ] Vídeo QEMU              backends: curses, dbus, egl-headless, gtk, none, sdl (ventana nativa GTK/SDL)
------------------------------------------------------------------------------------
Resultado: 10 OK · 0 WARN · 0 FAIL
Estado: todo listo. Binarios resueltos y KVM disponible.
```

### Etapa 2 — GUI en PyQt6 `[COMPLETADA 2026-11-06]`

**Objetivo:** clonar la UX de VirtualBox sin lógica de virtualización en la vista.

- [x] `QMainWindow` con toolbar, lista de VMs, panel de detalles y menús.
- [x] Modelo `VMConfig` (nombre, RAM MB, vCPUs, disco, ISO, red, vídeo).
- [x] Asistente "Nueva VM" en pasos + validación contra `get_ram_max()`/`get_cores_max()`.
- [x] Persistencia JSON en `~/.local/share/vibecBox/vms/`.
- [x] Estados de VM: `stopped`, `running`, `paused`, `error`.

**Restricción:** el backend sigue siendo testeable sin PyQt6. Cumplida:
`test_core.py` verifica por AST que `vibecBox_core.py` y `models.py` no importan
PyQt6/PySide6, y se ejecuta con el Python del sistema (sin el venv).

**Entregables nuevos:** `models.py` (modelo y persistencia puros),
`vibecBox_gui.py` (ventana, asistente y diagnóstico), `test_gui.py` (smoke test
offscreen), `requirements.txt`, `.venv/` y `.gitignore`.

**Evidencia (2026-11-06):**

```text
$ QT_QPA_PLATFORM=offscreen .venv/bin/python test_gui.py
[OK  ] Entorno aislado              /tmp/vibecbox-gui-…
[OK  ] VM válida                    sin errores
[OK  ] Validación rechaza           5 errores detectados
[OK  ] Nombre duplicado             Ya existe una VM llamada «prueba etapa 2».
[OK  ] ISO inexistente              La ISO no existe: /no/existe.iso
[OK  ] Escritura atómica            …json modo 600
[OK  ] Sin temporales               0 restos
[OK  ] Round-trip JSON              Prueba Etapa 2
[OK  ] Qt                           6.12.0
[OK  ] HostInfo                     RAM 32011 MB · 16 CPUs · KVM sí
[OK  ] Lista de VMs                 1 elemento(s)
[OK  ] Selección inicial            primera VM seleccionada
[OK  ] Panel de detalles            Prueba Etapa 2
[OK  ] Asistente nombre libre       página completa
[OK  ] Asistente nombre duplicado   bloqueado correctamente
[OK  ] Asistente configuración      2048 MB · 2 vCPU · user
[OK  ] Eliminar y refrescar         0 elemento(s)
Resultado: 17 OK · 0 WARN · 0 FAIL
```

```text
$ python3 test_core.py
[OK  ] Backend sin GUI         vibecBox_core.py, models.py sin PyQt6/PySide6
Resultado: 10 OK · 0 WARN · 0 FAIL
```

La ventana real también se abrió en Wayland/XWayland (`timeout 6
.venv/bin/python vibecBox_gui.py` → exit 124, sin errores en el log), y se
generó una captura offscreen del layout con tres VMs de ejemplo.

**Pendiente fuera de alcance:** los estados son visuales; las transiciones
reales (`running`/`paused`/`error`) llegan con el ciclo de vida de la Etapa 4.
El botón «Iniciar» valida el vídeo GTK/SDL y avisa de que el lanzamiento es de
la Etapa 4.

### Etapa 3 — Configuración VirtIO `[COMPLETADA 2026-11-06 · asset de datos pendiente]`

**Objetivo:** exprimir el host con la configuración correcta de QEMU.

- [x] `build_args(VMConfig)`: `-machine q35`, `-accel kvm`, `-cpu host`,
      `-smp`, `-m`, `virtio-blk`, `virtio-net`, `virtio-vga`, `virtio-rng`.
- [x] Creación de discos con `qemu-img create -f qcow2` (atómica e idempotente).
- [x] Arranque desde ISO y desde disco; orden de boot configurable.
- [x] Fallback TCG explícito y avisado si `/dev/kvm` no está disponible.
- [x] Detección de `share/qemu` y paso de `-L` (distro vs binario gestionado).
- [ ] Empaquetar el asset `qemu-system-data-linux-amd64-11.0.0.1.tar.gz`
      (18,19 MB, sha256
      `27e5a04a32d56783ebf8277140ec52304dd2376f97a1c01c36982bd228f37cfc`)
      para el fallback gestionado. Con el QEMU de distro no hace falta.
- [x] Tests unitarios de argumentos (sin lanzar QEMU): `test_qemu.py`.

**Aceptación:** el plan de arranque lo acepta el QEMU real y el invitado usa
VirtIO. Verificado el 2026-11-06 en dos niveles:

1. `test_qemu.py` → 22 OK, 0 FAIL, sin lanzar ninguna VM.
2. Prueba de integración manual: el comando generado con `-S -display none`
   siguió vivo a los 4 s con `accel=kvm`, sin errores de dispositivos.

**Límite:** no se ha instalado un SO invitado real (requiere una ISO), así que
el arranque completo hasta el escritorio queda como verificación de la Etapa 4.

**Evidencia (2026-11-06):**

```text
$ python3 test_qemu.py
[OK  ] Plan KVM completo           12/12 argumentos
[OK  ] Programa y acelerador       qemu-system-x86_64 · kvm
[OK  ] Fallback TCG                accel=tcg · cpu=max
[OK  ] Órdenes de arranque         disk=c · cdrom=d · network=n
[OK  ] ISO en CD SCSI              scsi-cd sobre virtio-scsi
[OK  ] Red puente avisada          requiere qemu-bridge-helper con permisos
[OK  ] Vídeo no soportado          Modelo de vídeo no soportado: 'qxl'
[OK  ] Datos de distro             sin -L (QEMU usa su prefijo)
[OK  ] Datos gestionados           -L …/share/qemu
[OK  ] Disco qcow2 creado          prueba.qcow2 · 1074135040 B
[OK  ] qemu-img info               format=qcow2 · virtual-size=1073741824
[OK  ] Creación idempotente        mismo fichero, sin recrear
Resultado: 22 OK · 0 WARN · 0 FAIL
```

```text
accel: kvm | avisos: 0
ACEPTADO: QEMU seguía arrancado a los 4 s (detenido con -S)
disco: prueba-de-arranque-898a05b1.qcow2 1074135040 bytes
```

**Hallazgo:** este QEMU (11.0.0 de NeonatoX) **no compila QXL**; por eso se
retiró del modelo y del asistente en lugar de generar un `-device qxl` inválido.
`virtio-vga`, `virtio-gpu` y `VGA` sí están disponibles.

### Etapa 4 — Lanzamiento con QProcess `[COMPLETADA 2026-11-06]`

**Objetivo:** delegar la ventana a QEMU y controlar el ciclo de vida.

- [x] `QProcess` con `program=qemu-system-x86_64` y `plan_launch(...).args`.
- [x] Crear el disco con `qemu.create_disk(vm)` antes de lanzar (idempotente).
- [x] Backend de vídeo GTK (`gtk,window-close=on`) o SDL: QEMU posee la ventana.
- [x] Exigir `gtk`/`sdl` con `-display help`; abortar con mensaje claro si el
      binario resuelto es headless.
- [x] Captura de `stdout/stderr` (canales fusionados) en un dock de logs.
- [x] Apagado ordenado QMP `system_powerdown` → `SIGTERM` → `SIGKILL`, con
      limpieza del socket QMP y parada forzada confirmada por el usuario.
- [x] Detección de errores de arranque (`FailedToStart`, KVM, permisos, imagen
      bloqueada) sin bloquear el hilo de UI.
- [x] Transiciones reales de estado (`running`/`stopped`/`error`) según QProcess.
- [x] Reconciliación al arrancar: ningún estado «vivo» sobrevive a un cierre.

**Aceptación:** iniciar/detener una VM desde la GUI sin VNC/SPICE y sin bloquear
el hilo de UI.

**Verificado el 2026-11-06:**

1. `test_runner.py` → **9 OK · 0 WARN · 0 FAIL**: arranque real sin ventana
   (`-display none`), QMP presente, parada ordenada con caída a `SIGTERM`,
   `SIGKILL` forzado, `FailedToStart → ERROR` y limpieza del socket.
2. Integración con ventana real: `VMRunner(display="gtk,window-close=on")`
   arrancó QEMU/KVM, la ventana GTK siguió viva a los 3 s y el proceso terminó
   con estado `stopped` al forzar la parada.
3. `test_gui.py` → **21 OK · 0 FAIL**: la GUI carga el dock de logs, las
   acciones de apagado, la reconciliación de estados y el bloqueo de
   iniciar/editar/eliminar mientras la VM corre.

**Evidencia (2026-11-06):**

```text
$ QT_QPA_PLATFORM=offscreen .venv/bin/python test_runner.py
[OK  ] Argumentos QMP           -monitor none · -qmp unix:…,server=on,wait=off
[OK  ] QEMU iniciado            PID 28575 · QMP presente
[OK  ] Parada ordenada          estado final stopped
[OK  ] Ruta QMP/SIGTERM         Solicitando apagado ordenado (QMP system_powerdown)…
[OK  ] Transiciones de estado   running → stopped
[OK  ] Limpieza del socket      QMP eliminado
[OK  ] Forzar parada            SIGKILL · estado stopped
[OK  ] Fallo de arranque        FailedToStart → ERROR
[OK  ] Formato de log           [12:56:51] Mi VM: hola QEMU
Resultado: 9 OK · 0 WARN · 0 FAIL
```

```text
started: True pid: 28747 running: True
sigue vivo a los 3 s: True
finished: [(9, <VMState.STOPPED: 'stopped'>)]
QEMU iniciado (PID 28747).
Forzando SIGKILL a QEMU.
QEMU terminó (código 9, proceso terminado por señal).
```

**Límite:** el invitado de prueba no tiene SO instalado; la verificación cubre
el proceso, la ventana GTK, QMP y las transiciones, no el apagado ordenado
dentro de un Linux invitado (el camino se ejercita con la caída a `SIGTERM`).

## 2. Backlog / riesgos

- Build propio estático con SDL2 (Alpine/musl) si se exige cero instalación;
  el build de terceros actual no sirve para la ventana de la Etapa 4.
- Permisos de `/dev/kvm` varían por distro (grupo `kvm` vs ACL) → mensaje de ayuda claro.
- HiDPI y escalado de la ventana GTK/SDL no lo controla Qt → documentar.
- Audio (PipeWire/PulseAudio) y red (user-mode vs bridge) pendientes de diseño.
- SPICE: solo visor externo opcional, nunca widget integrado.

## 3. Decisiones (ADR ligero)

- **ADR-001 — Backend puro y sin dependencias.** `vibecBox_core.py` usa solo stdlib
  para poder testearse en cualquier host y evitar acoplar el descubrimiento a Qt.
- **ADR-002 — Descarga opt-in.** `resolve_binary()` no descarga por defecto:
  una GUI no debe bloquearse ni sorprender al usuario con red en el primer arranque.
- **ADR-003 — Rutas XDG con override.** `~/.local/share/vibecBox/bin/` por defecto,
  `VIBECBOX_DATA_HOME`/`VIBECBOX_BIN_DIR` para tests y empaquetado.
- **ADR-004 — Vídeo nativo de QEMU.** GTK/SDL en proceso hijo; cero captura de
  framebuffer en Qt (requisito de rendimiento y de alcance).
- **ADR-005 — Fuente estática fijada por versión y sha256.** Se usa
  `hermeticbuild/qemu-prebuilt` 11.0.0.1 solo para probar el pipeline de
  descarga; el checksum vive en el código y es obligatorio. Se descarta como
  fuente de producción porque no compila GTK/SDL.
- **ADR-006 — Comprobar capacidades, no solo existencia.** El backend no se
  limita a `which`: interroga `--version` y `-display help`. Un QEMU headless
  es un WARN explícito, nunca un fallo silencioso en la GUI.
- **ADR-007 — Venv con `--system-site-packages`.** La GUI vive en `.venv` para
  no tocar el Python del sistema, pero mantiene visible Qt/PyQt6 de la distro:
  si aparece el paquete oficial para 3.14, basta desinstalar el wheel del venv.
  El mínimo exigido es Qt 6.4 y se comprueba al arrancar.
- **ADR-008 — Modelo separado de la vista.** `models.py` define y valida
  `VMConfig`, y persiste JSON atómico (modo 600) sin importar PyQt6. La GUI no
  construye argumentos de QEMU: solo edita el modelo.
- **ADR-009 — `-accel` en lugar de `-enable-kvm`.** Se usa `-accel kvm`
  (equivalente moderno, no deprecado) y `-cpu host`; si KVM no está disponible,
  `-accel tcg` con `-cpu max` y un aviso explícito en `BuildPlan.warnings`.
- **ADR-010 — No ofrecer dispositivos no compilados.** Este QEMU no incluye
  QXL; se retiró del modelo tras comprobar `-device help`. Los modelos de vídeo
  ofrecidos son solo los que el binario resuelto puede crear.
- **ADR-011 — Discos qcow2 atómicos e idempotentes.** `create_disk()` escribe
  un `.part`, valida el magic `QFI\xfb`, hace `os.replace` y devuelve el disco
  existente si ya es válido. Así el arranque de la Etapa 4 puede llamarlo
  siempre sin duplicar trabajo.
- **ADR-012 — Apagado en cascada con QMP.** `VMRunner.stop()` pide
  `system_powerdown` por QMP (socket propio en `data/run/`), espera 12 s y cae a
  `SIGTERM`; si el proceso sigue vivo 5 s después, `SIGKILL`. La parada forzada
  manual va directa a `SIGKILL`, siempre con confirmación en la GUI.
- **ADR-013 — Una parada pedida no es un error.** El runner marca `_stopping`
  antes de QMP/SIGTERM/SIGKILL; un código de salida distinto de cero por esas
  señales se presenta como `stopped`. Solo un fallo no solicitado
  (`FailedToStart` o caída inesperada) queda en `error`.

## 4. Próximos pasos

1. ~~Crear los archivos de memoria y el código de Etapa 1.~~ Hecho.
2. ~~Ejecutar `python3 test_core.py --download` y anexar salida.~~ Hecho.
3. ~~Instalar QEMU de la distro (GTK/SDL).~~ Hecho (libnuma resuelta).
4. ~~Instalar PyQt6 y abrir la Etapa 2.~~ Hecho (PyQt6 6.12 en `.venv`).
5. ~~Etapa 3: `build_args(VMConfig)` + creación de discos con `qemu-img`.~~ Hecho.
6. ~~Etapa 4: `QProcess`, ventana GTK/SDL y ciclo de vida real.~~ Hecho.
7. Empaquetar el asset `share/qemu` solo si se quiere el fallback sin distro.
8. Probar una ISO real de instalación (Linux invitado) de extremo a extremo.
