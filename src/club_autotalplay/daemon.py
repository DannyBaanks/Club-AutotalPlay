"""Daemon del auto-reconnect: corre el ciclo en loop y publica estado.

El estado se escribe a `core.STATE_PATH` (JSON) para que la GUI/tray (M2)
pueda leerlo sin re-probar la red.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
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

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

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
