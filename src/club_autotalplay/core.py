"""Core del auto-reconnect para la red publica Club_Totalplay_WiFi.

La red otorga sesiones de ~15 min (en la practica se corta ~cada 5 min).
Cuando expira, el portal redirige todo el trafico HTTP a:
    https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp?...
El navegador reproduce un video de publicidad y al terminar hace
    POST /ClubMovil/video-completado  con {ip, mac, nombreAcc, tipo}
Ese POST es el que otorga la sesion (devuelve 204 No Content).

Este modulo detecta el corte (302 en el probe de conectividad) y hace el
mismo POST automaticamente, sin browser ni video. Tambien levanta el WiFi
si NetworkManager lo dejo caido.

Es una libreria: no configura logging al importarse. El CLI/daemon llama a
`setup_logging()` antes de usarla.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# --- Config ---------------------------------------------------------------
SSID = "Club_Totalplay_WiFi"
IS_WINDOWS = sys.platform.startswith("win")
# DEV: en Linux se detecta bajo demanda (nmcli); en Windows tambien (netsh).
# No se fija aqui: en otra maquina la interfaz puede no ser wlo1.
DEV = None
# Probe de conectividad: 204 = online, 302 = captive portal.
PROBE_HOST = "connectivitycheck.gstatic.com"
PROBE_PATH = "/generate_204"
PROBE_PORT = 80
# Endpoint que otorga la sesion.
GRANT_HOST = "clubwifi.totalplay.com.mx"
GRANT_PATH = "/ClubMovil/video-completado"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
POLL_SECONDS = 20          # cada cuantos segundos se chequea (red sana)
POLL_DOWN_SECONDS = 5      # sondeo rapido mientras la red NO esta online
TIMEOUT = 8                # timeout por request
COOLDOWN_SECONDS = 45      # no reintentar el grant mas seguido que esto
WIFI_RETRY_SECONDS = 30    # no re-botear el WiFi mas seguido que esto
FAIL_STREAK_ESCALATE = 5   # N ciclos sin online -> reinicio total (ignora cooldowns)

if IS_WINDOWS:
    _base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    STATE_DIR = os.path.join(_base, "club-autotalplay")
else:
    STATE_DIR = os.path.expanduser("~/.local/state/club-autotalplay")
LOG_PATH = os.path.join(STATE_DIR, "watchdog.log")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

log = logging.getLogger("club_autotalplay")


def setup_logging(level=logging.INFO, to_stdout=True):
    """Configura logging una sola vez (idempotente)."""
    root = logging.getLogger("club_autotalplay")
    root.setLevel(level)
    if root.handlers:
        return root
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if to_stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return root


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        log.warning("no pude guardar estado: %s", e)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Fuerza a urllib a NO seguir redirects (queremos ver el 302)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def probe():
    """Devuelve (status, location_url). status: online|captive|down."""
    url = f"http://{PROBE_HOST}:{PROBE_PORT}{PROBE_PATH}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = _opener.open(req, timeout=TIMEOUT)
        code = resp.getcode()
        resp.read()
        if code == 204:
            return "online", None
        loc = resp.headers.get("Location")
        return ("captive", loc) if loc else ("down", None)
    except urllib.error.HTTPError as e:
        # urllib lanza HTTPError cuando el redirect handler devuelve None
        loc = e.headers.get("Location")
        if e.code in (301, 302, 303, 307, 308) and loc:
            return "captive", loc
        return "down", None
    except (socket.timeout, urllib.error.URLError, ConnectionError, OSError) as e:
        log.debug("probe fallo: %s", e)
        return "down", None


def parse_portal_params(location):
    """Extrae ip/mac/nombreAcc de la URL de redirect del portal."""
    if not location:
        return None
    try:
        qs = urllib.parse.urlparse(location).query
        params = dict(urllib.parse.parse_qsl(qs))
    except Exception:
        return None
    ip = params.get("wlanuserip") or params.get("ip")
    mac = params.get("wlanparameter") or params.get("mac")
    acc = params.get("accname") or params.get("nombreAcc") or params.get("wlanacname")
    if not (ip and mac):
        log.warning("params incompletos en location: %s", _redact_location(location))
        return None
    return {"ip": ip, "mac": mac, "nombreAcc": acc or "", "tipo": "DESKTOP"}


def _redact_location(location):
    """Location del portal con ip/mac de la query enmascarados."""
    if not location:
        return location
    try:
        parts = urllib.parse.urlparse(location)
        qs = dict(urllib.parse.parse_qsl(parts.query))
        if qs.get("wlanuserip"):
            qs["wlanuserip"] = _redact_ip(qs["wlanuserip"])
        if qs.get("wlanparameter"):
            qs["wlanparameter"] = _redact_mac(qs["wlanparameter"])
        new_q = urllib.parse.urlencode(qs)
        return urllib.parse.urlunparse(parts._replace(query=new_q))
    except Exception:
        return "<location no parseable>"


def grant_session(params):
    """POST al endpoint que otorga la sesion. Devuelve True si 2xx."""
    body = json.dumps(params).encode("utf-8")
    ctx = ssl.create_default_context()
    try:
        conn = http.client.HTTPSConnection(GRANT_HOST, 443, timeout=TIMEOUT, context=ctx)
        conn.request(
            "POST",
            GRANT_PATH,
            body=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Origin": f"https://{GRANT_HOST}",
                "Referer": f"https://{GRANT_HOST}/ClubMovil/inicio?type=DESKTOP",
            },
        )
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        ok = 200 <= resp.status < 300
        log.info(
            "grant POST -> HTTP %s %s body=%r params=%s",
            resp.status, resp.reason, data[:120], _redact_params(params),
        )
        return ok
    except Exception as e:
        log.error("grant POST fallo: %s", e)
        return False


def _run(cmd, timeout=15):
    """Ejecuta un subprocess sin ventana de consola visible en Windows.

    Sin CREATE_NO_WINDOW, cuando el padre es windowed (la bandeja) cada
    `netsh` abre una consola nueva que parpadea en pantalla — y el daemon lo
    llama cada ~20 s, asi que salia una ventana por ciclo.
    """
    kwargs = {}
    if IS_WINDOWS:
        # 0x08000000 = CREATE_NO_WINDOW (el hijo no recibe consola)
        kwargs["creationflags"] = 0x08000000
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


def _redact_ip(ip):
    """Enmascara el ultimo octeto de una IPv4 (10.149.222.54 -> 10.149.222.x)."""
    if not ip:
        return ip
    parts = ip.split(".")
    if len(parts) == 4:
        parts[-1] = "x"
        return ".".join(parts)
    return ip


def _redact_mac(mac):
    """Conserva los primeros 3 octetos del MAC (suficiente para vendor)."""
    if not mac:
        return mac
    sep = "-" if "-" in mac else ":"
    parts = mac.replace(":", "-").split("-")
    if len(parts) >= 3:
        return sep.join(parts[:3] + ["xx"] * (len(parts) - 3))
    return mac


def _redact_params(params):
    """Copia de params con IP/MAC enmascarada para logs."""
    if not params:
        return params
    out = dict(params)
    if out.get("ip"):
        out["ip"] = _redact_ip(out["ip"])
    if out.get("mac"):
        out["mac"] = _redact_mac(out["mac"])
    return out


def wifi_device():
    """Interfaz WiFi activa. Linux: la que este conectada a una red (o la
    primera wifi si ninguna). Windows: None (netsh no necesita interfaz)."""
    global DEV
    if IS_WINDOWS:
        return None
    if DEV:
        return DEV
    try:
        out = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"]).stdout
        wifi_devs = []
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1].strip() == "wifi":
                dev, state = parts[0].strip(), parts[2].strip()
                wifi_devs.append((dev, state))
                if state == "connected":
                    DEV = dev
                    log.debug("interfaz WiFi detectada: %s", dev)
                    return DEV
        if wifi_devs:
            DEV = wifi_devs[0][0]
            log.debug("interfaz WiFi (no conectada): %s", DEV)
            return DEV
    except Exception as e:
        log.debug("no pude detectar interfaz WiFi: %s", e)
    return None


def _connected_to_other_ssid():
    """True si hay un WiFi conectado a una red que NO es la nuestra.

    En ese caso NO se debe tocar el radio ni hacer `connection up`: matariamos
    la conexion que el usuario esta usando (oficina, casa, etc.).
    """
    if IS_WINDOWS:
        # netsh wlan connect a nuestra red desconecta la actual; solo lo hace
        # el usuario via CLI explicito, no el daemon automaticamente.
        cur = current_ssid()
        return bool(cur) and cur != SSID
    cur = current_ssid()
    return bool(cur) and cur != SSID


def _windows_wlan_show():
    """`netsh wlan show interfaces` (una sola llamada reutilizable)."""
    try:
        return _run(["netsh", "wlan", "show", "interfaces"], timeout=15).stdout
    except Exception:
        return ""


def wifi_connected():
    if IS_WINDOWS:
        # El valor del SSID no se traduce (solo las etiquetas), asi que buscar
        # el SSID en la salida es robusto al idioma de Windows.
        return SSID in _windows_wlan_show()
    dev = wifi_device()
    if not dev:
        return False
    try:
        out = _run(["nmcli", "-t", "-f", "GENERAL.STATE", "dev", "show", dev]).stdout
        return "100" in out  # 100 (connected)
    except Exception:
        return False


def current_ssid():
    if IS_WINDOWS:
        for line in _windows_wlan_show().splitlines():
            s = line.strip()
            # la etiqueta "SSID" es estable en todos los idiomas; la de estado
            # no ("State"/"Estado"), por eso no se usa para filtrar.
            if s.startswith("SSID") and "BSSID" not in s:
                return s.split(":", 1)[-1].strip()
        return ""
    dev = wifi_device()
    if not dev:
        return ""
    try:
        return _run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", dev]).stdout.split(":", 1)[-1].strip()
    except Exception:
        return ""


def bring_up_wifi():
    # Guard: si hay un WiFi conectado a OTRA red, no tocamos nada.
    # Apagar el radio o forzar `connection up` mataria la conexion que el
    # usuario esta usando (oficina, casa, etc.).
    if _connected_to_other_ssid():
        cur = current_ssid()
        log.warning(
            "WiFi conectado a otra red (%r != %r); NO toco el radio ni el perfil",
            cur, SSID,
        )
        return False
    if IS_WINDOWS:
        # analogo de `nmcli connection up`: conecta a un perfil WiFi guardado.
        log.info("levantando WiFi: netsh wlan connect name=%s", SSID)
        r = _run(["netsh", "wlan", "connect", f"name={SSID}"], timeout=30)
        # netsh devuelve 0 en exito. El mensaje viene localizado (en ES:
        # "La solicitud de conexion se completo correctamente"), asi que NO se
        # decide por texto — con locale ES el chequeo viejo ("se conect" /
        # "connected") no matcheaba y reportaba fallo en un exito.
        if r.returncode == 0:
            log.info("WiFi OK: %s", (r.stdout or r.stderr).strip())
            return True
        log.error("netsh fallo rc=%s out=%s err=%s", r.returncode, r.stdout.strip(), r.stderr.strip())
        return False
    # Linux: toggle de radio (el MISMO que el toggle manual del usuario).
    # `nmcli connection up` a veces falla con "Causa desconocida" cuando el AP
    # no esta en el escaneo; apagar/prender el radio siempre reconecta.
    log.info("re-boteando WiFi: nmcli radio wifi off/on")
    _run(["nmcli", "radio", "wifi", "off"])
    time.sleep(2)
    _run(["nmcli", "radio", "wifi", "on"])
    # esperar a que NetworkManager reconecte a la red conocida
    deadline = time.time() + 30
    while time.time() < deadline:
        if wifi_connected():
            log.info("WiFi OK (radio re-botado y reconnectado)")
            return True
        time.sleep(2)
    # fallback por si el radio no basto
    log.warning("radio toggle no reconectó en 30s; intento connection up")
    r = _run(["nmcli", "connection", "up", SSID], timeout=45)
    if r.returncode == 0:
        log.info("WiFi OK: %s", r.stdout.strip())
        return True
    log.error("nmcli fallo rc=%s out=%s err=%s", r.returncode, r.stdout.strip(), r.stderr.strip())
    return False


def verify_online(deadline=45):
    """Repite el probe hasta que sea online o se acabe el tiempo."""
    t0 = time.time()
    while time.time() - t0 < deadline:
        st, _ = probe()
        if st == "online":
            return True
        time.sleep(3)
    return False


def handle_captive(location, state):
    maybe_autocapture("handle_captive")
    params = parse_portal_params(location)
    if not params:
        log.error("no pude parsear params del portal; location=%s", _redact_location(location))
        return False
    log.info("captive portal detectado -> otorgando sesion: %s", _redact_params(params))
    if not grant_session(params):
        return False
    if verify_online(deadline=40):
        log.info("SESION OTORGADA - internet restaurado")
        state["last_grant_ok"] = now_iso()
        return True
    log.warning("grant aceptado pero el probe sigue sin 204; intento re-botear WiFi")
    return False


def _iso_to_epoch(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _try_grant_from_probe(state):
    """Si el probe dice captive, intenta grant. Devuelve True si restauro online."""
    st, loc = probe()
    if st == "online":
        return True
    if st == "captive":
        p = parse_portal_params(loc)
        if p and grant_session(p) and verify_online(deadline=20):
            state["last_grant_ok"] = now_iso()
            log.info("SESION OTORGADA")
            return True
    return False


def cycle(state):
    """Un ciclo completo del watchdog. Devuelve accion tomada."""
    st, location = probe()
    if st == "online":
        state["fail_streak"] = 0
        return "online"

    state["fail_streak"] = int(state.get("fail_streak") or 0) + 1
    streak = state["fail_streak"]
    escalate = streak >= FAIL_STREAK_ESCALATE
    log.warning(
        "probe=%s location=%s fail_streak=%d%s",
        st, _redact_location(location), streak, " ESCALANDO (reinicio total)" if escalate else "",
    )

    if st == "captive":
        last = state.get("last_grant_attempt", "")
        can_grant = escalate or not (last and (time.time() - _iso_to_epoch(last)) < COOLDOWN_SECONDS)
        if can_grant:
            state["last_grant_attempt"] = now_iso()
            save_state(state)
            if handle_captive(location, state):
                state["fail_streak"] = 0
                return "granted"
            # grant fallo -> cae al re-botear abajo (no nos quedamos esperando)
        else:
            log.info("cooldown de grant activo (%s); sin grant, pruebo bounce", last)
        # grant no disponible o fallo -> cae al bounce YA (sin return precoz)

    # "down" o grant fallido: quizas el WiFi se cayo. Re-botear con cooldown.
    if not wifi_connected():
        log.warning("WiFi no conectado (ssid=%r)", current_ssid())
    last_bounce = state.get("last_bounce", "")
    can_bounce = escalate or not (last_bounce and (time.time() - _iso_to_epoch(last_bounce)) < WIFI_RETRY_SECONDS)
    if not can_bounce:
        log.info("cooldown de bounce WiFi activo (%s)", last_bounce)
        return "bounce-cooldown"
    state["last_bounce"] = now_iso()
    save_state(state)
    if bring_up_wifi():
        time.sleep(3)
        # Tras re-botear puede quedar captive o tardar en subir: sondear y
        # (si hace falta) grant en loop corto, no un solo verify_online.
        deadline = time.time() + 40
        while time.time() < deadline:
            if _try_grant_from_probe(state):
                state["fail_streak"] = 0
                log.info("internet restaurado tras bounce de WiFi")
                return "bounced"
            time.sleep(3)
    return "failed"


def print_status():
    st, loc = probe()
    print(f"probe      : {st}")
    print(f"location   : {loc or '-'}")
    print(f"wifi       : {'conectado' if wifi_connected() else 'DESCONECTADO'} (dev={wifi_device() or '-'})")
    print(f"ssid       : {current_ssid() or '-'}")
    params = parse_portal_params(loc) if loc else None
    if params:
        print(f"portal     : {json.dumps(params)}")
    s = load_state()
    for k in ("last_grant_ok", "last_grant_attempt", "last_bounce"):
        if s.get(k):
            print(f"{k:14}: {s[k]}")


def debug_dump(perform_grant=True, redact=False):
    """Diagnostico crudo para capturar un corte real (sobre todo Windows).

    Duerme cero y no aplica cooldowns: imprime el estado de cada paso tal
    cual, para poder comparar un corte de Windows contra uno de Linux y
    separar estas hipotesis:
      A) el probe no ve el 302 (p.ej. proxy del sistema)  -> probe_result
      B) el portal exige un GET previo (priming/cookie)   -> portalGET_*
      C) el User-Agent cambia el flujo                    -> grant_*
    Devuelve el texto; el CLI decide donde imprimirlo.

    redact=True enmascara IP/MAC en params y body (para autocaptura al log);
    el `debug` explicito del usuario puede usar redact=False.
    """
    out = []

    def p(k, v):
        out.append(f"{k:24}: {v}")

    p("platform", sys.platform)
    p("IS_WINDOWS", IS_WINDOWS)
    p("STATE_PATH", STATE_PATH)
    try:
        proxies = urllib.request.getproxies()
    except Exception as e:
        proxies = {"_error": str(e)}
    p("urllib.getproxies()", json.dumps(proxies) if proxies else "(vacio)")
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        if os.environ.get(var):
            p(f"env {var}", os.environ[var])
    p("USER_AGENT", USER_AGENT)

    # --- probe crudo (hipotesis A) --------------------------------------
    loc = None
    url = f"http://{PROBE_HOST}:{PROBE_PORT}{PROBE_PATH}"
    p("probe_url", url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        resp = _opener.open(req, timeout=TIMEOUT)
        code = resp.getcode()
        loc = resp.headers.get("Location")
        body = resp.read()[:200]
        p("probe_status", code)
        p("probe_location", _redact_location(loc) if redact else (loc or "-"))
        p("probe_body", repr(body))
        for k, v in resp.headers.items():
            if redact and k.lower() == "location":
                v = _redact_location(v)
            p(f"probe_hdr {k}", v)
        probe_st = "online" if code == 204 else ("captive" if loc else "down")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        p("probe_status", f"HTTPError {e.code}")
        p("probe_location", _redact_location(loc) if redact else (loc or "-"))
        for k, v in e.headers.items():
            if redact and k.lower() == "location":
                v = _redact_location(v)
            p(f"probe_hdr {k}", v)
        probe_st = "captive" if (e.code in (301, 302, 303, 307, 308) and loc) else "down"
    except Exception as e:
        p("probe_error", f"{type(e).__name__}: {e}")
        probe_st = "down"
    p("probe_result", probe_st)

    p("wifi_connected", wifi_connected())
    p("current_ssid", current_ssid() or "-")

    params = parse_portal_params(loc) if loc else None
    p("parsed_params", json.dumps(_redact_params(params) if redact else params) if params else "-")

    # --- hipotesis B: GET previo a la pagina del portal ------------------
    if loc and perform_grant:
        try:
            g = urllib.request.Request(loc, headers={"User-Agent": USER_AGENT})
            gr = urllib.request.build_opener().open(g, timeout=TIMEOUT)
            gb = gr.read()[:300]
            p("portalGET_status", gr.getcode())
            for k, v in gr.headers.items():
                if k.lower() in ("location", "set-cookie", "content-type"):
                    if redact and k.lower() == "location":
                        v = _redact_location(v)
                    p(f"portalGET_hdr {k}", v)
            p("portalGET_body", repr(gb))
        except Exception as e:
            p("portalGET_error", f"{type(e).__name__}: {e}")

    # --- grant POST (hipotesis C) ---------------------------------------
    if params and perform_grant:
        body = json.dumps(params).encode("utf-8")
        p("grant_url", f"https://{GRANT_HOST}{GRANT_PATH}")
        p("grant_body", json.dumps(_redact_params(params)) if redact else body.decode("utf-8"))
        ctx = ssl.create_default_context()
        try:
            conn = http.client.HTTPSConnection(GRANT_HOST, 443, timeout=TIMEOUT, context=ctx)
            conn.request(
                "POST", GRANT_PATH, body=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                    "Origin": f"https://{GRANT_HOST}",
                    "Referer": f"https://{GRANT_HOST}/ClubMovil/inicio?type=DESKTOP",
                },
            )
            r = conn.getresponse()
            rb = r.read()
            conn.close()
            p("grant_status", f"{r.status} {r.reason}")
            for k, v in r.getheaders():
                p(f"grant_hdr {k}", v)
            p("grant_body_resp", repr(rb[:300]))
        except Exception as e:
            p("grant_error", f"{type(e).__name__}: {e}")

        st2, _ = probe()
        p("reprobe_tras_grant", st2)

    return "\n".join(out)


# Guarda para que el daemon se autovuelque el diagnostico una vez por episodio
# captive (no en cada ciclo, o el log se llena de netsh repetido).
_last_capture = {"t": 0.0}


def maybe_autocapture(tag="captive"):
    """Vuelca debug_dump al log la primera vez que vemos un corte (con cooldown).

    Pasivo por defecto: NO hace GET/POST extra al portal (perform_grant=False)
    y enmascara IP/MAC (redact=True). El diagnostico activo (grant crudo) queda
    para el `debug` explicito del usuario, no para el log automatico.
    """
    now = time.time()
    if now - _last_capture["t"] < COOLDOWN_SECONDS:
        return
    _last_capture["t"] = now
    try:
        log.info("=== AUTOCAPTURA (%s) ===\n%s", tag, debug_dump(perform_grant=False, redact=True))
    except Exception as e:
        log.warning("autocaptura fallo: %s", e)


def main(argv=None):
    """CLI minima del core: --once / --status (para probar M0)."""
    import argparse
    ap = argparse.ArgumentParser(
        prog="club-autotalplay.core",
        description="Core del auto-reconnect Club_Totalplay_WiFi",
    )
    ap.add_argument("--once", action="store_true", help="un solo ciclo y salir")
    ap.add_argument("--status", action="store_true", help="imprime estado actual")
    ap.add_argument("--poll", type=int, default=POLL_SECONDS, help="segundos entre chequeos")
    args = ap.parse_args(argv)

    setup_logging()
    if args.status:
        print_status()
        return 0

    state = load_state()
    log.info("core iniciado (poll=%ss, ssid=%s, dev=%s)", args.poll, SSID, wifi_device() or "-")
    if args.once:
        action = cycle(state)
        save_state(state)
        log.info("ciclo --once: %s", action)
        return 0

    while True:
        try:
            action = cycle(state)
            save_state(state)
            if action != "online":
                log.info("ciclo: %s", action)
        except Exception as e:
            log.exception("ciclo exception: %s", e)
        time.sleep(args.poll)


if __name__ == "__main__":
    sys.exit(main())
