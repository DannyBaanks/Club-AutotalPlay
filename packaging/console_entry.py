"""Entrada de consola para el .exe de Windows (CLI, sin GUI).

Corre la CLI con los argumentos que se pasen. Sin argumentos muestra la
ayuda. Soporta status / once / run (daemon). El tray/GUI de Windows es
trabajo futuro; por ahora el .exe entrega el auto-reconnect (daemon) y la
CLI, que es lo comprobable en CI.
"""
from __future__ import annotations

import sys

from club_autotalplay.cli import main


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
