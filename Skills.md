# Skills.md — Habilidades conseguidas

> Regla: solo se registra una capacidad cuando está implementada **y verificada**
> (comando reproducible + resultado). Formato: capacidad · evidencia · decisión.

## Capacidades verificadas

### Descubrimiento dinámico de binarios — 2026-11-06

- **Evidencia:** `python3 test_core.py --download` instala ambos binarios en
  `~/.local/share/vibecBox/bin/`; la ejecución posterior sin `--download` los
  resuelve como `managed:` sin tocar la red. No hay rutas hardcodeadas.
- **Decisión:** `shutil.which()` primero para respetar la distro y el PATH del
  usuario; fallback gestionado para instalaciones portables; descarga solo bajo
  autorización explícita (ADR-002). Evita secuestrar la instalación del sistema.

### Descarga estática verificada (pipeline) — 2026-11-06

- **Evidencia:** se descargaron `qemu-system-bin-linux-amd64-x86_64-softmmu-11.0.0.1.tar.gz`
  (5 818 414 B) y `qemu-img-linux-amd64-11.0.0.1.tar.gz` (1 513 590 B), se validó
  su sha256 fijado, se extrajo solo `bin/<binario>`, se comprobó el magic ELF y
  se instalaron con permisos `755`. Resultado: QEMU 11.0.0 y `qemu-img` 11.0.0,
  ambos `static-pie`.
- **Decisión:** checksum obligatorio, extracción por miembro concreto (nunca
  `extractall`), `os.replace` atómico y validación ELF antes de ejecutar.
- **Límite detectado:** el build estático no trae GTK/SDL; se añadió la sonda
  `-display help` para no prometer una ventana que QEMU no puede abrir.

### Detección de KVM sin abrir el dispositivo — 2026-11-06

- **Evidencia:** `test_core.py` reporta `/dev/kvm`, `char=sí`, `r/w=sí`,
  grupo `kvm`, incluso con acceso concedido por ACL sin pertenencia al grupo.
- **Decisión:** `stat()` + `os.access()` en lugar de `open()`: no altera el
  estado del dispositivo, funciona sin privilegios y da diagnóstico accionable.

### Lectura de RAM desde /proc/meminfo — 2026-11-06

- **Evidencia:** `32011 MB` calculados desde `MemTotal` (32 779 624 kB),
  coherente con la capacidad del host.
- **Decisión:** parser estricto con regex y excepción tipada, sin `psutil`:
  cero dependencias y control total del redondeo (MB enteros).

### Conteo de CPUs lógicas — 2026-11-06

- **Evidencia:** `16 hilos`, idéntico a `nproc` y `os.cpu_count()`.
- **Decisión:** `os.cpu_count()` con suelo de 1; el backend no reserva hilos.
  La reserva para el host se decidirá en `VMConfig` (Etapa 2/3), no aquí.

### Sonda de capacidades de vídeo — 2026-11-06

- **Evidencia:** con el QEMU de distro (`/usr/bin`, 11.0.0), `-display help`
  devuelve `none, gtk, sdl, egl-headless, curses, dbus` y `test_core.py` pasa a
  `[OK] Vídeo QEMU … (ventana nativa GTK/SDL)`. Con el binario estático de la
  Etapa 1 devolvía `curses, none` y se marcaba `WARN`, como se documentó.
- **Decisión:** comprobar capacidades, no solo existencia (ADR-006). El resultado
  depende del binario resuelto, no de una suposición sobre la distro.

### Ventana nativa GTK/SDL disponible — 2026-11-06

- **Evidencia:** `python3 test_core.py` → `10 OK · 0 WARN · 0 FAIL`, con
  `qemu-system-x86_64` resuelto por PATH y ambos backends de ventana presentes.
- **Decisión:** el PATH tiene prioridad; el binario gestionado (headless) solo
  actúa como fallback si la distro no está instalada.

### GUI PyQt6 con UX VirtualBox — 2026-11-06

- **Evidencia:** `QT_QPA_PLATFORM=offscreen .venv/bin/python test_gui.py` →
  `17 OK · 0 WARN · 0 FAIL`; la ventana real arranca en Wayland/XWayland
  (`timeout 6 .venv/bin/python vibecBox_gui.py` → seguía abierta, log vacío).
- **Decisión:** `QMainWindow` + toolbar + lista + panel de detalles + asistente
  por pasos. La vista solo edita `VMConfig`; no construye argumentos de QEMU ni
  captura framebuffers (ADR-008). En la Etapa 4 el botón «Iniciar» se conectó a
  `runner.VMRunner` y se añadieron apagar, forzar y el dock de logs.

### Modelo y persistencia JSON atómica — 2026-11-06

- **Evidencia:** el round-trip guarda/carga nombre, RAM y estado; el fichero
  queda en modo `600`, sin temporales `.vm-*`; nombres duplicados, ISO
  inexistente, RAM por encima del host y vCPUs fuera de rango se rechazan.
- **Decisión:** `models.py` sin PyQt6 (verificado por AST en `test_core.py`),
  una VM por fichero `<id>.json`, `tempfile` + `fsync` + `os.replace`.

### Validación contra capacidades reales del host — 2026-11-06

- **Evidencia:** el asistente limita RAM a `32011 MB` y vCPUs a `16`, valores
  leídos de `get_ram_max()`/`get_cores_max()`; no hay constantes hardcodeadas.
