"""CLI unificada de Club-AutotalPlay.

Subcomandos:
    run         corre el daemon en foreground (systemd lo llama)
    status      imprime el estado que publica el daemon (sin re-probar)
    once        un solo ciclo de reconexion (para probar)
    install     instala el servicio systemd user  (M4)
    uninstall   remueve el servicio y restaura el anterior (M4)

El entry point de pyproject apunta aqui: `club-autotalplay`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import core, daemon


def _force_utf8():
    """Windows mojibakea acentos/em-dash en la consola; forzamos UTF-8.

    Sin esto, el volcado de `debug` sale ilegible justamente donde mas se
    necesita (el Location del portal, los headers).
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _cmd_run(args):
    return daemon.run(poll=args.poll)


def _cmd_once(args):
    core.setup_logging()
    action = daemon.run_once()
    print(f"ciclo: {action}")
    return 0


def _age(iso):
    """Devuelve la edad en segundos de un timestamp ISO, o None."""
    if not iso:
        return None
    try:
        from datetime import datetime
        return (datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return None


def _cmd_status(args):
    """Lee el state.json del daemon. Si no hay datos frescos, hace un probe."""
    state = core.load_state()
    updated = state.get("updated_at")
    age = _age(updated)
    live = args.live  # --live fuerza probe real

    if state and age is not None and age < core.POLL_SECONDS * 3 and not live:
        # datos frescos del daemon: no re-probar
        print(f"fuente       : daemon (state.json, hace {int(age)}s)")
        print(f"online       : {'si' if state.get('online') else 'no'}")
        print(f"probe        : {state.get('probe', '-')}")
        print(f"wifi         : {'conectado' if state.get('wifi_connected') else 'DESCONECTADO'} (dev={core.wifi_device() or '-'})")
        print(f"ssid         : {state.get('ssid') or '-'}")
        print(f"ultima accion: {state.get('last_action', '-')}")
        print(f"actualizado  : {updated}")
        for k in ("last_grant_ok", "last_grant_attempt", "last_bounce"):
            if state.get(k):
                print(f"{k:16}: {state[k]}")
        if state.get("pid"):
            print(f"pid           : {state['pid']}")
    else:
        if state and age is not None and age >= core.POLL_SECONDS * 3:
            print(f"(state.json viejo, hace {int(age)}s — probando la red en vivo)")
        else:
            print("(sin state.json del daemon — probando la red en vivo)")
        core.print_status()
    return 0


def _cmd_install(args):
    from . import install as install_mod
    return install_mod.install()


def _cmd_uninstall(args):
    from . import install as install_mod
    return install_mod.uninstall()


def _cmd_tray(args):
    """Lanza la app completa: daemon en background + icono de tray."""
    from . import tray
    tray.run()
    return 0


def _cmd_gui(args):
    """Solo la GUI (lector de estado); el daemon debe correr aparte o via tray."""
    from . import gui
    gui.run_app()
    return 0


def _cmd_debug(args):
    """Vuelca el estado de probe/portal/grant para capturar un corte.

    Redactado por defecto (IP/MAC/accname/cookies enmascarados); --full
    muestra el detalle crudo para diagnostico propio.
    """
    core.setup_logging(to_stdout=False)
    print(core.debug_dump(perform_grant=not args.dry, redact=not args.full))
    return 0


def main(argv=None):
    _force_utf8()
    ap = argparse.ArgumentParser(
        prog="club-autotalplay",
        description="Auto-reconnect para la red Club_Totalplay_WiFi",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="corre el daemon (foreground)")
    p_run.add_argument("--poll", type=int, default=core.POLL_SECONDS,
                       help="segundos entre chequeos")
    p_run.set_defaults(func=_cmd_run)

    p_once = sub.add_parser("once", help="un solo ciclo de reconexion")
    p_once.set_defaults(func=_cmd_once)

    p_status = sub.add_parser("status", help="estado del daemon (sin re-probar)")
    p_status.add_argument("--live", action="store_true",
                          help="fuerza un probe real de la red")
    p_status.set_defaults(func=_cmd_status)

    p_dbg = sub.add_parser("debug", help="diagnostico (probe+portal+grant) para capturar un corte")
    p_dbg.add_argument("--dry", action="store_true",
                       help="solo probe; no hace GET ni POST al portal")
    p_dbg.add_argument("--full", action="store_true",
                       help="sin redaccion: muestra IP/MAC/accname crudos")
    p_dbg.set_defaults(func=_cmd_debug)

    p_inst = sub.add_parser("install", help="instala el servicio systemd user (M4)")
    p_inst.set_defaults(func=_cmd_install)

    p_uninst = sub.add_parser("uninstall", help="remueve el servicio, restaura el anterior (M4)")
    p_uninst.set_defaults(func=_cmd_uninstall)

    # --- M2: app de escritorio ---
    p_tray = sub.add_parser("tray", help="app completa: daemon + icono de tray")
    p_tray.set_defaults(func=_cmd_tray)

    p_app = sub.add_parser("app", help="alias de 'tray' (lo que lanza el .desktop/AppImage)")
    p_app.set_defaults(func=_cmd_tray)

    p_gui = sub.add_parser("gui", help="solo la GUI (lector de estado/logs)")
    p_gui.set_defaults(func=_cmd_gui)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
