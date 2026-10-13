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
import os
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
        log.info("tray arrancando (daemon en background)")

        # el daemon corre en background, salvo que ya haya otro vivo (asi no
        # quedan dos daemons peleando por el cooldown en state.json)
        if daemon.is_running():
            log.info("daemon ya corriendo; el tray solo lee el estado")
        else:
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
