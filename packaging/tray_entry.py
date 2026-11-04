"""Entrada windowed para el .exe de bandeja (Windows) y la app (Linux).

A diferencia de `console_entry.py` (CLI, sin GUI), esta entrada arranca la
app completa: daemon en background + icono de system tray. En Windows,
pystray escoge el backend win32 (Shell_NotifyIcon) solo; en Linux, xorg.

- Sin argumentos (lo que usa el .lnk de autostart) -> tray.
- Con argumentos -> CLI. El tray se relanza a si mismo con `gui` para abrir
  el panel; sin esto el .exe ignoraba el argumento y abria OTRO tray.
"""
from __future__ import annotations

import sys

from club_autotalplay.cli import main


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["tray"]) or 0)
