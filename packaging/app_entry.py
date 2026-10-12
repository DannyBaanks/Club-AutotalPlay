"""Entrada PyInstaller para la app de escritorio.

- Sin argumentos (doble click / .desktop) -> corre el subcomando `app`
  (tray: daemon en background + icono).
- Con argumentos (`status`, `once`, ...) -> los pasa a la CLI, lo que
  permite probar el AppImage empaquetado sin abrir el tray.
"""
from __future__ import annotations

import sys

from club_autotalplay.cli import main


if __name__ == "__main__":
    argv = sys.argv[1:] or ["app"]
    sys.exit(main(argv))
