"""Tests de regresión del core: ciclo captive/down/grant, guard de radio,
redacción y escalamiento. Sin tocar la red real: probe/grant/nmcli mockeados.

Correr: .venv/bin/python -m pytest tests/ -v
"""
from __future__ import annotations

import pytest

from club_autotalplay import core


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CAPTIVE_LOC = (
    "https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp"
    "?wlanuserip=10.149.222.166&wlanacname="
    "&wlanparameter=50-2e-91-a1-f8-e4&accname=NEZA-TEST"
)
PORTAL_PARAMS = {
    "ip": "10.149.222.166",
    "mac": "50-2e-91-a1-f8-e4",
    "nombreAcc": "NEZA-TEST",
    "tipo": "DESKTOP",
}


def _fresh_state(**kw):
    st = {
        "fail_streak": 0,
        "last_grant_attempt": "",
        "last_bounce": "",
        "last_grant_ok": "",
    }
    st.update(kw)
    return st


# ---------------------------------------------------------------------------
# probe / parse
# ---------------------------------------------------------------------------

def test_parse_portal_params_ok():
    p = core.parse_portal_params(CAPTIVE_LOC)
    assert p == PORTAL_PARAMS


def test_parse_portal_params_incompleta():
    assert core.parse_portal_params("https://x/?solo=1") is None
    assert core.parse_portal_params(None) is None


def test_probe_online(monkeypatch):
    class R:
        def getcode(self):
            return 204
        def read(self):
            return b""
        headers = {}

    monkeypatch.setattr(core._opener, "open", lambda req, timeout: R())
    st, loc = core.probe()
    assert st == "online" and loc is None


def test_probe_captive(monkeypatch):
    def fake_open(req, timeout):
        raise core.urllib.error.HTTPError(
            "http://x", 302, "Found", {"Location": CAPTIVE_LOC}, None
        )
    monkeypatch.setattr(core._opener, "open", fake_open)
    st, loc = core.probe()
    assert st == "captive" and loc == CAPTIVE_LOC


def test_probe_down(monkeypatch):
    def fake_open(req, timeout):
        raise core.socket.timeout("timed out")
    monkeypatch.setattr(core._opener, "open", fake_open)
    st, loc = core.probe()
    assert st == "down" and loc is None


# ---------------------------------------------------------------------------
# redacción
# ---------------------------------------------------------------------------

def test_redact_params():
    out = core._redact_params(PORTAL_PARAMS)
    assert out["ip"] == "10.149.222.x"
    assert out["mac"] == "50-2e-91-xx-xx-xx"
    assert out["nombreAcc"] == "<ACC>"  # identificador de zona/AP
    # el original no se toca
    assert PORTAL_PARAMS["ip"] == "10.149.222.166"


def test_redact_location():
    out = core._redact_location(CAPTIVE_LOC)
    assert "10.149.222.x" in out
    assert "50-2e-91-xx-xx-xx" in out
    assert "NEZA-TEST" not in out  # accname tambien se enmascara


def test_redact_mac_solo_tres_octetos():
    assert core._redact_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:xx:xx:xx"


# ---------------------------------------------------------------------------
# ciclo: online
# ---------------------------------------------------------------------------

def test_ciclo_online_reseta_streak(monkeypatch):
    monkeypatch.setattr(core, "probe", lambda: ("online", None))
    state = _fresh_state(fail_streak=3)
    action = core.cycle(state)
    assert action == "online"
    assert state["fail_streak"] == 0


# ---------------------------------------------------------------------------
# ciclo: captive -> grant OK
# ---------------------------------------------------------------------------

