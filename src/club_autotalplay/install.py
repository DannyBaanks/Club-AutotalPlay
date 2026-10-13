"""Instalador/desinstalador del servicio systemd user (M4).

Corte seguro para maquinas ajenas:
  install   -> escribe la unit apuntando al ejecutable PERSISTENTE correcto
               (AppImage/binario/python segun como se lanza), arranca el
               nuevo servicio, VERIFICA que quede activo, y solo entonces
               deshabilita el watchdog viejo. Si la verificacion falla,
               revierte todo al estado previo.
  uninstall -> detiene/desactiva el nuevo y restaura el viejo SOLO si
               existia en esta maquina (en instalaciones limpias nunca hubo).
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


def _unit_exists(unit):
    """True si la unit existe en systemd (aunque este deshabilitada)."""
    return _run(["systemctl", "--user", "cat", unit]).returncode == 0


def _exec_start():
    """(exe, args) robustos para ExecStart, incluso corriendo desde AppImage.

    - AppImage: `sys.executable` es el binario empaquetado en el montaje
      TEMPORAL del AppImage (/tmp/.mount_XXX) — usarlo en ExecStart romperia
      el servicio en el siguiente arranque. El AppImage mismo es el
      ejecutable persistente; su ruta llega en la env APPIMAGE.
    - Binario congelado fuera de AppImage (raro): usarlo directamente.
    - Desarrollo: python del venv con -m.
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.exists(appimage):
        return appimage, "run"
    if getattr(sys, "frozen", False):
        return sys.executable, "run"
    return sys.executable, "-m club_autotalplay.cli run"


def _unit_template():
    exe, args = _exec_start()
    # systemd parte ExecStart por espacios: si la ruta contiene espacios
    # (p.ej. "ISyCo Git" o "Descargas") hay que comillarla, o truena con
    # status=203/EXEC.
    return f"""[Unit]
Description=Club-AutotalPlay auto-reconnect
After=network.target NetworkManager.service

[Service]
Type=simple
ExecStart="{exe}" {args}
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
    exe, args = _exec_start()
    with open(path, "w") as f:
        f.write(_unit_template())
    print(f"  unit escrita: {path}")
    print(f'  ExecStart   : "{exe}" {args}')

    _run(["systemctl", "--user", "daemon-reload"], check=True)

    # 2. recordar el estado del viejo watchdog y pararlo SOLO para evitar
    #    doble-daemon mientras se prueba el nuevo (no se deshabilita todavia)
    old_enabled = _run(["systemctl", "--user", "is-enabled", OLD_UNIT]).returncode == 0
    old_active = _is_active(OLD_UNIT)
    if old_active:
        _run(["systemctl", "--user", "stop", OLD_UNIT])

    # 3. habilitar + arrancar el nuevo
    _run(["systemctl", "--user", "enable", "--now", NEW_UNIT], check=True)
    # dar un momento a systemd para pasar de 'activating' a 'active'
    import time
    for _ in range(10):
        if _is_active(NEW_UNIT):
            break
        time.sleep(0.5)

    # 4. VERIFICAR antes de migrar: si el nuevo no queda activo, rollback
    #    (el viejo vuelve exactamente a su estado previo) y salir con error.
    if not _is_active(NEW_UNIT):
        print("  ERROR: el servicio nuevo no quedo activo; revirtiendo", file=sys.stderr)
        _run(["systemctl", "--user", "disable", "--now", NEW_UNIT])
        if old_enabled:
            _run(["systemctl", "--user", "enable", "--now", OLD_UNIT])
        elif old_active:
            _run(["systemctl", "--user", "start", OLD_UNIT])
        print(f"  {OLD_UNIT}: estado previo restaurado", file=sys.stderr)
        print(f"  revisa: journalctl --user -u {NEW_UNIT}", file=sys.stderr)
        return 1

    # 5. verificado OK -> ahora si migrar: deshabilitar el viejo (no borrarlo)
    if old_enabled:
        _run(["systemctl", "--user", "disable", OLD_UNIT])
        print(f"  {OLD_UNIT}: desactivado (respaldo; la unit se conserva)")

    print(f"  {NEW_UNIT}: activo y habilitado")
    print("  estado: club-autotalplay status")
    print("  logs  : journalctl --user -u club-autotalplay -f")
    return 0


def uninstall():
    if core.IS_WINDOWS:
        return _win_uninstall()
    print(f"== desinstalando {NEW_UNIT} ==")

    _run(["systemctl", "--user", "disable", "--now", NEW_UNIT])
    print(f"  {NEW_UNIT} detenido y desactivado")

    _run(["systemctl", "--user", "daemon-reload"])

    # restaurar el viejo SOLO si existe en esta maquina: en una instalacion
    # limpia de un tercero nunca existio clubwifi-watchdog
    if _unit_exists(OLD_UNIT):
        _run(["systemctl", "--user", "enable", "--now", OLD_UNIT])
        if _is_active(OLD_UNIT):
            print(f"  {OLD_UNIT} restaurado y activo")
        else:
            print(f"  aviso: {OLD_UNIT} no quedo activo", file=sys.stderr)
        print("  listo: el watchdog viejo vuelve a ser el responsable")
    else:
        print(f"  (no hay {OLD_UNIT} en esta maquina; nada que restaurar)")
        print("  listo: el auto-reconnect quedo removido")
    return 0
