"""Instalador/desinstalador del servicio systemd user (M4).

Corte limpio:
  install   -> escribe `club-autotalplay.service` apuntando al core nuevo,
               desactiva el watchdog viejo (`clubwifi-watchdog.service`) como
               respaldo (no lo borra), y arranca el nuevo.
  uninstall -> detiene/desactiva el nuevo y restaura el viejo (respaldo).
"""
from __future__ import annotations

import os
import subprocess
import sys

from . import core

OLD_UNIT = "clubwifi-watchdog.service"
NEW_UNIT = "club-autotalplay.service"
UNIT_DIR = os.path.expanduser("~/.config/systemd/user")


def _run(cmd, check=False):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} fallo rc={r.returncode}: {r.stderr.strip()}")
    return r


def _unit_template():
    # sys.executable = el python del venv donde esta instalado el paquete.
    # systemd parte ExecStart por espacios: si la ruta contiene espacios
    # (p.ej. "ISyCo Git") hay que comillarla, o truena con status=203/EXEC.
    exec_start = f'"{sys.executable}" -m club_autotalplay.cli run'
    return f"""[Unit]
Description=Club-AutotalPlay auto-reconnect (core nuevo)
After=network.target NetworkManager.service

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def _is_active(unit):
    return _run(["systemctl", "--user", "is-active", unit]).returncode == 0


def install():
    print(f"== instalando {NEW_UNIT} ==")

    # 1. escribir la unit del core nuevo
    os.makedirs(UNIT_DIR, exist_ok=True)
    path = os.path.join(UNIT_DIR, NEW_UNIT)
    with open(path, "w") as f:
        f.write(_unit_template())
    print(f"  unit escrita: {path}")
    print(f"  ExecStart   : {sys.executable} -m club_autotalplay.cli run")

    _run(["systemctl", "--user", "daemon-reload"], check=True)

    # 2. desactivar el viejo como respaldo (no lo borramos)
    if _run(["systemctl", "--user", "is-enabled", OLD_UNIT]).returncode == 0:
        _run(["systemctl", "--user", "disable", OLD_UNIT])
        print(f"  {OLD_UNIT} desactivado (respaldo; la unit se conserva)")
    _run(["systemctl", "--user", "stop", OLD_UNIT])

    # 3. habilitar + arrancar el nuevo
    _run(["systemctl", "--user", "enable", "--now", NEW_UNIT], check=True)
    # dar un momento a systemd para pasar de 'activating' a 'active'
    import time
    for _ in range(10):
        if _is_active(NEW_UNIT):
            break
        time.sleep(0.5)
    if not _is_active(NEW_UNIT):
        print("  ERROR: el servicio nuevo no quedo activo", file=sys.stderr)
        print(f"  revisa: journalctl --user -u {NEW_UNIT}", file=sys.stderr)
        return 1

    print(f"  {NEW_UNIT}: activo y habilitado")
    print("  (el watchdog viejo queda desactivado como respaldo)")
    print("  estado: club-autotalplay status")
    print("  logs  : journalctl --user -u club-autotalplay -f")
    return 0


def uninstall():
    print(f"== desinstalando {NEW_UNIT} ==")

    _run(["systemctl", "--user", "disable", "--now", NEW_UNIT])
    print(f"  {NEW_UNIT} detenido y desactivado")

    # restaurar el viejo (respaldo)
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", OLD_UNIT])
    if _is_active(OLD_UNIT):
        print(f"  {OLD_UNIT} restaurado y activo")
    else:
        print(f"  aviso: {OLD_UNIT} no quedo activo", file=sys.stderr)

    print("  listo: el watchdog viejo vuelve a ser el responsable")
    return 0