def test_ciclo_captive_grant_ok(monkeypatch):
    calls = {"probe": 0}

    def fake_probe():
        calls["probe"] += 1
        # 1ª llamada del ciclo: captive; verify_online ve online
        return ("captive", CAPTIVE_LOC) if calls["probe"] == 1 else ("online", None)

    monkeypatch.setattr(core, "probe", fake_probe)
    monkeypatch.setattr(core, "current_ssid", lambda: core.SSID)
    monkeypatch.setattr(core, "grant_session", lambda p: True)
    monkeypatch.setattr(core, "verify_online", lambda deadline=0: True)
    monkeypatch.setattr(core, "maybe_autocapture", lambda tag="captive": None)
    monkeypatch.setattr(core, "save_state", lambda s: None)

    state = _fresh_state()
    action = core.cycle(state)
    assert action == "granted"
    assert state["fail_streak"] == 0
    assert state["last_grant_ok"]


# ---------------------------------------------------------------------------
# ciclo: captive -> grant falla -> bounce
# ---------------------------------------------------------------------------

def test_ciclo_captive_grant_falla_hace_bounce(monkeypatch):
    monkeypatch.setattr(core, "probe", lambda: ("captive", CAPTIVE_LOC))
    monkeypatch.setattr(core, "current_ssid", lambda: core.SSID)
    monkeypatch.setattr(core, "grant_session", lambda p: False)
    monkeypatch.setattr(core, "maybe_autocapture", lambda tag="captive": None)
    monkeypatch.setattr(core, "save_state", lambda s: None)
    monkeypatch.setattr(core, "wifi_connected", lambda: True)
    monkeypatch.setattr(core, "bring_up_wifi", lambda: True)
    # tras bounce: _try_grant_from_probe -> online
    monkeypatch.setattr(core, "_try_grant_from_probe", lambda s: True)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)  # sin sleeps reales

    state = _fresh_state()
    action = core.cycle(state)
    assert action == "bounced"


# ---------------------------------------------------------------------------
# ciclo: down -> bounce
# ---------------------------------------------------------------------------

def test_ciclo_down_bounce(monkeypatch):
    monkeypatch.setattr(core, "probe", lambda: ("down", None))
    monkeypatch.setattr(core, "save_state", lambda s: None)
    monkeypatch.setattr(core, "wifi_connected", lambda: False)
    monkeypatch.setattr(core, "current_ssid", lambda: "")
    monkeypatch.setattr(core, "bring_up_wifi", lambda: True)
    monkeypatch.setattr(core, "_try_grant_from_probe", lambda s: True)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    state = _fresh_state()
    action = core.cycle(state)
    assert action == "bounced"
    # bounce exitoso => streak se resetea (el log lo vio como 1 durante el ciclo)
    assert state["fail_streak"] == 0


# ---------------------------------------------------------------------------
# escalamiento: fail_streak >= FAIL_STREAK_ESCALATE ignora cooldowns
# ---------------------------------------------------------------------------

def test_escalada_ignora_cooldowns(monkeypatch):
    """Con streak >= 5 y cooldowns activos, igual hace bounce+grant."""
    monkeypatch.setattr(core, "probe", lambda: ("down", None))
    monkeypatch.setattr(core, "save_state", lambda s: None)
    monkeypatch.setattr(core, "wifi_connected", lambda: False)
    monkeypatch.setattr(core, "current_ssid", lambda: "")

    bounced = {"n": 0}

    def fake_bring():
        bounced["n"] += 1
        return True

    monkeypatch.setattr(core, "bring_up_wifi", fake_bring)
    monkeypatch.setattr(core, "_try_grant_from_probe", lambda s: True)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    # cooldowns "recientes": sin escalada NO deberia rebotear
    state = _fresh_state(
        fail_streak=core.FAIL_STREAK_ESCALATE,  # ya en el umbral
        last_bounce=core.now_iso(),            # cooldown activo
    )
    action = core.cycle(state)
    assert action == "bounced"
    assert bounced["n"] == 1  # escalada forzó el bounce pese al cooldown


