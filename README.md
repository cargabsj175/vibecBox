# vibecBox

**vibecBox** (*vivi is not VirtualBox*) es una GUI de escritorio para Linux que
imita la experiencia visual y el flujo de trabajo de VirtualBox, pero ejecuta
**QEMU/KVM nativo**. No es un frontend de libvirt, no es un cliente remoto y no
reemplaza a QEMU: lo orquesta con la mejor configuración posible para el host.

## Filosofía

Los principios no negociables de vibecBox son:

| # | Principio | Implicación |
|---|-----------|-------------|
| 1 | **Familiaridad con VirtualBox** | Misma jerarquía mental: lista de VMs, detalles, botones Nueva/Configuración/Iniciar/Apagar/Forzar y panel de logs. |
| 2 | **Rendimiento VirtIO** | `-machine q35`, `-accel kvm`, `-cpu host`, discos `virtio-blk`/`virtio-scsi`, red `virtio-net`, vídeo `virtio-vga`/`virtio-gpu`. |
| 3 | **Cero libvirt** | Sin daemon, sin XML, sin `virsh`, sin `libvirt-python`. Los argumentos se construyen en Python puro. |
| 4 | **Cero VNC/SPICE embebido** | La ventana de la VM la crea QEMU con backend **GTK** o **SDL**. vibecBox nunca captura el framebuffer ni dibuja un cliente remoto dentro de Qt. |
| 5 | **Descubrimiento dinámico de binarios** | Nunca rutas fijas. `shutil.which()` primero; fallback `~/.local/share/vibecBox/bin/`; descarga explícita como último recurso. |
| 6 | **Backend puro** | `vibecBox_core.py` y `models.py` no importan PyQt6, no abren ventanas y no imprimen por stdout. |
| 7 | **Cero privilegios** | vibecBox corre como usuario normal. Nunca `sudo`, salvo indicar al usuario cómo habilitar `/dev/kvm`. |

## Características

- **Rendimiento bruto VirtIO/KVM nativo**: configuración optimizada para QEMU/KVM sin capas intermedias.
- **Sin libvirt**: totalmente autónomo, sin daemones de sistema.
- **Sin VNC/SPICE**: la ventana es nativa de QEMU/GTK-SDL, sin framebuffer embebido.
- **Descubrimiento de binarios**: `shutil.which()` + directorio gestionado por el usuario.
- **Testing coverage**: suite completa de tests unitarios y de humo (Core, GUI, QemuPlan, QProcess Runner).

## Instalación rápida

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/vibecBox.git
cd vibecBox

# 2. Crear entorno virtual con site-packages del sistema
python3 -m venv .venv --system-site-packages

# 3. Instalar dependencias
.source .venv/bin/activate
pip install -r requirements.txt

# 4. Verificar el entorno (opcional)
python3 test_core.py           # descubre binarios y capacidades del host
QT_QPA_PLATFORM=offscreen .venv/bin/python test_gui.py  # modelo + GUI offscreen
python3 test_qemu.py           # argumentos VirtIO y discos qcow2 (sin lanzar VM)
QT_QPA_PLATFORM=offscreen .venv/bin/python test_runner.py  # QMP, SIGTERM/SIGKILL
```

## Correr los tests

La suite de tests se ejecuta desde el directorio raíz del proyecto:

```bash
# Tests que no requieren privilegios especiales
python3 test_core.py                    # valida descubrimiento de host y binarios
QT_QPA_PLATFORM=offscreen .venv/bin/python test_gui.py  # modelo + GUI offscreen
python3 test_qemu.py                    # argumentos QEMU y discos qcow2 (sin lanzar VM)
QT_QPA_PLATFORM=offscreen .venv/bin/python test_runner.py  # QProcess, QMP, ciclo de vida
```

**Notas importantes:**

- `test_core.py` requiere `/dev/kvm` disponible y operativo; si no está, marcará `WARN` pero no fallará críticamente.
- `test_runner.py` lanza QEMU con `-display none` (headless) y un disco sin SO invitado. No se abren ventanas.
- Todas las pruebas se ejecutan dentro de `.venv` para aislamiento completo.
- Si `/dev/kvm` no está accesible, sigue los pasos de `Agents.md` para agregar el usuario al grupo `kvm` y reiniciar la sesión.
