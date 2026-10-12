"""Localizacion robusta de los assets (iconos).

Busca el directorio de iconos en varios sitios, en orden:
  1. junto al paquete (`src/club_autotalplay/assets`)  -> mejor para
     empaquetado (AppImage/PyInstaller, donde el paquete viaja entero).
  2. la raiz del repo (`assets/`)                      -> en desarrollo.
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def assets_dir():
    candidates = [
        os.path.join(_HERE, "assets"),                 # interno al paquete
        os.path.join(_HERE, "..", "..", "assets"),     # raiz del repo (dev)
    ]
    for c in candidates:
        c = os.path.abspath(c)
        if os.path.isdir(c):
            return c
    return os.path.abspath(candidates[0])


def icon_path(state_name="online", size=48):
    return os.path.join(assets_dir(), f"icon_{state_name}_{size}.png")