def test_sin_escalada_respeta_cooldown_bounce(monkeypatch):
    monkeypatch.setattr(core, "probe", lambda: ("down", None))
    monkeypatch.setattr(core, "save_state", lambda s: None)
    monkeypatch.setattr(core, "wifi_connected", lambda: True)
    monkeypatch.setattr(core, "current_ssid", lambda: core.SSID)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    state = _fresh_state(fail_streak=1, last_bounce=core.now_iso())
    action = core.cycle(state)
    assert action == "bounce-cooldown"


# ---------------------------------------------------------------------------
# guard de radio: otra red => no tocar
# ---------------------------------------------------------------------------

def test_bring_up_wifi_no_toca_otra_red(monkeypatch):
    monkeypatch.setattr(core, "current_ssid", lambda: "OtraRed")
    monkeypatch.setattr(core, "IS_WINDOWS", False)

    def fail_radio(*a, **k):
        raise AssertionError("no debe ejecutar nmcli radio con otra red")

    monkeypatch.setattr(core, "_run", fail_radio)
    assert core.bring_up_wifi() is False


def test_bring_up_wifi_si_red_propia(monkeypatch):
    """Con nuestra red (o sin red), el bounce sí se ejecuta."""
    monkeypatch.setattr(core, "IS_WINDOWS", False)
    monkeypatch.setattr(core, "current_ssid", lambda: core.SSID)
    monkeypatch.setattr(core, "wifi_connected", lambda: True)
    runs = []

    def fake_run(cmd, timeout=15):
        runs.append(cmd)
        class R: returncode, stdout, stderr = 0, "ok", ""
        return R()

    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    assert core.bring_up_wifi() is True
    assert ["nmcli", "radio", "wifi", "off"] in runs


def test_bring_up_wifi_sin_red(monkeypatch):
    """Sin WiFi conectado, también puede rebotar (no hay red ajena que matar)."""
    monkeypatch.setattr(core, "IS_WINDOWS", False)
    monkeypatch.setattr(core, "current_ssid", lambda: "")
    monkeypatch.setattr(core, "wifi_connected", lambda: True)
    runs = []

    def fake_run(cmd, timeout=15):
        runs.append(cmd)
        class R: returncode, stdout, stderr = 0, "ok", ""
        return R()

    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    assert core.bring_up_wifi() is True


# ---------------------------------------------------------------------------
# wifi_device: detección dinámica
# ---------------------------------------------------------------------------

def test_wifi_device_detecta(monkeypatch):
    core.DEV = None  # reset cache

    def fake_run(cmd, timeout=15):
        class R: returncode, stderr = 0, ""
        R.stdout = (
            "wlo1:wifi:connected\n"
            "eth0:ethernet:connected\n"
            "wlp2s0:wifi:disconnected\n"
        )
        return R()

    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core, "IS_WINDOWS", False)
    assert core.wifi_device() == "wlo1"  # la conectada gana
    core.DEV = None


def test_wifi_device_cachea(monkeypatch):
    core.DEV = None
    calls = {"n": 0}

    def fake_run(cmd, timeout=15):
        calls["n"] += 1
        class R: returncode, stderr = 0, ""
        R.stdout = "wx0:wifi:connected\n"
        return R()

    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core, "IS_WINDOWS", False)
    core.wifi_device()
    core.wifi_device()
    assert calls["n"] == 1  # cacheado
    core.DEV = None


# ---------------------------------------------------------------------------
# autocaptura pasiva y redactada
# ---------------------------------------------------------------------------

def test_maybe_autocapture_pasiva(monkeypatch):
    core._last_capture["t"] = 0.0
    seen = {}

    def fake_dump(perform_grant=True, redact=False):
        seen["perform_grant"] = perform_grant
        seen["redact"] = redact
        return "DUMP"

    monkeypatch.setattr(core, "debug_dump", fake_dump)
    monkeypatch.setattr(core.log, "info", lambda *a, **k: None)
    core.maybe_autocapture("test")
    assert seen["perform_grant"] is False  # pasiva: no hace GET/POST extra
    assert seen["redact"] is True          # enmascara IP/MAC


