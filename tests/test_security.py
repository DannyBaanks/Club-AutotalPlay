"""Endurecimiento del grant: binding, OUI y TLS.

El binding es fail-closed: si la identidad local no se puede leer, no se
otorga. La OUI y el TLS son senales de anomalia, no pruebas de identidad, y
no deben desconectar la WiFi ni pelear con NetworkManager.
"""
from __future__ import annotations

import pytest

from club_autotalplay import core

# conftest stubea core._tls_probe para que ningun test toque la red; estos
# tests unitarios del handshake restauran la funcion real.
_REAL_TLS_PROBE = core._tls_probe

IDENT_IP = "10.149.222.166"
IDENT_MAC = "50:2e:91:a1:f8:e4"

CAPTIVE_LOC = (
    "https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp"
    "?wlanuserip=10.149.222.166&wlanparameter=50-2e-91-a1-f8-e4&accname=NEZA-TEST"
)


def _params(ip=IDENT_IP, mac=IDENT_MAC):
    return {"ip": ip, "mac": mac, "nombreAcc": "NEZA-TEST", "tipo": "DESKTOP"}


def _ident(monkeypatch, ip, mac):
    monkeypatch.setattr(core, "_local_identity", lambda: (ip, mac))


def _online_cycle(monkeypatch, bssid):
    monkeypatch.setattr(core, "probe", lambda: ("online", None))
    monkeypatch.setattr(core, "current_ssid", lambda: core.SSID)
    monkeypatch.setattr(core, "current_bssid", lambda: bssid)
    monkeypatch.setattr(core, "save_state", lambda s: None)


# --- binding params <-> interfaz ------------------------------------------

def test_binding_ok(monkeypatch):
    _ident(monkeypatch, IDENT_IP, IDENT_MAC)
    assert core.check_params_binding(_params(mac="50-2E-91-A1-F8-E4")) == core.BIND_OK


def test_binding_mac_ajena(monkeypatch):
    _ident(monkeypatch, IDENT_IP, IDENT_MAC)
    assert core.check_params_binding(_params(mac="aa:bb:cc:dd:ee:ff")) == core.BIND_MISMATCH


def test_binding_ip_ajena(monkeypatch):
    _ident(monkeypatch, IDENT_IP, IDENT_MAC)
    assert core.check_params_binding(_params(ip="10.149.222.167")) == core.BIND_MISMATCH


def test_binding_subred_ajena(monkeypatch):
    _ident(monkeypatch, "192.168.1.10", IDENT_MAC)
    assert core.check_params_binding(_params(ip="192.168.1.10")) == core.BIND_MISMATCH


def test_binding_subred_10_229_observada(monkeypatch):
    # 2026-11-24 16:21: AP Club real (48:4c:29:2b:3e:5f) dio 10.229.28.130.
    _ident(monkeypatch, "10.229.28.130", IDENT_MAC)
    assert core.check_params_binding(_params(ip="10.229.28.130")) == core.BIND_OK


def test_binding_subred_configurable(monkeypatch):
    monkeypatch.setenv(core.ALLOWED_SUBNETS_ENV, "10.150.0.0/16")
    _ident(monkeypatch, "10.150.1.5", IDENT_MAC)
    assert core.check_params_binding(_params(ip="10.150.1.5")) == core.BIND_OK


def test_binding_identidad_ilegible_es_unverified(monkeypatch):
    _ident(monkeypatch, None, None)
    assert core.check_params_binding(_params()) == core.BIND_UNVERIFIED


def test_binding_sin_mac_local_es_unverified(monkeypatch):
    _ident(monkeypatch, IDENT_IP, None)
    assert core.check_params_binding(_params()) == core.BIND_UNVERIFIED


def test_binding_sin_ip_local_es_unverified(monkeypatch):
    _ident(monkeypatch, None, IDENT_MAC)
    assert core.check_params_binding(_params()) == core.BIND_UNVERIFIED


# --- fail-closed: sin identidad no hay POST -------------------------------

def test_grant_unverified_no_postea(monkeypatch):
    _ident(monkeypatch, None, None)

    def forbidden(p):
        raise AssertionError("no debe postear sin identidad verificada")

    monkeypatch.setattr(core, "_grant_session_once", forbidden)
    assert core.grant_session(_params()) is False


def test_grant_mac_ajena_no_postea(monkeypatch):
    _ident(monkeypatch, IDENT_IP, IDENT_MAC)

    def forbidden(p):
        raise AssertionError("no debe postear con la MAC de otro equipo")

    monkeypatch.setattr(core, "_grant_session_once", forbidden)
    assert core.grant_session(_params(mac="aa:bb:cc:dd:ee:ff")) is False


def test_handle_captive_unverified_no_grant(monkeypatch):
    _ident(monkeypatch, None, None)
    monkeypatch.setattr(core, "maybe_autocapture", lambda tag="captive": None)

    def forbidden(p):
        raise AssertionError("no debe grant sin identidad verificada")

    monkeypatch.setattr(core, "grant_session", forbidden)
    assert core.handle_captive(CAPTIVE_LOC, {}) is False


# --- OUI: filtro de anomalia, no autenticacion ----------------------------

@pytest.mark.parametrize("mac", [
    "48:4C:29:2B:B8:AF",
    "94:90:10:2D:F7:37",
    "96:90:10:2D:F7:37",  # LAA de 94:90:10 (Huawei)
])
def test_oui_permitida(mac):
    assert core._oui_allowed(mac) is True


