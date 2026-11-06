# Agents.md — Directrices de vibecBox

> Memoria permanente. Leer `Agents.md`, `Memory.md` y `Skills.md` al inicio de
> cada sesión. Si el código contradice este documento, gana el documento y se
> corrige el código (o se actualiza la decisión aquí, nunca en silencio).

## 1. Identidad del proyecto

**vibecBox** (*vivi is not VirtualBox*) es una GUI de escritorio para Linux que
imita la experiencia visual y el flujo de trabajo de VirtualBox, pero ejecuta
**QEMU/KVM nativo**. No es un frontend de libvirt, no es un cliente remoto y no
reemplaza a QEMU: lo orquesta con la mejor configuración posible para el host.

## 2. Principios no negociables

| # | Principio | Implicación práctica |
|---|---|---|
| 1 | **Familiaridad con VirtualBox** | Misma jerarquía mental: lista de VMs, detalles, botones Nueva/Configuración/Iniciar/Apagar/Forzar, asistentes paso a paso y panel de logs. |
| 2 | **Rendimiento VirtIO** | `-machine q35`, `-accel kvm`, `-cpu host`, discos `virtio-blk`/`virtio-scsi`, red `virtio-net`, vídeo `virtio-vga`/`virtio-gpu`. |
| 3 | **Cero libvirt** | Sin daemon, sin XML, sin `virsh`, sin `libvirt-python`. Los argumentos se construyen en Python puro. |
| 4 | **Cero VNC/SPICE embebido** | La ventana de la VM la crea QEMU con backend **GTK** o **SDL**. vibecBox nunca captura el framebuffer ni dibuja un cliente remoto dentro de Qt. |
| 5 | **Descubrimiento dinámico de binarios** | Nunca rutas fijas. `shutil.which()` primero; fallback `~/.local/share/vibecBox/bin/`; descarga explícita como último recurso. |
| 6 | **Backend puro** | `vibecBox_core.py` y `models.py` no importan PyQt6, no abren ventanas y no imprimen por stdout. |
| 7 | **Cero privilegios** | vibecBox corre como usuario normal. Nunca `sudo`, salvo el mensaje que indique al usuario cómo habilitar `/dev/kvm`. |

## 3. Resolución de binarios (contrato)

Orden obligatorio para `qemu-system-x86_64` y `qemu-img`:

1. Ruta explícita recibida por parámetro (si es ejecutable).
2. `shutil.which(nombre)` — respeta el PATH y la distro del usuario.
3. Directorio gestionado: `$XDG_DATA_HOME/vibecBox/bin/` o, por defecto,
   `~/.local/share/vibecBox/bin/` (override: `VIBECBOX_BIN_DIR`).
4. `trigger_download(nombre)`, solo si el llamador lo autoriza
   (`allow_download=True` o flag de GUI). Nunca descargar en silencio.

Reglas de descarga:

- URL por variable de entorno (`VIBECBOX_DOWNLOAD_URL_<BINARIO>`) o
  `DOWNLOAD_SOURCES`; sin URL configurada se falla con un mensaje accionable.
- Escritura atómica en el directorio destino, validación de magic ELF,
  `sha256` obligatorio y `chmod 0o755`.
- QEMU no publica binarios estáticos oficiales de sistema completo: se usa una
  fuente de terceros fijada (ver 3.1) solo para pruebas, o el paquete de la
  distro.

### 3.1 Fuente estática de prueba (fijada)

Para probar el pipeline de descarga sin depender de la distro:

- Repo: `hermeticbuild/qemu-prebuilt` (fork de `ziglang/qemu-static`,
  build Alpine/musl, `static-pie`, con acelerador KVM).
- Versión: **11.0.0.1**.
- `qemu-system-x86_64`: asset `…-x86_64-softmmu-11.0.0.1.tar.gz`,
  sha256 `31399af8d874176f104679c6aae8c8741bcd86283dbc8de1fce8a140f67f1448`.
- `qemu-img`: asset `…-11.0.0.1.tar.gz`,
  sha256 `4cab6e3f186ec6c500dd340f84b794f8320e3d58a2b2b9d2f835416589279d3e`.

Reglas:

- El sha256 es **obligatorio**; sin checksum no se instala nada.
- Solo se extrae el miembro `bin/<binario>` del `.tar.gz`; nunca `extractall`.
- Es una fuente **de prueba, no de producción**: este build **no incluye GTK
  ni SDL** (`-display none|curses`). Para la ventana de la Etapa 4 se usa el
  QEMU de la distro o un build propio con SDL/GTK.
