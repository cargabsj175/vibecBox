#!/usr/bin/env python3
"""Refresca las cachés del menú y de iconos tras una instalación real."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    # Los empaquetados con DESTDIR deben actualizar las cachés el gestor de
    # paquetes, no el script de instalación.
    if os.environ.get("DESTDIR"):
        return 0

    prefix = sys.argv[1]
    datadir = sys.argv[2]
    base = os.path.join(prefix, datadir)

    subprocess.run(
        ["update-desktop-database", os.path.join(base, "applications")],
        check=False,
    )
    subprocess.run(
        ["gtk-update-icon-cache", "-f", "-t", os.path.join(base, "icons", "hicolor")],
        check=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
