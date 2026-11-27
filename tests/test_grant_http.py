"""Regresiones del grant HTTP y del guardado de estado.

Salen de los logs reales 2026-11-23..27 (Windows, AP Club real):

  * 208 "grant POST fallo: The read operation timed out" y 214 "Remote end
    closed" sobre respuestas que YA eran 204: se leia el cuerpo de un 204
    que el portal manda con `Transfer-Encoding: chunked`.
  * 13 "no pude guardar estado: [WinError 5] Acceso denegado" al hacer
    os.replace contra un state.json que otro proceso estaba leyendo.

Sin red: la conexion HTTPS se sustituye por un doble.
"""
from __future__ import annotations

import http.client
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from club_autotalplay import core


PARAMS = {
    "ip": "10.149.222.166",
    "mac": "50-2e-91-a1-f8-e4",
    "nombreAcc": "NEZA-TEST",
    "tipo": "DESKTOP",
}


class _Respuesta:
    """Doble de http.client.HTTPResponse."""

    def __init__(self, status, reason="", body=b"", read_error=None):
        self.status = status
        self.reason = reason
        self._body = body
        self._read_error = read_error
        self.read_called = False

    def read(self):
        self.read_called = True
        if self._read_error is not None:
            raise self._read_error
        return self._body


class _Conexion:
    """Doble de http.client.HTTPSConnection."""

    def __init__(self, respuesta=None, error_getresponse=None):
        self._respuesta = respuesta
        self._error = error_getresponse
        self.peticion = None
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.peticion = {
            "method": method, "path": path, "body": body, "headers": headers,
        }

    def getresponse(self):
        if self._error is not None:
            raise self._error
        return self._respuesta

    def close(self):
        self.closed = True


def _conectar(monkeypatch, conexion):
    monkeypatch.setattr(
        core.http.client, "HTTPSConnection", lambda *a, **k: conexion,
    )


# ---------------------------------------------------------------------------
# grant: el status manda, el cuerpo no
# ---------------------------------------------------------------------------

def test_grant_204_no_lee_el_cuerpo(monkeypatch):
    """Un 204 no tiene cuerpo: no se llama read() (se quedaria bloqueado)."""
    respuesta = _Respuesta(204, "No Content")
    conexion = _Conexion(respuesta)
    _conectar(monkeypatch, conexion)

    assert core._grant_session_once(PARAMS) is True
    assert respuesta.read_called is False
    assert conexion.peticion["method"] == "POST"
    assert conexion.peticion["path"] == core.GRANT_PATH
    assert conexion.closed is True


def test_grant_205_tampoco_lee_el_cuerpo(monkeypatch):
    respuesta = _Respuesta(205, "Reset Content")
    _conectar(monkeypatch, _Conexion(respuesta))

    assert core._grant_session_once(PARAMS) is True
    assert respuesta.read_called is False


def test_grant_200_si_lee_el_cuerpo(monkeypatch):
    respuesta = _Respuesta(200, "OK", body=b"ok")
    _conectar(monkeypatch, _Conexion(respuesta))

    assert core._grant_session_once(PARAMS) is True
    assert respuesta.read_called is True


def test_grant_2xx_con_cuerpo_ilegible_sigue_siendo_exito(monkeypatch):
    """Un cuerpo roto no convierte un 2xx en fallo (el portal ya otorgo)."""
    respuesta = _Respuesta(200, "OK", read_error=OSError("connection reset"))
    _conectar(monkeypatch, _Conexion(respuesta))

    assert core._grant_session_once(PARAMS) is True


def test_grant_remotedisconnected_es_fallo_sin_excepcion(monkeypatch):
    conexion = _Conexion(error_getresponse=http.client.RemoteDisconnected("boom"))
    _conectar(monkeypatch, conexion)

    assert core._grant_session_once(PARAMS) is False
    assert conexion.closed is True  # el finally cierra aunque falle


