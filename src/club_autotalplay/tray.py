"""Icono de system tray (patron Sideloadly): corre el daemon + muestra estado.

Al lanzar la app se:
  1. arranca el daemon en un thread (auto-reconnect en segundo plano),
  2. se muestra un icono en el tray cuyo color refleja el estado:
       verde  = online
       ambar  = reconectando / captive
       rojo   = sin conexion
  3. el menu contextual da: Reconectar ahora, Ver estado, Salir.

Requiere pystray + Pillow (extra 'gui') y un backend de tray (en X11,
pystray usa AppIndicator/ayatana si esta disponible).
"""
from __future__ import annotations

import logging
import threading
import time

from PIL import Image

from . import core, daemon
from .assets import icon_path as _icon_path

log = logging.getLogger("club_autotalplay.tray")


def _state_name(state):
    """Mapea el state.json a nombre de icono."""
    if not state:
        return "offline"
    if state.get("online"):
        return "online"
    if state.get("probe") == "captive" or state.get("last_action") in (
        "granted", "granted-after-bounce", "bounced", "cooldown", "bounce-cooldown",
    ):
        return "reconnecting"
    return "offline"


def _title(state):
    if not state:
        return "Club-AutotalPlay — sin datos"
    ssid = state.get("ssid") or core.SSID
    if state.get("online"):
        return f"Club-AutotalPlay — {ssid} (en línea)"
    if state.get("probe") == "captive":
        return f"Club-AutotalPlay — {ssid} (reconectando)"
    return f"Club-AutotalPlay — {ssid} (sin conexión)"


class TrayApp:
    def __init__(self):
        self._stop = threading.Event()
        self._icon = None
        self._gui_proc = None

    # --- daemon existente ---------------------------------------------------
    @staticmethod
    def _daemon_already_running():
        """¿Hay ya un daemon vivo (p.ej. el servicio systemd)?

        Mira el state.json: si es fresco y su pid sigue vivo, otro daemon
        (servicio) ya esta trabajando — el tray no debe spawnearear otro.
        """
        state = core.load_state()
        pid = state.get("pid")
        if not pid:
            return False
        updated = state.get("updated_at")
        if updated:
            try:
                from datetime import datetime
                age = (datetime.now().astimezone() - datetime.fromisoformat(updated)).total_seconds()
            except Exception:
                age = None
            if age is None or age > core.POLL_SECONDS * 3:
                return False  # estado viejo: el pid no sirve
        # NO usar os.kill(pid, 0) aqui: en Windows eso NO sondea, llama a
        # TerminateProcess y MATARIA al daemon en vez de preguntar si vive
        # (verificado: victim murio con rc=0xC000013A). daemon._pid_alive usa
        # OpenProcess/GetExitCodeProcess en Windows y kill(pid,0) en POSIX.
        return daemon._pid_alive(pid)

    # --- threads -----------------------------------------------------------
    def _daemon_thread(self):
        # daemon.run es bloqueante; corre en este thread. Usa su propio loop.
        daemon.run(poll=core.POLL_SECONDS)

    def _watcher_thread(self):
        """Lee state.json y actualiza el icono del tray cada 2 s."""
        import pystray
        while not self._stop.is_set():
            state = core.load_state()
            name = _state_name(state)
            if self._icon is not None:
                try:
                    self._icon.icon = Image.open(_icon_path(name))
                    self._icon.title = _title(state)
                except Exception as e:
                    log.debug("no pude actualizar icono: %s", e)
            self._stop.wait(2.0)

    # --- acciones del menu -------------------------------------------------
    def _reconnect_now(self):
        # dispara un ciclo inmediato (el daemon tambien lo hace; el grant es
        # idempotente y hay cooldown en state.json)
        threading.Thread(target=daemon.run_once, daemon=True).start()

    def _open_gui(self):
        from . import gui
        gui.open_window()

    def _quit(self, icon, item):
        self._stop.set()
        if self._icon is not None:
            self._icon.stop()

    # --- arranque ----------------------------------------------------------
    def run(self):
        import pystray
        from pystray import Menu, MenuItem

        core.setup_logging()

        # el daemon: solo se spawnea si no hay otro vivo (servicio systemd).
        # si ya corre uno, el tray solo lo observa (lee su state.json).
        if self._daemon_already_running():
            log.info("daemon ya corriendo (servicio); el tray solo lo observa")
        else:
            log.info("tray arrancando (daemon en background)")
            threading.Thread(target=self._daemon_thread, daemon=True, name="daemon").start()

        menu = Menu(
            MenuItem("Reconectar ahora", self._reconnect_now, default=True),
            MenuItem("Ver estado / logs", self._open_gui),
            Menu.SEPARATOR,
            MenuItem("Salir", self._quit),
        )

        # icono inicial = offline hasta que el daemon publique estado
        image = Image.open(_icon_path("offline"))
        self._icon = pystray.Icon("club-autotalplay", image, "Club-AutotalPlay", menu)

        # watcher que pinta el estado real
        threading.Thread(target=self._watcher_thread, daemon=True, name="watcher").start()

        self._icon.run()  # bloqueante; corre el loop del tray


def run():
    TrayApp().run()
