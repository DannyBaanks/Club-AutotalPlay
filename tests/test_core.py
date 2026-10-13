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
    # el original no se toca
    assert PORTAL_PARAMS["ip"] == "10.149.222.166"


def test_redact_location():
    out = core._redact_location(CAPTIVE_LOC)
    assert "10.149.222.x" in out
    assert "50-2e-91-xx-xx-xx" in out
    assert "NEZA-TEST" in out  # accname no es sensible


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