def test_maybe_autocapture_cooldown(monkeypatch):
    core._last_capture["t"] = core.time.time()  # recién capturada
    calls = {"n": 0}

    def fake_dump(*a, **k):
        calls["n"] += 1
        return "X"

    monkeypatch.setattr(core, "debug_dump", fake_dump)
    core.maybe_autocapture("test")
    assert calls["n"] == 0  # dentro del cooldown: no vuelca de nuevo


# ---------------------------------------------------------------------------
# grant_session: 2xx = True, error = False (con HTTP mockeado)
# ---------------------------------------------------------------------------

def test_grant_session_204(monkeypatch):
    class FakeResp:
        status = 204
        reason = "No Content"
        def read(self):
            return b""

    class FakeConn:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def getresponse(self): return FakeResp()
        def close(self): pass

    monkeypatch.setattr(core.http.client, "HTTPSConnection", FakeConn)
    assert core.grant_session(PORTAL_PARAMS) is True


def test_grant_session_error(monkeypatch):
    class FakeConn:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): raise ConnectionError("boom")
        def close(self): pass

    monkeypatch.setattr(core.http.client, "HTTPSConnection", FakeConn)
    assert core.grant_session(PORTAL_PARAMS) is False


# ---------------------------------------------------------------------------
# guard de red ajena: el ciclo ENTERO se abstiene, no solo bring_up_wifi
# ---------------------------------------------------------------------------

def test_ciclo_otra_red_no_recupera(monkeypatch):
    """En otra red: ni grant ni bounce; devuelve other-network y resetea streak."""
    monkeypatch.setattr(core, "probe", lambda: ("down", None))
    monkeypatch.setattr(core, "current_ssid", lambda: "WifiOficina")

    def forbidden(*a, **k):
        raise AssertionError("no debe ejecutarse recuperacion en red ajena")

    monkeypatch.setattr(core, "grant_session", forbidden)
    monkeypatch.setattr(core, "bring_up_wifi", forbidden)
    monkeypatch.setattr(core, "wifi_connected", forbidden)

    state = _fresh_state(fail_streak=4)
    action = core.cycle(state)
    assert action == "other-network"
    assert state["fail_streak"] == 0  # estar en otra red no es un fallo nuestro


def test_ciclo_captive_otra_red_no_grant(monkeypatch):
    """Captive de OTRO portal (otra red): no se hace grant con params ajenos."""
    monkeypatch.setattr(core, "probe", lambda: ("captive", "https://otro-portal.mx/login"))
    monkeypatch.setattr(core, "current_ssid", lambda: "WifiOficina")

    def forbidden(*a, **k):
        raise AssertionError("no debe grantear el portal de otra red")

    monkeypatch.setattr(core, "grant_session", forbidden)
    state = _fresh_state()
    assert core.cycle(state) == "other-network"


# ---------------------------------------------------------------------------
# current_ssid: SSID REAL (propiedad del perfil), no el nombre del perfil
# ---------------------------------------------------------------------------

def test_current_ssid_perfil_renombrado(monkeypatch):
    """GENERAL.CONNECTION da el nombre del perfil; el SSID real vive en
    802-11-wireless.ssid. Un perfil renombrado no debe confundir al guard."""
    core.DEV = None
    calls = []

    def fake_run(cmd, timeout=15):
        calls.append(cmd)
        class R:
            returncode, stderr = 0, ""
        if "GENERAL.CONNECTION" in cmd:
            R.stdout = "GENERAL.CONNECTION:Mi Club Renombrado\n"
        elif "802-11-wireless.ssid" in cmd:
            R.stdout = core.SSID + "\n"
        else:
            R.stdout = "wlo1:wifi:connected\n"
        return R()

    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core, "IS_WINDOWS", False)
    assert core.current_ssid() == core.SSID
    core.DEV = None