def test_grant_500_es_fallo(monkeypatch):
    _conectar(monkeypatch, _Conexion(_Respuesta(500, "Server Error")))

    assert core._grant_session_once(PARAMS) is False


# ---------------------------------------------------------------------------
# state.json: reintentos ante lector concurrente (WinError 5)
# ---------------------------------------------------------------------------

def test_save_state_reintenta_si_el_destino_esta_abierto(monkeypatch):
    real_replace = os.replace
    intentos = {"n": 0}

    def replace_inestable(src, dst):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise PermissionError(13, "Acceso denegado")
        return real_replace(src, dst)

    monkeypatch.setattr(core.os, "replace", replace_inestable)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    core.save_state({"online": True, "last_action": "online"})

    assert intentos["n"] == 3
    assert core.load_state() == {"online": True, "last_action": "online"}


def test_save_state_no_deja_temporales(monkeypatch):
    core.save_state({"online": True})
    dirname = os.path.dirname(core.STATE_PATH)
    sobrantes = [n for n in os.listdir(dirname) if n.endswith(".tmp")]
    assert sobrantes == []


def test_save_state_no_revienta_si_nunca_puede(monkeypatch):
    def siempre_denegado(src, dst):
        raise PermissionError(13, "Acceso denegado")

    monkeypatch.setattr(core.os, "replace", siempre_denegado)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    core.save_state({"online": False})  # solo registra el warning

    dirname = os.path.dirname(core.STATE_PATH)
    assert [n for n in os.listdir(dirname) if n.endswith(".tmp")] == []


# ---------------------------------------------------------------------------
# salida de consola: netsh emite UTF-8, no la codificacion ANSI
# ---------------------------------------------------------------------------

def test_run_decodifica_utf8():
    codigo = "import sys; sys.stdout.buffer.write(b'conexi\\xc3\\xb3n')"
    salida = core._run([sys.executable, "-c", codigo])
    assert "conexión" in salida.stdout


# ---------------------------------------------------------------------------
# reproduccion del portal real: 204 + chunked sin chunk final
# ---------------------------------------------------------------------------

class _PortalQueNoCierra(BaseHTTPRequestHandler):
    """Contesta 204 con `Transfer-Encoding: chunked` y no manda el chunk final.

    Es lo que hace clubwifi.totalplay.com.mx (ver los headers en el log):
    un 204 que anuncia chunked y luego deja la conexion abierta. Leer el
    cuerpo con http.client entra en _read_chunked() y espera para siempre.
    """

    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 (nombre que exige http.server)
        largo = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(largo)
        self.send_response(204)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        # Sin `0\r\n\r\n`: el cliente que lea el cuerpo se queda colgado.

    def log_message(self, *args):  # silencia el log del servidor en pytest
        pass


def test_grant_204_chunked_sin_chunk_final_no_se_cuelga(monkeypatch):
    """La regresion real: antes esto gastaba GRANT_TIMEOUT y reportaba fallo."""
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _PortalQueNoCierra)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    puerto = servidor.server_address[1]
    try:
        monkeypatch.setattr(core, "GRANT_HOST", "127.0.0.1")
        monkeypatch.setattr(core, "GRANT_TIMEOUT", 1)
        monkeypatch.setattr(
            core.http.client, "HTTPSConnection",
            lambda host, port, timeout=None, context=None: http.client.HTTPConnection(
                "127.0.0.1", puerto, timeout=timeout,
            ),
        )

        t0 = time.monotonic()
        resultado = core._grant_session_once(PARAMS)
        transcurrido = time.monotonic() - t0

        assert resultado is True
        # Si leyera el cuerpo, tardaria GRANT_TIMEOUT (1 s) y devolveria False.
        assert transcurrido < 1.0, f"leyo el cuerpo del 204 ({transcurrido:.2f}s)"
    finally:
        servidor.shutdown()
        servidor.server_close()