- **Decisión:** la UI no puede configurar una VM que el host no pueda ejecutar.

### Entorno de GUI aislado y migrable — 2026-11-06

- **Evidencia:** `.venv` con `--system-site-packages`, PyQt6 6.12.0 importable y
  `QT_QPA_PLATFORM=offscreen` para CI sin pantalla.
- **Decisión:** el backend sigue corriendo con el Python del sistema; la GUI usa
  el venv. Si la distro publica PyQt6 para 3.14, se desinstala el wheel del venv
  sin tocar código (ADR-007).

### Plan de arranque VirtIO verificado — 2026-11-06

- **Evidencia:** `python3 test_qemu.py` → `22 OK · 0 WARN · 0 FAIL`.
  El plan incluye `-machine q35`, `-accel kvm`, `-cpu host`, `-smp`, `-m`,
  `virtio-blk-pci`, `virtio-net-pci`, `virtio-vga`, `virtio-rng-pci` y el
  orden de arranque como `-boot order=c|d|n,menu=on`.
- **Evidencia de integración:** el comando generado, ejecutado con
  `-S -display none`, siguió vivo a los 4 s con `accel=kvm` y sin errores de
  dispositivos: QEMU acepta la línea completa.
- **Decisión:** `BuildPlan` separa programa, argumentos, acelerador y avisos;
  la GUI podrá mostrar la vista previa y los avisos sin lanzar nada. KVM se
  detecta en el host y el fallback TCG es explícito (ADR-009).

### Discos qcow2 atómicos — 2026-11-06

- **Evidencia:** `create_disk()` genera un qcow2 con magic `QFI\xfb` y
  `virtual-size` de 1 GiB según `qemu-img info`; la segunda llamada no recrea
  el disco y `force=True` lo sustituye de forma atómica.
- **Decisión:** escribir a `.part`, validar el magic y `os.replace`; nunca
  sobrescribir un disco válido sin `force` (ADR-011).

### Detección de dispositivos reales — 2026-11-06

- **Evidencia:** `-device help` de este QEMU no lista QXL; sí
  `virtio-vga`, `virtio-gpu-pci` y `VGA`. `qemu.BuildPlan` rechaza `qxl` y el
  asistente ya no lo ofrece.
- **Decisión:** no generar argumentos para dispositivos que el binario resuelto
  no compila (ADR-010); la lista del modelo se contrastó con el QEMU real.

### Datos de runtime localizados con `-L` — 2026-11-06

- **Evidencia:** con el QEMU de distro no se añade `-L` (usa su prefijo
  compilado); para un binario gestionado se detecta `bin/../share/qemu` o
  `VIBECBOX_QEMU_DATA` y se pasa `-L` explícito.
- **Decisión:** el fallback portátil no depende de rutas del sistema; el asset
  de datos solo hace falta si se usa ese fallback (pendiente de empaquetar).

### Lanzamiento real con QProcess y ventana GTK — 2026-11-06

- **Evidencia:** `VMRunner` arrancó `/usr/bin/qemu-system-x86_64` con KVM y
  `-display gtk,window-close=on`; el proceso siguió vivo a los 3 s con una
  ventana nativa y terminó en `stopped` al forzar la parada.
- **Decisión:** `QProcess` con canales fusionados y programa/argumentos
  separados (nunca una shell). La UI no captura el framebuffer: QEMU es dueño
  de la ventana (ADR-004). El disco se crea con `create_disk()` antes de
  lanzar y el botón de inicio se deshabilita mientras la VM corre.

### QMP y apagado en cascada — 2026-11-06

- **Evidencia:** `test_runner.py` conectó al socket QMP
  (`-qmp unix:…,server=on,wait=off`), envió `system_powerdown` y, sin SO
  invitado, cayó a `SIGTERM` dentro del plazo; `SIGKILL` forzado terminó el
  proceso y el socket se eliminó.
- **Decisión:** cascada QMP → 12 s → `SIGTERM` → 5 s → `SIGKILL` (ADR-012).
  `-monitor none` evita una consola interactiva; el único control es QMP.

### Estados derivados del proceso — 2026-11-06

- **Evidencia:** las señales `started`/`state_changed`/`finished` produjeron
  `running → stopped`; un binario inexistente produjo
  `FailedToStart → ERROR`; al abrir la GUI se reconciliaron estados viejos a
  `stopped`.
- **Decisión:** el estado persistido refleja el proceso, no una suposición.
  Una parada pedida por el usuario no se marca como error aunque QEMU salga
  con señal (ADR-013).

### Arranque seguro de binarios para diagnóstico — 2026-11-06

- **Evidencia:** `qemu-system-x86_64 --version`, `qemu-img --version` y
  `-display help` se capturan con timeout y se muestran en el informe sin
  bloquear el script.
- **Decisión:** `subprocess.run` con lista de argumentos, `shell=False` y
  timeout; nunca interpolar rutas en una shell.

## En validación

### Datos de runtime de QEMU (`share/qemu`)

- **Estado:** la detección y el paso de `-L` están implementados y probados. El
  QEMU de distro trae sus BIOS/keymaps en `/usr/share/qemu`; solo falta
  empaquetar el asset para el fallback gestionado.
