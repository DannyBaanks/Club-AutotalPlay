"""GUI minima de Club-AutotalPlay (patron Sideloadly: ventana chica con opciones).

Muestra:
  - indicador de estado (verde/ambar/rojo) + ssid + online
  - timestamp del ultimo grant
  - boton "Reconectar ahora"
  - tail de los ultimos eventos (lee el log del core)
  - toggle "Iniciar con el sistema" (autostart via ~/.config/autostart)

Se puede abrir desde el tray ("Ver estado / logs"). No bloquea el daemon:
el daemon corre en su propio proceso/thread.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

from . import core, daemon

log = logging.getLogger("club_autotalplay.gui")

AUTOSTART_DESKTOP = os.path.expanduser("~/.config/autostart/club-autotalplay.desktop")
_COLORS = {"online": "#22c55e", "reconnecting": "#eab308", "offline": "#ef4444"}


def _state_name(state):
    if not state:
        return "offline"
    if state.get("online"):
        return "online"
    if state.get("probe") == "captive" or state.get("last_action") in (
        "granted", "granted-cached", "granted-after-bounce", "bounced", "cooldown", "bounce-cooldown",
    ):
        return "reconnecting"
    return "offline"


def _read_log_tail(n=12):
    try:
        with open(core.LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception as e:
        return f"(sin log todavía: {e})"


def _autostart_enabled():
    return os.path.exists(AUTOSTART_DESKTOP)


def _set_autostart(enabled):
    if enabled:
        os.makedirs(os.path.dirname(AUTOSTART_DESKTOP), exist_ok=True)
        # apunta al launcher del sistema (script en PATH o AppImage)
        exec_line = _autostart_exec()
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Club-AutotalPlay\n"
            "Comment=Auto-reconnect Club_Totalplay_WiFi\n"
            f"Exec={exec_line}\n"
            "Icon=club-autotalplay\n"
            "Terminal=false\n"
            "Categories=Network;\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        with open(AUTOSTART_DESKTOP, "w") as f:
            f.write(content)
    else:
        try:
            os.remove(AUTOSTART_DESKTOP)
        except FileNotFoundError:
            pass


def _autostart_exec():
    """Que comando lanzar al arrancar la sesion.

    Prefiere el AppImage si existe (M3); si no, el launcher del venv.
    """
    appimage = os.path.expanduser("~/.local/bin/Club-AutotalPlay.AppImage")
    if os.path.exists(appimage):
        return appimage
    # venv del repo (desarrollo)
    venv_bin = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".venv", "bin", "club-autotalplay")
    )
    if os.path.exists(venv_bin):
        return f"{venv_bin} app"
    return "club-autotalplay app"


class GuiWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Club-AutotalPlay")
        self.root.resizable(False, False)
        self._stop = threading.Event()
        self._build()
        self._refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self.root, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")

        # fila: indicador + estado
        self.dot = tk.Canvas(frm, width=16, height=16, highlightthickness=0)
        self.dot.grid(row=0, column=0, padx=(0, 8), pady=(0, 6))
        self.dot_oval = self.dot.create_oval(2, 2, 14, 14, fill=_COLORS["offline"], outline="")

        self.status_lbl = ttk.Label(frm, text="Cargando…", font=("Sans", 11, "bold"))
        self.status_lbl.grid(row=0, column=1, sticky="w", pady=(0, 6))

        self.ssid_lbl = ttk.Label(frm, text="")
        self.ssid_lbl.grid(row=1, column=0, columnspan=2, sticky="w", **pad)

        self.grant_lbl = ttk.Label(frm, text="")
        self.grant_lbl.grid(row=2, column=0, columnspan=2, sticky="w", **pad)

        # boton reconectar
        btn = ttk.Button(frm, text="Reconectar ahora", command=self._reconnect)
        btn.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

        # autostart toggle
        self.autostart = tk.BooleanVar(value=_autostart_enabled())
        ttk.Checkbutton(
            frm, text="Iniciar con el sistema", variable=self.autostart,
            command=self._toggle_autostart,
        ).grid(row=4, column=0, columnspan=2, sticky="w", **pad)

        # log tail
        ttk.Label(frm, text="Últimos eventos:").grid(row=5, column=0, columnspan=2, sticky="w", **pad)
        self.log_txt = tk.Text(frm, height=10, width=58, state="disabled",
                              font=("Monospace", 8), background="#111", foreground="#ddd")
        self.log_txt.grid(row=6, column=0, columnspan=2, sticky="ew", **pad)

    def _set_log(self, text):
        self.log_txt.configure(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.insert("1.0", text)
        self.log_txt.configure(state="disabled")

    def _refresh(self):
        state = core.load_state()
        name = _state_name(state)
        self.dot.itemconfigure(self.dot_oval, fill=_COLORS[name])
        labels = {"online": "En línea", "reconnecting": "Reconectando…", "offline": "Sin conexión"}
        self.status_lbl.configure(text=labels[name])
        ssid = (state.get("ssid") or core.SSID) if state else core.SSID
        wifi = "conectado" if (state and state.get("wifi_connected")) else "desconectado"
        self.ssid_lbl.configure(text=f"Red: {ssid}  ·  WiFi {wifi}  ·  dev {core.DEV}")
        grant = (state or {}).get("last_grant_ok")
        self.grant_lbl.configure(text=f"Última sesión otorgada: {grant or '—'}")
        self._set_log(_read_log_tail(12))
        self.root.after(2000, self._refresh)

    def _reconnect(self):
        threading.Thread(target=daemon.run_once, daemon=True).start()

    def _toggle_autostart(self):
        try:
            _set_autostart(self.autostart.get())
        except Exception as e:
            log.error("autostart fallo: %s", e)

    def _on_close(self):
        self._stop.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def open_window():
    """Abre la GUI. Si ya hay una ventana, la trae al frente."""
    # corre en el thread principal de tkinter
    GuiWindow().run()


def run_app():
    """Punto de entrada 'app': GUI (y opcionalmente el daemon si no corre)."""
    open_window()
