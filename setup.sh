#!/usr/bin/env bash
#
# setup.sh — Instalación automatizada y ejecución de la suite de tests
# para vibecBox. Configura .venv con --system-site-packages y corre
# toda la suite de tests para validar la instalación en el host.
#
# Uso:
#   bash setup.sh
#
set -euo pipefail

###############################################################################
# Seguridad y portabilidad
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

cd "$PROJECT_DIR"

###############################################################################
# 1. Crear entorno virtual con --system-site-packages
###############################################################################

if [ ! -d ".venv" ]; then
    echo "Creando entorno virtual .venv con --system-site-packages..."
    python3 -m venv .venv --system-site-packages
else
    echo "El entorno virtual .venv ya existe. Saltando creación."
fi

###############################################################################
# 2. Activar .venv e instalar dependencias
###############################################################################

# shellcheck disable=SC1091
source .venv/bin/activate

echo "Instalando dependencias del proyecto..."
pip install --upgrade pip
pip install -r requirements.txt

###############################################################################
# 3. Verificar que los binarios críticos están disponibles
###############################################################################

echo "Verificando binarios críticos..."
if [ -x .venv/bin/python ]; then
    echo "Python de .venv disponible: .venv/bin/python"
else
    echo "ERROR: No se encontró python en .venv/bin/"
    exit 1
fi

###############################################################################
# 4. Ejecutar suite completa de tests
###############################################################################

echo ""
echo "=== Ejecutando test_core.py ==="
python3 test_core.py
TEST_CORE_EXIT=$?

echo ""
echo "=== Ejecutando test_qemu.py ==="
python3 test_qemu.py
TEST_QEMU_EXIT=$?

echo ""
echo "=== Ejecutando test_gui.py (modo offscreen) ==="
QT_QPA_PLATFORM=offscreen .venv/bin/python test_gui.py
TEST_GUI_EXIT=$?

echo ""
echo "=== Ejecutando test_runner.py (modo offscreen) ==="
QT_QPA_PLATFORM=offscreen .venv/bin/python test_runner.py
TEST_RUNNER_EXIT=$?

###############################################################################
# 5. Resumen final
###############################################################################

echo ""
echo "=== Resumen de tests ==="
echo "test_core.py       : salida $TEST_CORE_EXIT"
echo "test_qemu.py       : salida $TEST_QEMU_EXIT"
echo "test_gui.py        : salida $TEST_GUI_EXIT"
echo "test_runner.py     : salida $TEST_RUNNER_EXIT"

EXIT_CODE=0
[ "$TEST_CORE_EXIT" -ne 0 ] && EXIT_CODE=1
[ "$TEST_QEMU_EXIT" -ne 0 ] && EXIT_CODE=1
[ "$TEST_GUI_EXIT" -ne 0 ] && EXIT_CODE=1
[ "$TEST_RUNNER_EXIT" -ne 0 ] && EXIT_CODE=1

if [ "$EXIT_CODE" -eq 0 ]; then
    echo ""
    echo "✅  ¡Todos los tests pasaron! La instalación de vibecBox es válida."
else
    echo ""
    echo "❌  Algunos tests fallaron. Revisar la salida anterior para detalles."
    exit $EXIT_CODE
fi
