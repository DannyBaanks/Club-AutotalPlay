"""Icono de system tray (patron Sideloadly): corre el daemon + muestra estado.

Al lanzar la app se:
  1. arranca el daemon en un thread (auto-reconnect en segundo plano),
  2. se muestra un icono en el tray cuyo color refleja el estado:
       verde  = online
       ambar  = reconectando / captive
       rojo   = sin conexion
  3. CLIC en el icono = abre el panel (GUI). En escritorios donde pystray
     no tiene menu (backend xorg: sin PyGObject, el caso del AppImage en
     GNOME) el clic es lo UNICO que llega, por eso la accion por defecto
     debe abrir algo visible. El panel trae todas las opciones del CLI.
     Con menu (AppIndicator): Abrir panel, Reconectar, Diagnostico, Log, Salir.

Requiere pystray + Pillow (extra 'gui') y un backend de tray (en X11,
pystray usa AppIndicator/ayatana si esta disponible).
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time

from PIL import Image

from . import core, daemon
from .launch import open_path, repair_autostart, self_command
from .assets import icon_path as _icon_path

log = logging.getLogger("club_autotalplay.tray")


def _state_name(state):
    """Mapea el state.json a nombre de icono."""
    if not state:
        return "offline"
    if state.get("online"):
        return "online"
    if state.get("probe") == "captive" or state.get("last_action") in (
        "granted", "granted-cached", "granted-after-bounce", "bounced", "cooldown", "bounce-cooldown",
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
        self._diag_proc = None

    # --- daemon existente ---------------------------------------------------
    @staticmethod
    def _daemon_already_running():
        """¿Hay ya un daemon vivo (p.ej. el servicio systemd)?

        Sondea el LOCK DE VIDA del daemon (daemon.lock con flock/msvcrt),
        no el pid del state.json: tras un reboot el state queda "viejo" por
        definición y el guard viejo dejaba spawnear un segundo daemon aunque
        el servicio ya estuviera arrancando (pasó en el boot del 2026-10-13).
        El kernel libera el lock cuando el proceso muere: no hay ventana de
        estado estancado.
        """
        return daemon.daemon_alive()

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

    @staticmethod
    def _spawn(proc, *args):
        """Lanza `args` como proceso aparte salvo que `proc` siga vivo."""
        if proc is not None and proc.poll() is None:
            return proc  # ya abierto
        try:
            return subprocess.Popen(self_command(*args))
        except Exception as e:
            log.error("no pude abrir %s: %s", args, e)
            return proc

    def _open_gui(self, *args):
        """Abre el panel en un PROCESO aparte (una sola instancia).

        Antes se creaba tk.Tk() dentro del thread de pystray: Tkinter no es
        seguro fuera del thread principal y un segundo clic abria otra raiz.
        """
        self._gui_proc = self._spawn(self._gui_proc, "gui")

    def _open_diag(self, *args):
        # proceso propio: con el panel ya abierto, el diagnostico no se pierde
        self._diag_proc = self._spawn(self._diag_proc, "gui", "--diag")

    def _open_log(self, *args):
        open_path(core.LOG_PATH)

    def _quit(self, icon, item):
        self._stop.set()
        if self._icon is not None:
            self._icon.stop()

    # --- arranque ----------------------------------------------------------
    def run(self):
        import pystray
        from pystray import Menu, MenuItem

        core.setup_logging()
        repair_autostart()

        # el daemon: solo se spawnea si no hay otro vivo (servicio systemd).
        # si ya corre uno, el tray solo lo observa (lee su state.json).
        if self._daemon_already_running():
            log.info("daemon ya corriendo (servicio); el tray solo lo observa")
        else:
            log.info("tray arrancando (daemon en background)")
            threading.Thread(target=self._daemon_thread, daemon=True, name="daemon").start()

        menu = Menu(
            MenuItem("Abrir panel", self._open_gui, default=True),
            MenuItem("Reconectar ahora", self._reconnect_now),
            MenuItem("Diagnóstico", self._open_diag),
            MenuItem("Abrir log completo", self._open_log),
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
