"""Entrada windowed para el .exe de bandeja (Windows) y la app (Linux).

A diferencia de `console_entry.py` (CLI, sin GUI), esta entrada arranca la
app completa: daemon en background + icono de system tray. En Windows,
pystray escoge el backend win32 (Shell_NotifyIcon) solo; en Linux, xorg.

El .lnk de autostart apunta aqui SIN argumentos.
"""
from __future__ import annotations

import sys

from club_autotalplay.tray import run


if __name__ == "__main__":
    sys.exit(run() or 0)
