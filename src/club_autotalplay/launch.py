"""Relanzar la app, abrir archivos y autostart — solo stdlib.

Vive aparte de tray.py (que importa Pillow/pystray) para que la GUI y el
CLI funcionen sin el extra [gui] instalado.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger("club_autotalplay.launch")

AUTOSTART_DESKTOP = os.path.expanduser("~/.config/autostart/club-autotalplay.desktop")
APPIMAGE_DEFAULT = os.path.expanduser("~/.local/bin/Club-AutotalPlay.AppImage")


def self_exec():
    """Lista base [exe, ...] para relanzar ESTA app (antes del subcomando).

    - AppImage: `sys.executable` vive en el montaje temporal /tmp/.mount_XXX;
      el ejecutable persistente es el AppImage (env APPIMAGE). Misma regla
      que install._exec_start.
    - Congelado (.exe/PyInstaller): el propio binario. Ambos entry points
      (app_entry, tray_entry) pasan los argumentos al CLI.
    - Desarrollo: python -m club_autotalplay.cli
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.exists(appimage):
        return [appimage]
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "club_autotalplay.cli"]


def self_command(*args):
    return [*self_exec(), *args]


def open_path(path):
    """Abre un archivo con la app por defecto del sistema."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        log.error("no pude abrir %s: %s", path, e)


# --- autostart del icono (Linux, ~/.config/autostart) ----------------------

def autostart_exec():
    """Comando del autostart. Siempre con el subcomando `app`: el CLI exige
    uno y el AppImage sin argumentos sale con error. Rutas entre comillas:
    la del repo lleva espacios ("ISyCo Git")."""
    if os.path.exists(APPIMAGE_DEFAULT):
        return f'"{APPIMAGE_DEFAULT}" app'
    venv_bin = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".venv", "bin", "club-autotalplay")
    )
    if os.path.exists(venv_bin):
        return f'"{venv_bin}" app'
    return "club-autotalplay app"


def _autostart_content():
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Club-AutotalPlay\n"
        "Comment=Auto-reconnect Club_Totalplay_WiFi\n"
        f"Exec={autostart_exec()}\n"
        "Icon=club-autotalplay\n"
        "Terminal=false\n"
        "Categories=Network;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def autostart_enabled():
    return os.path.exists(AUTOSTART_DESKTOP)


def set_autostart(enabled):
    if enabled:
        os.makedirs(os.path.dirname(AUTOSTART_DESKTOP), exist_ok=True)
        with open(AUTOSTART_DESKTOP, "w") as f:
            f.write(_autostart_content())
    else:
        try:
            os.remove(AUTOSTART_DESKTOP)
        except FileNotFoundError:
            pass


def repair_autostart():
    """Reescribe un autostart EXISTENTE cuyo Exec no es el correcto.

    Versiones <= 0.1.6 escribian el AppImage sin `app` (no abria nada); la
    casilla seguia marcada y el usuario no se enteraba. Solo toca el archivo
    si ya existe (respeta un autostart desactivado). Devuelve True si reparo.
    """
    if sys.platform.startswith("win") or not autostart_enabled():
        return False
    try:
        with open(AUTOSTART_DESKTOP, encoding="utf-8", errors="replace") as f:
            current = f.read()
    except OSError:
        return False
    want = f"Exec={autostart_exec()}"
    if want in current.splitlines():
        return False
    try:
        set_autostart(True)
        log.info("autostart reparado: %s", want)
        return True
    except OSError as e:
        log.error("no pude reparar el autostart: %s", e)
        return False
