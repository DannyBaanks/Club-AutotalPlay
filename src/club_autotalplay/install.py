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
    kwargs = {}
    if core.IS_WINDOWS:
        # CREATE_NO_WINDOW: evita que powershell/systemctl abran consola
        # visible si el padre es windowed (la bandeja).
        kwargs["creationflags"] = 0x08000000
    r = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
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


# --- Windows: autostart via acceso directo en la carpeta de inicio ---------
# No hay systemd en Windows. El equivalente user-friendly es un .lnk en
# `shell:startup`: es visible en Administrador de tareas > Apps de inicio,
# el usuario puede desactivarlo, y no requiere privilegios.


def _startup_dir():
    return os.path.join(
        os.environ.get("APPDATA") or os.path.expanduser("~"),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )


def _win_shortcut_path():
    return os.path.join(_startup_dir(), "Club-AutotalPlay.lnk")


def _win_autostart_target():
    """(target, args) para el .lnk.

    Si el .exe congelado es el de bandeja, apunta a el sin args.
    Si se corrio desde el .exe de consola, busca el .exe de bandeja junto a
    el (mismo folder de distribucion); si no esta, usa el actual pero con el
    subcomando `tray` para que arranque la app completa igual.
    """
    if getattr(sys, "frozen", False):
        here = os.path.dirname(sys.executable)
        tray = os.path.join(here, "Club-AutotalPlayTray.exe")
        if os.path.exists(tray):
            return tray, ""
        # solo hay el .exe de consola: lanzarlo sin args solo imprime la ayuda
        return sys.executable, "tray"
    # desarrollo: python -m club_autotalplay.cli tray
    return sys.executable, "-m club_autotalplay.cli tray"


def _ps_quote(s):
    """Comilla para PowerShell (single-quote; ' se duplica)."""
    return "'" + s.replace("'", "''") + "'"


def _win_create_shortcut():
    target, args = _win_autostart_target()
    lnk = _win_shortcut_path()
    workdir = os.path.dirname(target)
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        + _ps_quote(lnk) + "); "
        "$s.TargetPath = " + _ps_quote(target) + "; "
        "$s.Arguments = " + _ps_quote(args) + "; "
        "$s.WorkingDirectory = " + _ps_quote(workdir) + "; "
        "$s.Save()"
    )
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])


def _win_install():
    print("== instalando autostart (Windows) ==")
    target, args = _win_autostart_target()
    print(f"  target : {target}")
    print(f"  args   : {args or '(ninguno)'}")
    print(f"  lnk    : {_win_shortcut_path()}")
    r = _win_create_shortcut()
    if r.returncode != 0:
        print(f"  ERROR: powershell rc={r.returncode}: {r.stderr.strip()}", file=sys.stderr)
        return 1
    if not os.path.exists(_win_shortcut_path()):
        print("  ERROR: el acceso directo no quedo creado", file=sys.stderr)
        return 1
    print("  OK: la app (bandeja + daemon) arrancara al iniciar sesion")
    print("  para probarlo ahora: cierra la app y vuelve a abrirla, o reinicia")
    return 0


def _win_uninstall():
    print("== desinstalando autostart (Windows) ==")
    lnk = _win_shortcut_path()
    try:
        os.remove(lnk)
        print(f"  eliminado: {lnk}")
    except FileNotFoundError:
        print("  (no habia acceso directo; nada que hacer)")
    print("  listo: ya no arrancara sola al iniciar sesion")
    return 0


def install():
    if core.IS_WINDOWS:
        return _win_install()
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
    if core.IS_WINDOWS:
        return _win_uninstall()
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