- Cualquier URL alternativa exige `VIBECBOX_DOWNLOAD_SHA256_<BINARIO>`.

## 4. Arquitectura

```
vibecBox/
├── vibecBox_core.py   # backend puro (stdlib, sin GUI, sin prints)
├── models.py          # VMConfig, validación y persistencia JSON (puro)
├── vibecBox_gui.py    # PyQt6: MainWindow, asistentes, diagnóstico, dock de logs
├── test_core.py       # prueba de humo del backend (exit code 0/1)
├── qemu.py            # BuildPlan (args VirtIO) + discos qcow2 (puro)
├── runner.py          # QProcess, QMP y ciclo de vida (PyQt6 QtCore/QtNetwork)
├── test_gui.py        # smoke test del modelo + GUI en offscreen
├── test_qemu.py       # tests de argumentos y discos, sin lanzar la VM
├── test_runner.py     # arranque real headless: QMP, SIGTERM y SIGKILL
├── requirements.txt   # PyQt6>=6.4 (solo GUI)
├── Agents.md          # este archivo
├── Memory.md          # roadmap y estado
├── Skills.md          # capacidades verificadas
└── (futuro)
    └── gui/           # widgets adicionales si la ventana crece
```

## 5. Convenciones de código

- Python 3.11+ (`from __future__ import annotations`), type hints en toda API pública.
- `snake_case` para funciones/variables, `PascalCase` para clases, `Final` para constantes.
- `pathlib.Path` en lugar de concatenar strings; `os.access` para permisos.
- `logging` en el backend, `print` solo en scripts CLI (`test_core.py`).
- Excepciones propias (`VibecBoxError`, `BinaryNotFoundError`, `DownloadError`).
- `subprocess` siempre con lista de argumentos, `shell=False`, timeout explícito.
- Un cambio = una responsabilidad; si toca backend y GUI, separar en dos commits.

## 6. Seguridad y robustez

- No ejecutar binarios descargados sin comprobar ELF y, si se conoce, `sha256`.
- No seguir enlaces simbólicos al escribir en el directorio gestionado.
- No construir líneas de shell con datos de usuario: argumentos en lista.
- Errores de KVM: explicar grupo `kvm` / ACL de `/dev/kvm`, nunca fallar en silencio.
- El backend debe funcionar sin QEMU instalado: devuelve `None` y explica qué falta.
- Antes de prometer una VM con ventana, comprobar `-display help`: si no hay
  `gtk`/`sdl`, avisar y recomendar el QEMU de la distro.
- No generar argumentos para dispositivos que el binario resuelto no compile
  (p. ej. QXL en este host): verificar con `-device help` y rechazar con error
  claro en `qemu.plan_launch()`.
- Los discos se crean con `qemu-img` en un `.part`, se valida el magic qcow2 y
  se instalan con `os.replace`; nunca se sobrescribe uno válido sin `force`.
- QEMU se lanza con `QProcess` y lista de argumentos; nunca con una shell.
  La ventana es de QEMU (GTK/SDL), no un widget ni un framebuffer de Qt.
- El apagado va QMP `system_powerdown` → `SIGTERM` → `SIGKILL`; la parada
  forzada exige confirmación y una parada pedida no se registra como `error`.
- Al cerrar la GUI se matan y esperan los procesos hijos; al arrancar se
  reconcilian los estados persistidos a `stopped`.

## 7. Protocolo de memoria

1. Al empezar: leer los tres `.md` y ejecutar `python3 test_core.py`,
   `python3 test_qemu.py`, `QT_QPA_PLATFORM=offscreen .venv/bin/python
   test_gui.py` y `test_runner.py`.
2. Al terminar una funcionalidad **verificada**: añadir entrada en `Skills.md`
   con evidencia (comando + resultado) y decisión de diseño.
3. Al cambiar alcance o cerrar etapa: actualizar `Memory.md` (checkbox + fecha).
4. No borrar decisiones pasadas: moverlas a "Historial" si quedan obsoletas.

## 8. Definición de "hecho"

- El código corre en el host real, no solo "compila".
- `python3 test_core.py` termina con código 0 (o WARN justificado y documentado).
- `QT_QPA_PLATFORM=offscreen .venv/bin/python test_gui.py` termina con código 0.
- `python3 test_qemu.py` termina con código 0 sin lanzar una VM.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python test_runner.py` termina con
  código 0 y sin dejar procesos QEMU vivos.
- Sin imports de PyQt6 en el backend; sin `sudo`; sin libvirt; sin VNC/SPICE embebido.
- `Skills.md` refleja la nueva capacidad con evidencia reproducible.