@pytest.mark.parametrize("mac", ["02:11:22:33:44:55", "24:91:BB:00:00:00", ""])
def test_oui_fuera_de_allowlist(mac):
    assert core._oui_allowed(mac) is False


def test_oui_allowlist_configurable(monkeypatch):
    monkeypatch.setenv(core.ALLOWED_OUIS_ENV, "24:91:BB")
    assert core._oui_allowed("24:91:bb:00:00:00") is True


def test_oui_unknown_override(monkeypatch):
    monkeypatch.setenv("CLUB_ALLOW_UNKNOWN_BSSID", "1")
    assert core._oui_allowed("02:11:22:33:44:55") is True


def test_bssid_desconocido_solo_advierte(monkeypatch):
    _online_cycle(monkeypatch, "02:11:22:33:44:55")
    state = {"fail_streak": 0}
    assert core.cycle(state) == "online"
    assert state["security"]["untrusted_bssid"] == "02:11:22:33:44:55"


def test_bssid_desconocido_no_toca_la_red(monkeypatch):
    _online_cycle(monkeypatch, "02:11:22:33:44:55")

    def forbidden(*a, **k):
        raise AssertionError("el filtro OUI no debe desconectar ni ejecutar comandos")

    monkeypatch.setattr(core, "_run", forbidden)
    state = {"fail_streak": 0}
    assert core.cycle(state) == "online"


def test_bssid_desconocido_compuerta_opcional(monkeypatch):
    monkeypatch.setenv(core.REQUIRE_KNOWN_OUI_ENV, "1")
    _online_cycle(monkeypatch, "02:11:22:33:44:55")
    state = {"fail_streak": 0}
    assert core.cycle(state) == "untrusted-bssid"
    assert "last_grant_attempt" not in state


def test_bssid_ilegible_compuerta_opcional(monkeypatch):
    """Con la compuerta activa, BSSID ilegible es UNVERIFIED (fail-closed)."""
    monkeypatch.setenv(core.REQUIRE_KNOWN_OUI_ENV, "1")
    _online_cycle(monkeypatch, None)
    state = {"fail_streak": 0}
    assert core.cycle(state) == "bssid-unverified"


def test_bssid_permitido_no_alerta(monkeypatch):
    _online_cycle(monkeypatch, "96:90:10:2d:f7:37")
    state = {"fail_streak": 0}
    assert core.cycle(state) == "online"
    assert "untrusted_bssid" not in state["security"]


# --- TLS: segunda senal, sin accion destructiva ---------------------------

def test_tls_probe_certificado_invalido(monkeypatch):
    class FakeConn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise core.ssl.SSLError("certificado invalido")

        def close(self):
            pass

    monkeypatch.setattr(core, "_tls_probe", _REAL_TLS_PROBE)
    monkeypatch.setattr(core.http.client, "HTTPSConnection", FakeConn)
    assert core._tls_probe() is False


def test_tls_probe_ok(monkeypatch):
    class Resp:
        status = 204

        def read(self):
            return b""

    class FakeConn:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            pass

        def getresponse(self):
            return Resp()

        def close(self):
            pass

    monkeypatch.setattr(core, "_tls_probe", _REAL_TLS_PROBE)
    monkeypatch.setattr(core.http.client, "HTTPSConnection", FakeConn)
    assert core._tls_probe() is True


def test_tls_fallido_registra_senal_sin_tocar_la_red(monkeypatch):
    _online_cycle(monkeypatch, "48:4c:29:2b:b8:af")
    monkeypatch.setattr(core, "_tls_probe", lambda: False)

    def forbidden(*a, **k):
        raise AssertionError("TLS es una senal: no debe desconectar")

    monkeypatch.setattr(core, "_run", forbidden)
    state = {"fail_streak": 0}
    assert core.cycle(state) == "online"
    assert state["security"]["tls_unverified"]


# --- el ciclo no cachea ni otorga params ajenos ---------------------------

def test_ciclo_captive_params_ajenos_no_grant_ni_bounce(monkeypatch):
    monkeypatch.setattr(core, "probe", lambda: ("captive", CAPTIVE_LOC))
    monkeypatch.setattr(core, "current_ssid", lambda: core.SSID)
    _ident(monkeypatch, IDENT_IP, "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(core, "save_state", lambda s: None)

    def forbidden(*a, **k):
        raise AssertionError("no debe grant ni bounce con params ajenos")

    monkeypatch.setattr(core, "grant_session", forbidden)
    monkeypatch.setattr(core, "bring_up_wifi", forbidden)
    state = {"fail_streak": 0}
    assert core.cycle(state) == "binding-mismatch"
    assert "last_portal_params" not in state


def test_ciclo_down_cache_unverified_no_grant_ni_bounce(monkeypatch):
    monkeypatch.setattr(core, "probe", lambda: ("down", None))
    monkeypatch.setattr(core, "current_ssid", lambda: core.SSID)
    monkeypatch.setattr(core, "wifi_connected", lambda: True)
    _ident(monkeypatch, None, None)
    monkeypatch.setattr(core, "save_state", lambda s: None)

    def forbidden(*a, **k):
        raise AssertionError("no debe grant ni bounce sin identidad")

    monkeypatch.setattr(core, "grant_session", forbidden)
    monkeypatch.setattr(core, "bring_up_wifi", forbidden)
    state = {"fail_streak": 0, "last_portal_params": _params()}
    assert core.cycle(state) == "binding-unverified"
