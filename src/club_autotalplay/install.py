"""Instalador/desinstalador del servicio systemd user (se completa en M4).

M4 hara el corte limpio: instalara `club-autotalplay.service` apuntando al
core nuevo, desactivara el watchdog viejo (`clubwifi-watchdog.service`) como
respaldo, y `uninstall` restaurara el viejo.
"""
from __future__ import annotations

import sys


def install():
    print("install: pendiente de M4 (corte del servicio systemd user).", file=sys.stderr)
    print("Mientras, el watchdog viejo (~/.local/bin/clubwifi-watchdog.py) sigue activo.", file=sys.stderr)
    return 2


def uninstall():
    print("uninstall: pendiente de M4.", file=sys.stderr)
    return 2