def test_current_ssid_sin_conexion(monkeypatch):
    core.DEV = None

    def fake_run(cmd, timeout=15):
        class R:
            returncode, stderr = 0, ""
        if "GENERAL.CONNECTION" in cmd:
            R.stdout = "GENERAL.CONNECTION:--\n"
        else:
            R.stdout = "wlo1:wifi:disconnected\n"
        return R()

    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core, "IS_WINDOWS", False)
    assert core.current_ssid() == ""
    core.DEV = None


# ---------------------------------------------------------------------------
# exclusión mutua: el lock del ciclo (daemon vs manual)
# ---------------------------------------------------------------------------

def test_cycle_lock_exclusion_mutua():
    """Si un ciclo sostiene el lock, otro no entra hasta que se suelte."""
    import threading
    import time as _t
    from club_autotalplay import daemon

    got = threading.Event()
    results = {}

    def holder():
        with daemon._cycle_lock():
            got.set()  # ya tengo el lock
            _t.sleep(0.6)

    t1 = threading.Thread(target=holder)
    t1.start()
    assert got.wait(timeout=2), "el holder no tomo el lock"

    # el segundo intento con timeout corto debe fallar (TimeoutError)
    try:
        with daemon._cycle_lock(timeout=0.3):
            results["second"] = "entro"
    except TimeoutError:
        results["second"] = "rechazado"

    t1.join()
    assert results["second"] == "rechazado"

    # tras soltar, el lock debe estar libre de nuevo
    with daemon._cycle_lock(timeout=1):
        pass


def test_run_once_ocupado_devuelve_busy(monkeypatch):
    """run_once con el lock tomado espera y devuelve 'busy' sin tocar nada."""
    import threading
    import time as _t
    from club_autotalplay import daemon

    got = threading.Event()

    def holder():
        with daemon._cycle_lock():
            got.set()
            _t.sleep(0.8)

    t1 = threading.Thread(target=holder)
    t1.start()
    assert got.wait(timeout=2)
    monkeypatch.setattr(daemon, "LOCK_TIMEOUT", 0.4)

    # cycle NO debe llamarse: el lock esta tomado
    def forbidden(state):
        raise AssertionError("cycle no debe correr sin el lock")

    monkeypatch.setattr(daemon.core, "cycle", forbidden)
    action = daemon.run_once()
    assert action == "busy"
    t1.join()

    # sanity: con el lock libre, run_once si corre cycle
    monkeypatch.setattr(daemon.core, "cycle", lambda s: "online")
    monkeypatch.setattr(daemon, "_refresh_state", lambda s, a: s)
    monkeypatch.setattr(daemon.core, "save_state", lambda s: None)
    assert daemon.run_once() == "online"


# ---------------------------------------------------------------------------
# handle_captive
# ---------------------------------------------------------------------------

def test_handle_captive_ok(monkeypatch):
    monkeypatch.setattr(core, "grant_session", lambda p: True)
    monkeypatch.setattr(core, "verify_online", lambda deadline=0: True)
    monkeypatch.setattr(core, "maybe_autocapture", lambda tag="captive": None)
    state = _fresh_state()
    assert core.handle_captive(CAPTIVE_LOC, state) is True
    assert state["last_grant_ok"]


def test_handle_captive_grant_falla(monkeypatch):
    monkeypatch.setattr(core, "grant_session", lambda p: False)
    monkeypatch.setattr(core, "maybe_autocapture", lambda tag="captive": None)
    state = _fresh_state()
    assert core.handle_captive(CAPTIVE_LOC, state) is False


def test_handle_captive_location_mala(monkeypatch):
    monkeypatch.setattr(core, "maybe_autocapture", lambda tag="captive": None)
    state = _fresh_state()
    assert core.handle_captive("https://x/?nada=1", state) is False
