"""Daemon del auto-reconnect: corre el ciclo en loop y publica estado.

El estado se escribe a `core.STATE_PATH` (JSON) para que la GUI/tray (M2)
pueda leerlo sin re-probar la red.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

from . import core

log = logging.getLogger("club_autotalplay.daemon")

# Claves que la GUI puede leer para pintar el estado.
STATE_KEYS = (
    "online",
    "probe",
    "ssid",
    "wifi_connected",
    "last_action",
    "updated_at",
    "last_grant_ok",
    "last_grant_attempt",
    "last_bounce",
)


def _refresh_state(state, action):
    """Agrega al state los campos derivados que consume la GUI."""
    # El ciclo ya dejo los timestamps (last_grant_ok, etc). Aqui agregamos
    # el estado 'fresco' del probe para que la GUI pinte en verde/rojo.
    st, _ = core.probe()
    state["probe"] = st
    state["online"] = st == "online"
    state["last_action"] = action
    state["updated_at"] = core.now_iso()
    state["ssid"] = core.current_ssid()
    state["wifi_connected"] = core.wifi_connected()
    return state


def run_once(state=None):
    """Un ciclo del daemon: corre core.cycle y publica estado. Devuelve accion."""
    if state is None:
        state = core.load_state()
    action = core.cycle(state)
    _refresh_state(state, action)
    core.save_state(state)
    return action


def run(poll=None):
    """Loop infinito del daemon (bloqueante)."""
    if poll is None:
        poll = core.POLL_SECONDS
    core.setup_logging()
    state = core.load_state()
    state["pid"] = os.getpid()
    core.save_state(state)
    log.info("daemon iniciado (poll=%ss, ssid=%s, dev=%s, pid=%s)",
             poll, core.SSID, core.DEV, os.getpid())

    stopping = {"flag": False}

    def _stop(signum, frame):
        log.info("recibi senal %s, saliendo", signum)
        stopping["flag"] = True

    # signal solo funciona en el main thread (si el daemon corre desde el
    # tray en un thread, lo omitimos; ahi el paro es via proceso).
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    else:
        log.info("daemon en thread (sin handlers de senal); paro via proceso")

    while not stopping["flag"]:
        try:
            action = run_once(state)
            if action != "online":
                log.info("ciclo: %s", action)
        except Exception as e:
            log.exception("ciclo exception: %s", e)
        # duerme en cortos para reaccionar rapido a la senal de parada
        slept = 0.0
        while slept < poll and not stopping["flag"]:
            time.sleep(0.5)
            slept += 0.5

    log.info("daemon detenido")
    return 0


def _pid_alive(pid):
    """True si el pid sigue vivo (Windows via ctypes; POSIX via kill 0)."""
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if core.IS_WINDOWS:
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return bool(ok) and code.value == STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def is_running():
    """True si ya hay un daemon vivo publicando estado (evita doble-daemon)."""
    return _pid_alive(core.load_state().get("pid"))
