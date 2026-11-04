"""GUI minima de Club-AutotalPlay (patron Sideloadly: ventana chica con opciones).

Muestra:
  - indicador de estado (verde/ambar/rojo) + ssid + online
  - timestamp del ultimo grant
  - boton "Reconectar ahora" (con el resultado del ciclo visible)
  - boton "Diagnóstico" (= `debug --dry`: probe/portal sin POST, redactado)
  - boton "Abrir log completo"
  - tail de los ultimos eventos (lee el log del core)
  - toggle "Iniciar con el sistema" (autostart via ~/.config/autostart)

Se puede abrir desde el tray ("Ver estado / logs"). No bloquea el daemon:
el daemon corre en su propio proceso/thread.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk

from . import core, daemon
from .launch import autostart_enabled as _autostart_enabled
from .launch import autostart_exec as _autostart_exec  # noqa: F401 (tests)
from .launch import open_path, repair_autostart
from .launch import set_autostart as _set_autostart

log = logging.getLogger("club_autotalplay.gui")

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


class GuiWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Club-AutotalPlay")
        self.root.resizable(False, False)
        self._stop = threading.Event()
        # resultados de threads de trabajo: Tk solo se toca desde el thread
        # principal (_drain_pending); los workers solo escriben este dict.
        self._pending = {}
        self._workers = []
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

        # acciones (las del CLI: once / debug --dry / log)
        bar = ttk.Frame(frm)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)
        for i in range(3):
            bar.columnconfigure(i, weight=1)
        self.reconnect_btn = ttk.Button(bar, text="Reconectar ahora", command=self._reconnect)
        self.reconnect_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.diag_btn = ttk.Button(bar, text="Diagnóstico", command=self._diagnose)
        self.diag_btn.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(bar, text="Abrir log completo", command=self._open_log).grid(
            row=0, column=2, sticky="ew", padx=(4, 0))
        self.action_lbl = ttk.Label(frm, text="")
        self.action_lbl.grid(row=7, column=0, columnspan=2, sticky="w", **pad)

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
        self._drain_pending()
        self.root.after(2000, self._refresh)

    def _drain_pending(self):
        if "reconnect" in self._pending:
            action = self._pending.pop("reconnect")
            self.action_lbl.configure(text=f"Reconectar: {_ACTION_TEXT.get(action, action)}")
            self.reconnect_btn.state(["!disabled"])
        if "diag" in self._pending:
            self._show_text("Diagnóstico", self._pending.pop("diag"))
            self.diag_btn.state(["!disabled"])
            self.action_lbl.configure(text="")

    def _run_bg(self, key, fn):
        def work():
            try:
                self._pending[key] = fn()
            except Exception as e:
                self._pending[key] = f"error: {e}"
        # NO daemon: si se cierra la ventana a media reconexion (entre
        # `radio wifi off` y `on`), el proceso espera a que el ciclo termine
        # en vez de matarlo y dejar el WiFi apagado.
        t = threading.Thread(target=work, daemon=False)
        t.start()
        self._workers.append(t)
        self.root.after(300, self._poll_pending)

    def _poll_pending(self):
        """Sondeo corto mientras haya un worker vivo; se apaga solo."""
        self._drain_pending()
        self._workers = [t for t in self._workers if t.is_alive()]
        if (self._workers or self._pending) and not self._stop.is_set():
            self.root.after(300, self._poll_pending)

    def _reconnect(self):
        self.reconnect_btn.state(["disabled"])
        self.action_lbl.configure(text="Reconectando…")
        self._run_bg("reconnect", daemon.run_once)

    def diagnose(self):
        self._diagnose()

    def _diagnose(self):
        self.diag_btn.state(["disabled"])
        self.action_lbl.configure(text="Diagnosticando… (unos segundos)")
        self._run_bg("diag", lambda: core.debug_dump(perform_grant=False, redact=True))

    def _open_log(self):
        open_path(core.LOG_PATH)

    def _show_text(self, title, text):
        win = tk.Toplevel(self.root)
        win.title(f"Club-AutotalPlay — {title}")
        txt = tk.Text(win, width=100, height=30, font=("Monospace", 9),
                      background="#111", foreground="#ddd")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.configure(state="disabled")
        ttk.Button(win, text="Copiar", command=lambda: (
            self.root.clipboard_clear(), self.root.clipboard_append(text))).pack(pady=6)

    def _toggle_autostart(self):
        try:
            _set_autostart(self.autostart.get())
        except Exception as e:
            log.error("autostart fallo: %s", e)

    def _on_close(self):
        self._stop.set()
        if any(t.is_alive() for t in self._workers):
            log.info("panel cerrado con un ciclo en curso; se termina antes de salir")
        self.root.destroy()  # los workers no-daemon terminan su ciclo

    def run(self):
        self.root.mainloop()


_ACTION_TEXT = {
    "online": "ya estabas en línea",
    "granted": "sesión otorgada",
    "granted-cached": "sesión otorgada (sin tocar el radio)",
    "bounced": "WiFi re-boteado, en línea",
    "busy": "el daemon ya está en un ciclo; espera unos segundos",
    "ssid-unknown": "no pude verificar la red WiFi",
    "other-network": "estás en otra red; no toco nada",
    "failed": "no se pudo reconectar (revisa Diagnóstico)",
}


def open_window(diag=False):
    """Abre la GUI (thread principal de tkinter)."""
    repair_autostart()
    win = GuiWindow()
    if diag:
        win.root.after(100, win.diagnose)
    win.run()


def run_app(diag=False):
    """Punto de entrada 'gui': el panel (el daemon corre aparte)."""
    open_window(diag=diag)
