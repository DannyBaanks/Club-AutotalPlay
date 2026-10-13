"""Daemon del auto-reconnect: corre el ciclo en loop y publica estado.

El estado se escribe a `core.STATE_PATH` (JSON) para que la GUI/tray (M2)
pueda leerlo sin re-probar la red.

Exclusión mutua: cada ciclo (el del loop del daemon Y los manuales de
"Reconectar ahora" del tray/GUI/`once`) pasa por un lock de archivo
(`cycle.lock`). Así dos recuperaciones nunca compiten por state.json ni
por el portal (grant/bounce simultáneos).
"""
from __future__ import annotations

import contextlib
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

# Un ciclo con bounce puede tardar ~90s (radio 30s + verify 40s + margen).
LOCK_TIMEOUT = 120


def _cycle_lock_path():
    return os.path.join(core.STATE_DIR, "cycle.lock")


def _daemon_lock_path():
    return os.path.join(core.STATE_DIR, "daemon.lock")


# --- Lock de VIDA del daemon (reboot-proof) ---------------------------------
#
# El guard viejo (pid en state.json + frescura) falla SIEMPRE tras un reboot:
# el state.json queda viejo por definicion, asi que el tray spawnéa un segundo
# daemon aunque el servicio ya este arrancando. Evidencia real: boot del
# 2026-10-13, el service (pid 1838) y el tray (pid 4273) corrieron a la vez
# todo el dia pisándose state.json.
#
# Solución: flock/msvcrt sobre daemon.lock retenido por TODA la vida del
# proceso daemon. El kernel libera el lock cuando el proceso muere — no hay
# ventana de "estado viejo": tras un reboot nadie lo tiene, y el primero que
# llega (normalmente el servicio, que arranca en el login) es EL daemon.


def acquire_daemon_lock():
    """Toma el lock de vida. Devuelve el fh abierto (conservar vivo) o None."""
    os.makedirs(core.STATE_DIR, exist_ok=True)
    fh = open(_daemon_lock_path(), "a+")
    if core.IS_WINDOWS:
        import msvcrt
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return None
    else:
        import fcntl
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return None
    try:  # registrar el pid por legibilidad (el lock real es el flock)
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
    except Exception:
        pass
    return fh


def daemon_alive():
    """True si hay un daemon vivo (el lock de vida está tomado).

    Sondeo sin side-effects: intenta tomar el lock, lo suelta y reporta.
    """
    os.makedirs(core.STATE_DIR, exist_ok=True)
    fh = open(_daemon_lock_path(), "a+")
    try:
        if core.IS_WINDOWS:
            import msvcrt
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        else:
            import fcntl
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False
    finally:
        fh.close()


@contextlib.contextmanager
def _cycle_lock(timeout=LOCK_TIMEOUT):
    """Lock de archivo cross-platform (flock en POSIX, locking en Windows).

    Serializa los ciclos del daemon contra los manuales del tray/GUI/CLI.
    Si no se obtiene en `timeout` segundos, lanza TimeoutError.
    """
    os.makedirs(core.STATE_DIR, exist_ok=True)
    fh = open(_cycle_lock_path(), "a+")
    try:
        deadline = time.time() + timeout
        if core.IS_WINDOWS:
            import msvcrt
            while True:
                if time.time() >= deadline:
                    raise TimeoutError(f"ciclo ocupado (lock {timeout}s)")
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.5)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                # deadline ANTES de reintentar: si el sleep anterior se paso
                # del timeout, no volver a competir por el lock
                if time.time() >= deadline:
                    raise TimeoutError(f"ciclo ocupado (lock {timeout}s)")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    time.sleep(0.5)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


# Acciones del ciclo que ya garantizan online verificado (sin re-probar).
_ONLINE_ACTIONS = ("online", "granted", "granted-cached", "granted-after-bounce", "bounced")


def _refresh_state(state, action):
    """Agrega al state los campos derivados que consume la GUI."""
    # El ciclo ya dejo los timestamps (last_grant_ok, etc). Aqui agregamos
    # el estado 'fresco' del probe para que la GUI pinte en verde/rojo.
    if action in _ONLINE_ACTIONS:
        # el ciclo ya verifico online (verify_online o probe==online)
        st = "online"
    else:
        st, _ = core.probe()
    state["probe"] = st
    state["online"] = st == "online"
    state["last_action"] = action
    state["updated_at"] = core.now_iso()
    state["ssid"] = core.current_ssid()
    state["wifi_connected"] = core.wifi_connected()
    return state


def run_once(state=None):
    """Un ciclo del daemon: corre core.cycle y publica estado. Devuelve accion.

    Pasa por _cycle_lock: si otro ciclo corre (daemon o manual), espera
    hasta LOCK_TIMEOUT; si no lo obtiene devuelve "busy" sin tocar nada.
    """
    if state is None:
        state = core.load_state()
    try:
        with _cycle_lock(timeout=LOCK_TIMEOUT):
            action = core.cycle(state)
            _refresh_state(state, action)
            core.save_state(state)
            return action
    except TimeoutError:
        log.info("ciclo ocupado (otra recuperacion corriendo); se omite")
        return "busy"


def run(poll=None):
    """Loop infinito del daemon (bloqueante)."""
    if poll is None:
        poll = core.POLL_SECONDS
    core.setup_logging()

    # Lock de vida: solo UN daemon por maquina/usuario. Si ya hay uno
    # (p.ej. el servicio y el usuario abrio el tray), este proceso se
    # abstiene por completo: sale limpio sin tocar state.json.
    lock_fh = acquire_daemon_lock()
    if lock_fh is None:
        log.info("otro daemon ya corre (lock de vida tomado); este proceso solo observa")
        return 0

    state = core.load_state()
    state["pid"] = os.getpid()
    core.save_state(state)
    log.info("daemon iniciado (poll=%ss, ssid=%s, dev=%s, pid=%s)",
             poll, core.SSID, core.wifi_device() or "-", os.getpid())

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

    try:
        action = "unknown"
        while not stopping["flag"]:
            try:
                # Estado FRESCO del disco en cada ciclo: si otro proceso
                # escribio state.json entre ciclos (un run_once manual), no
                # lo pisamos con un dict viejo en memoria.
                action = run_once()
                if action != "online":
                    log.info("ciclo: %s", action)
            except Exception as e:
                log.exception("ciclo exception: %s", e)
            # Poll adaptativo: sonda rapida (5s) mientras la red no esta online;
            # poll largo (20s) solo cuando el ultimo ciclo quedo online.
            poll_eff = core.POLL_SECONDS if action in _ONLINE_ACTIONS else core.POLL_DOWN_SECONDS
            # duerme en cortos para reaccionar rapido a la senal de parada
            slept = 0.0
            while slept < poll_eff and not stopping["flag"]:
                time.sleep(0.5)
                slept += 0.5
    finally:
        try:
            lock_fh.close()  # libera el lock de vida
        except Exception:
            pass

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
    """True si ya hay un daemon vivo (lock de vida tomado; reboot-proof)."""
    return daemon_alive()
