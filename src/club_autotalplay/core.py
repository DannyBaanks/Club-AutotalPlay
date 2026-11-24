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
import ipaddress
import json
import logging
import os
import re
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
TIMEOUT = 5                # timeout por request (antes 8s; el portal responde rapido o falla rapido)
GRANT_TIMEOUT = 3          # timeout del POST de grant: los 204 llegan en 0.5-1.7 s; un read
                           # timeout de 5 s x4 intentos retrasaba el bounce ~22 s (log 2026-11-04 00:16)
DNS_WAIT_SECONDS = 4       # sin DNS el link se cayo (roaming entre APs, ~3 s): esperar al probe
                           # en vez de quemar reintentos del POST (log 2026-11-03 21:50)
COOLDOWN_SECONDS = 45      # no reintentar el grant mas seguido que esto
WIFI_RETRY_SECONDS = 30    # no re-botear el WiFi mas seguido que esto
FAIL_STREAK_ESCALATE = 5   # N ciclos sin online -> reinicio total (ignora cooldowns)
GRANT_RECHECK_SECONDS = 1  # tras un grant sin respuesta, re-probar esto antes de rendirse (antes 3s)
SESSION_SECONDS = 900      # el portal corta ~900 s despues del grant (ver next_poll_seconds)
EXPIRY_LEAD_SECONDS = 20   # abrir la ventana de sondeo rapido este tanto antes
EXPIRY_TAIL_SECONDS = 60   # ...y mantenerla este tanto despues
POLL_EXPIRY_SECONDS = 1    # sondeo dentro de la ventana de expiracion

# --- Endurecimiento del grant (2026-11-24) --------------------------------
# El probe viaja por HTTP plano y un AP gemelo puede falsificar el 204/302.
# El grant se liga a la interfaz (MAC+IP+subred); identidad ilegible =>
# UNVERIFIED y NO se otorga. La OUI del BSSID y el TLS al portal son solo
# senales de anomalia: no prueban identidad y no desconectan la WiFi.
ALLOWED_SUBNETS_ENV = "CLUB_ALLOWED_SUBNETS"
DEFAULT_ALLOWED_SUBNETS = "10.149.0.0/16"
ALLOWED_OUIS_ENV = "CLUB_ALLOWED_OUIS"
DEFAULT_ALLOWED_OUIS = "48:4C:29,94:90:10"  # Huawei TotalPlay; 96:90:10 = LAA de 94:90:10
REQUIRE_KNOWN_OUI_ENV = "CLUB_REQUIRE_KNOWN_OUI"
TLS_CHECK_SECONDS = 300    # cada cuanto se cruza el 204 con TLS al portal

if IS_WINDOWS:
    _base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    STATE_DIR = os.path.join(_base, "club-autotalplay")
else:
    STATE_DIR = os.path.expanduser("~/.local/state/club-autotalplay")
LOG_PATH = os.path.join(STATE_DIR, "watchdog.log")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

# --- Interlock con hotspot propio (wifihost) -------------------------------
# Si hay un AP propio corriendo sobre el MISMO phy (wifihost deja su estado en
# /run/wifihost/state), `nmcli radio wifi off` es un rfkill del phy COMPLETO:
# tumba el uplink y el AP a la vez (el telefono pierde WiFi ~10-15 s por bounce).
# Con el interlock activo el bounce de radio pasa a ser ULTIMO recurso: primero
# se intenta `nmcli connection up`, que no apaga el radio.
# Kill-switch sin revertir: CLUB_AUTOTALPLAY_INTERLOCK=0
HOTSPOT_STATE_PATH = "/run/wifihost/state"
INTERLOCK_ENV = "CLUB_AUTOTALPLAY_INTERLOCK"

log = logging.getLogger("club_autotalplay")


def setup_logging(level=logging.INFO, to_stdout=True):
    """Configura logging una sola vez (idempotente)."""
    root = logging.getLogger("club_autotalplay")
    root.setLevel(level)
    if root.handlers:
        return root
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    _private_dir(os.path.dirname(LOG_PATH))
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if to_stdout:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return root


def _private_dir(path):
    """Crea el dir de estado y lo deja 0700 en POSIX.

    `makedirs(mode=...)` solo aplica al crear: un dir que ya existia con
    0775 (instalaciones previas) se queda abierto. Log, state.json (params
    del portal) y locks viven aqui; con el dir cerrado, los permisos de cada
    archivo rotado ya no importan.
    """
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o700)
        except OSError as e:
            log.warning("no pude cerrar permisos de %s: %s", path, e)


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
        _private_dir(os.path.dirname(STATE_PATH))
        tmp = STATE_PATH + ".tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        fd = os.open(tmp, flags, 0o600)
        if not IS_WINDOWS:
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        if not IS_WINDOWS:
            os.chmod(tmp, 0o600)
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


def is_trusted_portal(location):
    """True solo si el redirect apunta al portal real: https://GRANT_HOST.

    El probe viaja por HTTP plano (asi funciona la deteccion de captive), y
    en una red publica cualquiera en el mismo segmento (o un AP gemelo con el
    mismo SSID) puede contestar ese 302. Un redirect a otro host no es el
    portal de Totalplay: no se le extraen params, no se hace grant y no se
    re-botea el radio por el. En 15 dias de logs (2026-10-13..28) el 100% de
    los redirects reales fue a https://clubwifi.totalplay.com.mx.
    """
    if not location:
        return False
    try:
        parts = urllib.parse.urlparse(location)
    except Exception:
        return False
    return parts.scheme == "https" and (parts.hostname or "").lower() == GRANT_HOST


_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([-:][0-9A-Fa-f]{2}){5}$")


def _valid_params(ip, mac, acc):
    """Formato estricto: lo que no parece IP/MAC/zona no se manda al portal."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return False
    if not addr.is_private:
        return False
    if not _MAC_RE.match(mac):
        return False
    return len(acc) <= 128 and acc.isprintable()


def parse_portal_params(location):
    """Extrae ip/mac/nombreAcc de la URL de redirect del portal."""
    if not location:
        return None
    if not is_trusted_portal(location):
        log.warning("redirect a un host que no es el portal: %s", _redact_location(location))
        return None
    try:
        qs = urllib.parse.urlparse(location).query
        params = dict(urllib.parse.parse_qsl(qs))
    except Exception:
        return None
    ip = params.get("wlanuserip") or params.get("ip")
    mac = params.get("wlanparameter") or params.get("mac")
    acc = params.get("accname") or params.get("nombreAcc") or params.get("wlanacname") or ""
    if not (ip and mac):
        log.warning("params incompletos en location: %s", _redact_location(location))
        return None
    if not _valid_params(ip, mac, acc):
        log.warning("params con formato invalido en location: %s", _redact_location(location))
        return None
    return {"ip": ip, "mac": mac, "nombreAcc": acc, "tipo": "DESKTOP"}


def _redact_location(location):
    """Location del portal con ip/mac/accname de la query enmascarados."""
    if not location:
        return location
    try:
        parts = urllib.parse.urlparse(location)
        qs = dict(urllib.parse.parse_qsl(parts.query))
        for key in ("wlanuserip", "ip"):
            if qs.get(key):
                qs[key] = _redact_ip(qs[key])
        for key in ("wlanparameter", "mac"):
            if qs.get(key):
                qs[key] = _redact_mac(qs[key])
        for k in qs:
            key = k.lower()
            if "acc" in key or "acname" in key:
                # accname/nombreAcc/wlanacname identify the zone or AP.
                qs[k] = "<ACC>"
            elif any(
                secret in key
                for secret in ("token", "auth", "session", "cookie", "password", "secret", "key")
            ):
                qs[k] = "<REDACTADO>"
        new_q = urllib.parse.urlencode(qs)
        return urllib.parse.urlunparse(parts._replace(query=new_q))
    except Exception:
        return "<location no parseable>"


RECOVERED = "recovered"  # el link volvio solo, sin grant (truthy a proposito)


def grant_session(params, retries=3):
    """POST al endpoint que otorga la sesion. Devuelve True si 2xx.

    Devuelve RECOVERED (truthy) si el link volvio online sin grant (roaming).
    Si el portal reaparece con params nuevos, `params` se actualiza IN PLACE.

    El portal a veces falla intermitentemente (IncompleteRead / Remote end
    closed, visto 2026-10-13 04:02-04:48): se reintenta `retries` veces con
    0.5 s de separacion antes de declarar fracaso.

    Muchas veces ese "fallo" es un exito: el AP abre el trafico al procesar
    el POST y corta la respuesta a medio camino. Evidencia (logs
    2026-10-13..28): ~170 episodios en que ambos intentos dieron
    IncompleteRead/Remote end closed, se re-boteo el radio y el internet
    volvio SIN ningun 204 despues. Por eso, tras cada intento fallido se
    re-prueba unos segundos antes de reintentar (y antes de que el ciclo
    caiga al bounce de radio, que cuesta ~20-40 s y apaga el WiFi).

    Se suben los reintentos y se baja el timeout para dar mas oportunidades
    al portal ANTES de recurrir al bounce del radio (lo que "apaga el wifi"
    y tarda varios segundos). Cuando el grant falla pero el probe ya da 204,
    cuenta como exito sin bounce.

    Antes de postear, los params deben estar ligados a ESTA interfaz
    (check_params_binding). Si la identidad local no se puede leer, el grant
    queda UNVERIFIED y no se manda nada: fail-closed.
    """
    estado = check_params_binding(params)
    if estado != BIND_OK:
        log.error(
            "grant bloqueado (%s): params no ligados a esta interfaz %s",
            estado, _redact_params(params),
        )
        return False
    for intento in range(retries + 1):
        res = _grant_session_once(params)
        if res is True:
            return True
        if res == "dns":
            # Sin resolucion DNS no hay link: el portal es inalcanzable y los
            # reintentos a ciegas solo retrasan la recuperacion. Esperar a que
            # NM re-asocie (roaming, ~3 s): si vuelve online, listo; si vuelve
            # con captive del portal, reintentar con los params FRESCOS.
            st, loc = _wait_link(DNS_WAIT_SECONDS)
            if st == "online":
                # No hubo grant: la sesion vieja sigue viva tras el roaming.
                # El llamador NO debe re-anclar la ventana de expiracion.
                log.info("link volvio tras fallo de DNS: sesion sigue viva")
                return RECOVERED
            fresh = parse_portal_params(loc) if st == "captive" and is_trusted_portal(loc) else None
            if not fresh or intento >= retries:
                log.warning("grant sin DNS (link caido); no reintento el POST")
                return False
            # Actualiza el dict del LLAMADOR: tras el roaming la IP cambia y
            # el llamador cachea estos params para el proximo grant sin bounce.
            params.clear()
            params.update(fresh)
            if check_params_binding(params) != BIND_OK:
                log.error("params frescos no ligados a esta interfaz; no reintento")
                return False
            continue
        if verify_online(deadline=GRANT_RECHECK_SECONDS, interval=0.5):
            log.info("grant sin respuesta pero el probe ya da 204: sesion otorgada")
            return True
        if intento < retries:
            log.warning("grant fallo (intento %d/%d); reintentando...", intento + 1, retries + 1)
            time.sleep(0.5)
    return False


def _wait_link(deadline, interval=0.5):
    """Sondea hasta `deadline` s; devuelve (estado, location) al ver online/captive."""
    t0 = time.time()
    st, loc = "down", None
    while time.time() - t0 < deadline:
        st, loc = probe()
        if st in ("online", "captive"):
            return st, loc
        time.sleep(interval)
    return st, loc


def _grant_session_once(params):
    """True si 2xx; "dns" si no se pudo resolver el host; False en otro fallo."""
    body = json.dumps(params).encode("utf-8")
    ctx = ssl.create_default_context()
    try:
        conn = http.client.HTTPSConnection(GRANT_HOST, 443, timeout=GRANT_TIMEOUT, context=ctx)
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
    except socket.gaierror as e:
        log.error("grant POST fallo: %s", e)
        return "dns"
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
    """Copia de params con IP/MAC/accname enmascarada para logs.

    accname (p.ej. NEZA-CDMX131207-F1A-A) identifica la zona/AP del cliente:
    tambien se enmascara.
    """
    if not params:
        return params
    out = dict(params)
    if out.get("ip"):
        out["ip"] = _redact_ip(out["ip"])
    if out.get("mac"):
        out["mac"] = _redact_mac(out["mac"])
    if out.get("nombreAcc"):
        out["nombreAcc"] = "<ACC>"
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
    la conexion que el usuario esta usando (oficina, casa, etc.). Un SSID
    imposible de consultar tambien bloquea la recuperacion automatica: no
    asumimos que una red desconocida sea segura para desconectar.
    """
    cur = current_ssid()
    if cur is None:
        log.warning("SSID desconocido; por seguridad no toco el radio ni el perfil")
        return True
    return bool(cur) and cur != SSID


def _windows_wlan_show():
    """`netsh wlan show interfaces` (una sola llamada reutilizable)."""
    try:
        r = _run(["netsh", "wlan", "show", "interfaces"], timeout=15)
        if r.returncode != 0:
            log.debug("netsh wlan show interfaces fallo rc=%s", r.returncode)
            return None
        return r.stdout
    except Exception as e:
        log.debug("no pude consultar interfaces WiFi: %s", e)
        return None


def wifi_connected():
    if IS_WINDOWS:
        # El valor del SSID no se traduce (solo las etiquetas), asi que buscar
        # el SSID en la salida es robusto al idioma de Windows.
        out = _windows_wlan_show()
        return bool(out is not None and SSID in out)
    dev = wifi_device()
    if not dev:
        return False
    try:
        out = _run(["nmcli", "-t", "-f", "GENERAL.STATE", "dev", "show", dev]).stdout
        return "100" in out  # 100 (connected)
    except Exception:
        return False


def _wifi_radio_is_disabled():
    """True solo cuando NetworkManager confirma que el radio esta apagado."""
    try:
        r = _run(["nmcli", "radio", "wifi"])
        return r.returncode == 0 and (r.stdout or "").strip().lower() == "disabled"
    except Exception:
        return False


def current_ssid():
    """SSID REAL de la red WiFi conectada (no el nombre del perfil de NM).

    GENERAL.CONNECTION devuelve el nombre del *perfil* de NetworkManager,
    que el usuario pudo haber renombrado ("Mi Club" != SSID real). El SSID
    verdadero vive en la propiedad 802-11-wireless.ssid del perfil.

    Devuelve ``""`` cuando nmcli/netsh confirma que no hay una red asociada,
    y ``None`` cuando no fue posible averiguarlo. Esa diferencia permite que
    la recuperacion automatica falle cerrada ante un estado desconocido.
    """
    if IS_WINDOWS:
        out = _windows_wlan_show()
        if out is None or not out.strip():
            return None
        for line in out.splitlines():
            s = line.strip()
            # la etiqueta "SSID" es estable en todos los idiomas; la de estado
            # no ("State"/"Estado"), por eso no se usa para filtrar.
            if s.startswith("SSID") and "BSSID" not in s:
                return s.split(":", 1)[-1].strip()
        return ""
    dev = wifi_device()
    if not dev:
        # Algunas versiones/estados de NetworkManager ocultan la interfaz
        # mientras el radio esta apagado. Eso confirma que no hay otra red.
        return "" if _wifi_radio_is_disabled() else None
    try:
        r = _run(
            ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", dev]
        )
        if r.returncode != 0:
            # Con radio desactivado, dev show puede fallar aunque nmcli siga
            # confirmando que no hay una asociacion Wi-Fi que proteger.
            return "" if _wifi_radio_is_disabled() else None
        profile = r.stdout.split(":", 1)[-1].strip()
    except Exception:
        return None
    if profile == "--":
        return ""  # sin conexion activa
    if not profile:
        return "" if _wifi_radio_is_disabled() else None
    # el perfil puede estar renombrado: pedir el SSID que tiene configurado
    try:
        r = _run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", profile])
        ssid = r.stdout.strip()
        if r.returncode == 0 and ssid:
            return ssid
        return None
    except Exception:
        return None


# --- Binding del grant a la interfaz + senales de anomalia -----------------
#
# Que protege cada cosa:
#   * binding (MAC+IP+subred): el grant solo se manda si los params son de
#     ESTA interfaz. Un 302 spoofeado con otra MAC/IP se rechaza. Si la
#     identidad local no se puede leer, el grant queda UNVERIFIED y no se
#     otorga (fail-closed).
#   * OUI del BSSID: filtro de anomalia. NO prueba que el AP sea autentico
#     (la MAC se falsifica); por defecto solo registra/alerta y nunca
#     desconecta, para no pelear con NetworkManager. Con
#     CLUB_REQUIRE_KNOWN_OUI=1 pasa a ser compuerta del grant.
#   * TLS al portal: segundo chequeo tras un 204. Un AP que retransmite deja
#     pasar TLS, asi que tampoco prueba identidad; detecta portales falsos y
#     redes que bloquean TLS.
# La autenticidad del enlace WiFi abierto no se puede demostrar en L2: para
# el plano de datos hace falta VPN (o 802.1X/PMF, que este hotspot no ofrece).

_BSSID_RE = re.compile(r"\b([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\b")

BIND_OK = "ok"
BIND_MISMATCH = "mismatch"
BIND_UNVERIFIED = "unverified"


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def _norm_mac(mac):
    return (mac or "").replace("-", ":").strip().lower()


def _oui_candidates(mac):
    """OUIs posibles de una MAC, incluida la variante sin bit LAA.

    Un CPE Huawei puede reportar 96:90:10 (bit locally-administered puesto)
    cuando su OUI de fabricante es 94:90:10. Se aceptan ambas.
    """
    parts = _norm_mac(mac).split(":")
    if len(parts) != 6:
        return set()
    try:
        first = int(parts[0], 16)
    except ValueError:
        return set()
    return {
        ":".join(parts[:3]).upper(),
        f"{first & 0xFD:02X}:{parts[1].upper()}:{parts[2].upper()}",
    }


def _allowed_ouis():
    raw = os.environ.get(ALLOWED_OUIS_ENV, DEFAULT_ALLOWED_OUIS)
    return {o.strip().upper() for o in raw.split(",") if o.strip()}


def _oui_allowed(mac):
    """Filtro de anomalia, no prueba de identidad: una MAC se falsifica."""
    if _env_bool("CLUB_ALLOW_UNKNOWN_BSSID", False):
        return True
    return bool(_oui_candidates(mac) & _allowed_ouis())


def _require_known_oui():
    return _env_bool(REQUIRE_KNOWN_OUI_ENV, False)


def _allowed_subnets():
    raw = os.environ.get(ALLOWED_SUBNETS_ENV, DEFAULT_ALLOWED_SUBNETS)
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning("subred ignorada en %s: %r", ALLOWED_SUBNETS_ENV, part)
    return nets


def _ip_allowed(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except (ValueError, TypeError):
        return False
    return any(addr in net for net in _allowed_subnets())


def current_bssid():
    """BSSID asociado, o None si no se puede leer (fail-open del filtro)."""
    if IS_WINDOWS:
        out = _windows_wlan_show()
        if not out:
            return None
        for line in out.splitlines():
            s = line.strip()
            if s.lower().startswith("bssid"):
                m = _BSSID_RE.search(s)
                return m.group(1).lower() if m else None
        return None
    dev = wifi_device()
    if not dev:
        return None
    try:
        r = _run(["iw", "dev", dev, "link"], timeout=5)
        if r.returncode == 0:
            m = _BSSID_RE.search(r.stdout or "")
            if m:
                return m.group(1).lower()
    except Exception as e:
        log.debug("iw link fallo: %s", e)
    try:
        out = _run(
            ["nmcli", "-t", "-f", "IN-USE,BSSID", "dev", "wifi", "list"], timeout=10
        ).stdout
        for line in out.splitlines():
            if line.startswith("*"):
                m = _BSSID_RE.search(line.replace("\\:", ":"))
                if m:
                    return m.group(1).lower()
    except Exception as e:
        log.debug("nmcli BSSID fallo: %s", e)
    return None


def _windows_local_identity():
    """(IP, MAC) en Windows; (None, None) si no se pueden parsear."""
    ip = mac = None
    out = _windows_wlan_show()
    if out:
        for line in out.splitlines():
            s = line.strip()
            if s.lower().startswith("bssid"):
                continue
            m = _BSSID_RE.search(s)
            if m:
                mac = m.group(1).lower()
                break
    try:
        r = _run(["ipconfig"], timeout=10)
        for line in (r.stdout or "").splitlines():
            if "IPv4" in line:
                m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
                if m:
                    ip = m.group(1)
                    break
    except Exception as e:
        log.debug("no pude leer IP local (Windows): %s", e)
    return ip, mac


def _local_identity():
    """(IPv4, MAC) de la interfaz WiFi, o (None, None) si no se pueden leer.

    Que devuelva None no autoriza el grant: `check_params_binding` lo trata
    como UNVERIFIED y el llamador falla cerrado.
    """
    if IS_WINDOWS:
        return _windows_local_identity()
    dev = wifi_device()
    if not dev:
        return None, None
    ip = None
    mac = None
    try:
        r = _run(["nmcli", "-g", "IP4.ADDRESS", "dev", "show", dev], timeout=5)
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line:
                ip = line.split("/", 1)[0].strip()
                break
    except Exception as e:
        log.debug("no pude leer IP local: %s", e)
    try:
        # -t no escapa los ':' de la MAC; -g los devuelve como '\:'.
        r = _run(["nmcli", "-t", "-f", "GENERAL.HWADDR", "dev", "show", dev], timeout=5)
        m = _BSSID_RE.search((r.stdout or "").replace("\\:", ":"))
        if m:
            mac = m.group(1).lower()
    except Exception as e:
        log.debug("no pude leer MAC local: %s", e)
    return ip, mac


def check_params_binding(params):
    """ok | mismatch | unverified para los params del portal.

    Requiere MAC local e IP local. Ademas la IP debe caer en una subred Club
    permitida. Si la identidad local no se puede leer, UNVERIFIED: el grant
    NO se otorga.
    """
    if not params:
        return BIND_MISMATCH
    ip = params.get("ip")
    mac = params.get("mac")
    if not (ip and mac):
        return BIND_MISMATCH
    if not _ip_allowed(ip):
        log.warning("params ip=%s fuera de las subredes Club permitidas", _redact_ip(ip))
        return BIND_MISMATCH
    local_ip, local_mac = _local_identity()
    if not local_mac:
        log.warning("no pude verificar la MAC local; grant UNVERIFIED")
        return BIND_UNVERIFIED
    if _norm_mac(mac) != _norm_mac(local_mac):
        log.warning(
            "params mac=%s no es la MAC local=%s",
            _redact_mac(mac), _redact_mac(local_mac),
        )
        return BIND_MISMATCH
    if not local_ip:
        log.warning("no pude verificar la IP local; grant UNVERIFIED")
        return BIND_UNVERIFIED
    if ip != local_ip:
        log.warning("params ip=%s no es la IP local=%s", _redact_ip(ip), _redact_ip(local_ip))
        return BIND_MISMATCH
    return BIND_OK


def _tls_probe():
    """Handshake TLS real contra el portal. True si el certificado valida.

    Un AP que retransmite deja pasar TLS: esto no prueba que el AP sea
    legitimo. Detecta un portal falso que forja el 204 y no termina TLS
    contra el host real, o una red que bloquea TLS.
    """
    ctx = ssl.create_default_context()
    try:
        conn = http.client.HTTPSConnection(GRANT_HOST, 443, timeout=TIMEOUT, context=ctx)
        conn.request("HEAD", "/", headers={"User-Agent": USER_AGENT})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return True
    except ssl.SSLError as e:
        log.warning("TLS a %s fallo (certificado): %s", GRANT_HOST, e)
        return False
    except Exception as e:
        log.warning("TLS a %s fallo: %s", GRANT_HOST, e)
        return False


def _maybe_tls_check(state):
    """Cruza el 204 con TLS cada TLS_CHECK_SECONDS; solo registra la senal."""
    sec = state.setdefault("security", {})
    last = sec.get("tls_checked", "")
    if last and (time.time() - _iso_to_epoch(last)) < TLS_CHECK_SECONDS:
        return True
    sec["tls_checked"] = now_iso()
    ok = _tls_probe()
    if ok:
        sec.pop("tls_unverified", None)
    else:
        sec["tls_unverified"] = now_iso()
        log.warning(
            "probe=online pero TLS al portal fallo: posible 204 falsificado "
            "o red que bloquea TLS (senal, no prueba de AP falso)"
        )
    save_state(state)
    return ok


def _note_untrusted_bssid(state, bssid):
    """Registra un BSSID fuera de la allowlist; no desconecta ni bloquea."""
    sec = state.setdefault("security", {})
    sec["bssid"] = bssid
    if sec.get("untrusted_bssid") != bssid:
        log.warning(
            "BSSID %s (OUI %s) fuera de la allowlist %s: senal de anomalia, "
            "no prueba de AP falso",
            bssid,
            ":".join(bssid.split(":")[:3]).upper(),
            sorted(_allowed_ouis()),
        )
        sec["untrusted_bssid"] = bssid
        sec["untrusted_at"] = now_iso()
        save_state(state)
    state["fail_streak"] = 0


def _note_trusted_bssid(state, bssid):
    sec = state.setdefault("security", {})
    sec["bssid"] = bssid
    if sec.pop("untrusted_bssid", None) is not None:
        sec.pop("untrusted_at", None)
        save_state(state)


def interlock_enabled():
    """True salvo que CLUB_AUTOTALPLAY_INTERLOCK=0 (kill-switch)."""
    return os.environ.get(INTERLOCK_ENV, "1") != "0"


def hotspot_active():
    """True si wifihost tiene un AP activo en esta maquina.

    El estado vive en /run (tmpfs): sin hotspot, o con wifihost detenido, el
    fichero no existe y el interlock no aplica (comportamiento historico).
    """
    try:
        if not os.path.exists(HOTSPOT_STATE_PATH):
            return False
        with open(HOTSPOT_STATE_PATH, "r", encoding="utf-8") as f:
            return "AP_IF=" in f.read()
    except OSError:
        return False


def _wait_wifi_connected(deadline):
    """Espera conexion a NUESTRA red; una red ajena aborta la espera.

    ``wifi_connected()`` solo dice que ALGUN WiFi quedó activo. Tras un
    toggle del radio NetworkManager puede auto-conectar otro perfil conocido;
    aceptarlo hacia que el daemon declarara ``bounced`` estando en otra red.
    """
    end = time.time() + deadline
    while time.time() < end:
        if wifi_connected():
            ssid = current_ssid()
            if ssid == SSID:
                return True
            if ssid and ssid != SSID:
                log.warning(
                    "WiFi conecto a otra red (%r != %r); no la confundo con una recuperacion",
                    ssid, SSID,
                )
                return False
        time.sleep(2)
    return wifi_connected() and current_ssid() == SSID


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
    # Linux: primero activar el perfil objetivo SIN apagar el radio. Ademas de
    # preservar un hotspot propio, evita que NetworkManager auto-conecte otro
    # perfil conocido al volver el rfkill (observado con DANNY 9882/TP-Link).
    # La unica excepcion es el kill-switch explicito del interlock con hotspot.
    hotspot = hotspot_active()
    if interlock_enabled() or not hotspot:
        t0 = time.time()
        if hotspot:
            log.info(
                "interlock: hotspot activo (%s); pruebo 'connection up' sin apagar el radio",
                HOTSPOT_STATE_PATH,
            )
        else:
            log.info("levantando %s con 'nmcli connection up' (radio intacto)", SSID)
        r = _run(["nmcli", "connection", "up", SSID], timeout=45)
        if r.returncode == 0 and _wait_wifi_connected(30):
            log.info("WiFi OK en %.1fs SIN rfkill", time.time() - t0)
            return True
        log.warning("'connection up' no basto (rc=%s)", r.returncode)
        # El usuario pudo cambiar de red mientras nmcli esperaba. No pelear
        # contra esa decision con un rfkill ni con otra activacion.
        if _connected_to_other_ssid():
            return False
        if hotspot:
            log.warning("interlock: ULTIMO recurso: bounce de radio (el hotspot se caera)")
    # Toggle de radio como ULTIMO recurso. `connection up` puede fallar cuando
    # el AP no aparece en el escaneo; el toggle fuerza una asociacion nueva.
    t0 = time.time()
    log.info("re-boteando WiFi: nmcli radio wifi off/on")
    _run(["nmcli", "radio", "wifi", "off"])
    time.sleep(2)
    _run(["nmcli", "radio", "wifi", "on"])
    if _wait_wifi_connected(30):
        log.info("WiFi OK (radio re-botado y reconnectado) en %.1fs", time.time() - t0)
        return True
    # Si NM eligio otra red al volver el radio, respetarla. Si siguio sin red,
    # un ultimo `connection up` dirigido evita esperar al autoconnect.
    if _connected_to_other_ssid():
        return False
    log.warning("radio toggle no reconectó %s; reintento connection up", SSID)
    r = _run(["nmcli", "connection", "up", SSID], timeout=45)
    if r.returncode == 0 and _wait_wifi_connected(30):
        log.info("WiFi OK: %s", r.stdout.strip())
        return True
    log.error(
        "no pude conectar %s (rc=%s) out=%s err=%s",
        SSID, r.returncode, r.stdout.strip(), r.stderr.strip(),
    )
    return False


def verify_online(deadline=45, interval=3.0):
    """Repite el probe hasta que sea online o se acabe el tiempo."""
    t0 = time.time()
    while time.time() - t0 < deadline:
        st, _ = probe()
        if st == "online":
            return True
        time.sleep(interval)
    return False


def next_poll_seconds(state, online):
    """Segundos hasta el proximo probe.

    El portal corta la sesion ~900 s despues de otorgarla (logs 2026-10-13..28:
    p10=899 s, mediana 906 s incluyendo hasta 20 s de lag de sondeo). Con poll
    fijo de 20 s el corte se detectaba ~10 s tarde en promedio; dentro de la
    ventana de expiracion se sondea cada segundo y el grant sale casi al
    instante. Fuera de ella se mantiene el poll largo (red sana) o el rapido
    (red caida).
    """
    if not online:
        return POLL_DOWN_SECONDS
    started = _iso_to_epoch(state.get("session_started") or "")
    if started:
        age = time.time() - started
        if SESSION_SECONDS - EXPIRY_LEAD_SECONDS <= age < SESSION_SECONDS + EXPIRY_TAIL_SECONDS:
            return POLL_EXPIRY_SECONDS
        if age < SESSION_SECONDS - EXPIRY_LEAD_SECONDS:
            # no dormir de mas: despertar justo al abrir la ventana
            return max(POLL_EXPIRY_SECONDS,
                       min(POLL_SECONDS, SESSION_SECONDS - EXPIRY_LEAD_SECONDS - age))
    return POLL_SECONDS


def handle_captive(location, state):
    maybe_autocapture("handle_captive")
    params = parse_portal_params(location)
    if not params:
        log.error("no pude parsear params del portal; location=%s", _redact_location(location))
        return False
    estado = check_params_binding(params)
    if estado != BIND_OK:
        log.error(
            "params del portal no ligados a esta interfaz (%s); no otorgo: %s",
            estado, _redact_params(params),
        )
        return False
    log.info("captive portal detectado -> otorgando sesion: %s", _redact_params(params))
    res = grant_session(params)
    if not res:
        return False
    if res == RECOVERED:
        return RECOVERED
    if verify_online(deadline=40):
        log.info("SESION OTORGADA - internet restaurado")
        state["last_grant_ok"] = now_iso()
        state["last_portal_params"] = params  # cache p/ grant sin bounce
        return True
    log.warning("grant aceptado pero el probe sigue sin 204; intento re-botear WiFi")
    return False


def _iso_to_epoch(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _grant_in_cooldown(state, escalate):
    """True si NO se debe intentar el grant todavia.

    El cooldown existe para no martillar el portal tras un grant FALLIDO. Si
    el ultimo intento salio bien (last_grant_ok >= last_grant_attempt) y la
    sesion se cayo enseguida (portal que corta la respuesta y "otorga" a
    medias: logs 2026-11-03 16:53, 22:43, 22:59), re-grantear de inmediato
    es lo correcto; antes el cooldown mandaba directo al bounce del radio
    (~15 s con el WiFi apagado).
    """
    if escalate:
        return False
    last = state.get("last_grant_attempt", "")
    if not last or (time.time() - _iso_to_epoch(last)) >= COOLDOWN_SECONDS:
        return False
    ok = state.get("last_grant_ok", "")
    if not (ok and _iso_to_epoch(ok) >= _iso_to_epoch(last)):
        return True
    # Una sola vez por ventana: si la sesion recae una y otra vez, el
    # siguiente intento vuelve a respetar el cooldown y cae al bounce.
    prev = state.get("last_relapse_regrant", "")
    if prev and (time.time() - _iso_to_epoch(prev)) < COOLDOWN_SECONDS * 2:
        return True
    state["last_relapse_regrant"] = now_iso()
    log.info("sesion recayo tras un grant OK; re-grant inmediato (sin esperar cooldown)")
    return False


def _try_grant_from_probe(state):
    """Si el probe dice captive, intenta grant. Devuelve True si restauro online."""
    # La recuperacion puede durar decenas de segundos. Revalidar el SSID en
    # cada intento: un 204 de otra red NO prueba que Club se haya recuperado.
    if current_ssid() != SSID:
        return False
    st, loc = probe()
    if st == "online":
        return True
    if st == "captive":
        p = parse_portal_params(loc)
        if (
            p
            and check_params_binding(p) == BIND_OK
            and grant_session(p)
            and verify_online(deadline=20)
        ):
            state["last_grant_ok"] = now_iso()
            state["last_portal_params"] = p  # cache p/ grant sin bounce
            log.info("SESION OTORGADA")
            return True
    return False


def cycle(state):
    """Un ciclo completo del watchdog. Devuelve accion tomada."""
    cycle_started_attempt = state.get("last_grant_attempt", "")
    st, location = probe()

    # BSSID: filtro de anomalia, NO autenticacion. Se evalua en TODOS los
    # estados (tambien online): un AP gemelo puede dar internet sin portal.
    # Por defecto solo registra la senal y NUNCA desconecta (el enlace lo
    # decide NetworkManager); CLUB_REQUIRE_KNOWN_OUI=1 lo vuelve compuerta.
    cur = current_ssid()
    if cur == SSID:
        bssid = current_bssid()
        if bssid and _oui_allowed(bssid):
            _note_trusted_bssid(state, bssid)
        elif bssid:
            _note_untrusted_bssid(state, bssid)
            if _require_known_oui():
                log.warning(
                    "BSSID no permitido y %s=1: no otorgo ni recupero",
                    REQUIRE_KNOWN_OUI_ENV,
                )
                return "untrusted-bssid"
        elif _require_known_oui():
            # Compuerta explicita: si no se puede leer el BSSID, no se asume
            # que sea seguro. Por defecto (sin la variable) es solo una senal.
            log.warning(
                "BSSID ilegible y %s=1: no otorgo ni recupero",
                REQUIRE_KNOWN_OUI_ENV,
            )
            return "bssid-unverified"

    if st == "online":
        _maybe_tls_check(state)
        state["fail_streak"] = 0
        return "online"

    # Guard de red ajena (todo el ciclo): si el WiFi esta en OTRA red
    # (oficina/casa), no ejecutamos NINGUNA recuperacion — el captive seria
    # de otro portal (grant equivocado) y el bounce mataria su conexion.
    # bring_up_wifi repite el guard como defensa en profundidad.
    # `cur` ya se consulto arriba para el chequeo de BSSID.
    if cur is None:
        state["fail_streak"] = 0
        log.warning(
            "probe=%s pero no pude verificar el SSID; omito la recuperacion automatica",
            st,
        )
        return "ssid-unknown"
    if cur and cur != SSID:
        state["fail_streak"] = 0
        log.warning(
            "probe=%s pero el WiFi esta en otra red (%r != %r); no toco nada",
            st, cur, SSID,
        )
        return "other-network"

    # Guard de portal ajeno: un 302 a otro host (spoofeado en la red publica,
    # o un AP gemelo) no dispara grant ni bounce. Sin esto, cualquiera en el
    # segmento podia hacer flapear el radio cada WIFI_RETRY_SECONDS.
    if st == "captive" and not is_trusted_portal(location):
        state["fail_streak"] = 0
        log.warning(
            "probe=captive hacia un host que no es el portal (%s); no toco nada",
            _redact_location(location),
        )
        return "foreign-portal"

    state["fail_streak"] = int(state.get("fail_streak") or 0) + 1
    streak = state["fail_streak"]
    escalate = streak >= FAIL_STREAK_ESCALATE
    log.warning(
        "probe=%s location=%s fail_streak=%d%s",
        st, _redact_location(location), streak, " ESCALANDO (reinicio total)" if escalate else "",
    )

    if st == "captive":
        # Cache de params ANTES del cooldown/grant: la URL del redirect trae
        # ip/mac/accname frescos; si el grant falla o esta en cooldown, no se
        # pierden (el camino "down" los usara despues sin bounce).
        parsed = parse_portal_params(location)
        if parsed:
            bind = check_params_binding(parsed)
            if bind != BIND_OK:
                state["fail_streak"] = 0
                log.error(
                    "params del portal no ligados a esta interfaz (%s); "
                    "no grant ni bounce",
                    bind,
                )
                return f"binding-{bind}"
            state["last_portal_params"] = parsed
        last = state.get("last_grant_attempt", "")
        can_grant = not _grant_in_cooldown(state, escalate)
        if can_grant:
            state["last_grant_attempt"] = now_iso()
            save_state(state)
            res = handle_captive(location, state)
            if res:
                state["fail_streak"] = 0
                return "recovered" if res == RECOVERED else "granted"
            # grant fallo -> cae al re-botear abajo (no nos quedamos esperando)
        else:
            log.info("cooldown de grant activo (%s); sin grant, pruebo bounce", last)
        # grant no disponible o fallo -> cae al bounce YA (sin return precoz)

    elif st == "down" and wifi_connected():
        # "down" con el WiFi ASOCIADO: casi siempre es la sesion del portal
        # que murio (el AP suelta el trafico) — no hace falta re-botear el
        # radio. El portal sigue alcanzable: grant con los ULTIMOS params
        # conocidos (ip/mac/accname estables en la misma AP) restaura la
        # sesion sin desconectar el link. El radio queda para cuando el
        # grant no basto (cae al bounce de abajo).
        cached = state.get("last_portal_params")
        last = state.get("last_grant_attempt", "")
        can_grant = not _grant_in_cooldown(state, escalate)
        if cached and can_grant:
            bind = check_params_binding(cached)
            if bind == BIND_UNVERIFIED:
                state["fail_streak"] = 0
                log.error(
                    "identidad local no verificable; no grant cacheado ni bounce"
                )
                return "binding-unverified"
            if bind != BIND_OK:
                log.warning(
                    "params cacheados no ligan a esta interfaz (%s); cae al bounce",
                    bind,
                )
            else:
                state["last_grant_attempt"] = now_iso()
                save_state(state)
                res = grant_session(cached)
                if res == RECOVERED:
                    state["fail_streak"] = 0
                    return "recovered"
                if res and verify_online(deadline=15):
                    state["last_grant_ok"] = now_iso()
                    state["last_portal_params"] = cached
                    state["fail_streak"] = 0
                    log.info("SESION OTORGADA (params cacheados; radio intacto)")
                    return "granted-cached"
                # grant fallo (portal inalcanzable?) -> cae al bounce
        elif not cached:
            log.info("sin params cacheados del portal; cae al bounce")

    # "down" o grant fallido: quizas el WiFi se cayo. Re-botear con cooldown.
    if not wifi_connected():
        log.warning("WiFi no conectado (ssid=%r)", current_ssid())
    elif probe()[0] == "online":
        # Ultima oportunidad barata ANTES de apagar el radio: si mientras
        # tanto NM re-asocio (roaming) o el grant tardo en surtir efecto,
        # el probe ya da 204 y el bounce solo cortaria la conexion.
        state["fail_streak"] = 0
        # Solo es "granted" si este ciclo SI mando un grant (que surtio
        # efecto tarde); si no, el link volvio solo y no hay sesion nueva.
        attempted = state.get("last_grant_attempt", "") != cycle_started_attempt
        log.info("restaurado sin bounce (re-probe previo al radio)")
        return "granted" if attempted else "recovered"
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
            cur = current_ssid()
            if cur and cur != SSID:
                state["fail_streak"] = 0
                log.warning(
                    "recuperacion abortada: el WiFi quedo en otra red (%r != %r)",
                    cur, SSID,
                )
                return "other-network"
            if _try_grant_from_probe(state):
                state["fail_streak"] = 0
                log.info("internet restaurado tras reconexion de WiFi")
                return "bounced"
            time.sleep(3)
    cur = current_ssid()
    if cur and cur != SSID:
        state["fail_streak"] = 0
        log.warning("recuperacion cancelada: el WiFi esta en otra red (%r)", cur)
        return "other-network"
    return "failed"


def print_status():
    st, loc = probe()
    print(f"probe      : {st}")
    print(f"location   : {_redact_location(loc) if loc else '-'}")
    print(f"wifi       : {'conectado' if wifi_connected() else 'DESCONECTADO'} (dev={wifi_device() or '-'})")
    print(f"ssid       : {current_ssid() or '-'}")
    params = parse_portal_params(loc) if loc else None
    if params:
        print(f"portal     : {json.dumps(_redact_params(params))}")
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

    def h(k, v):
        """Header para debug: cookies y authorization se enmascaran con redact."""
        if not redact:
            return v
        kl = k.lower()
        if kl in ("set-cookie", "cookie", "authorization"):
            return "(redactado)"
        if kl == "location":
            return _redact_location(v)
        return v

    p("platform", sys.platform)
    p("IS_WINDOWS", IS_WINDOWS)
    p("STATE_PATH", STATE_PATH)
    try:
        proxies = urllib.request.getproxies()
    except Exception as e:
        proxies = {"_error": str(e)}
    if redact:
        # Proxy URLs can contain usernames/passwords or tokens. The automatic
        # capture only needs to show which proxy classes are configured.
        proxies = {k: "<configurado>" for k, v in proxies.items() if v}
    p("urllib.getproxies()", json.dumps(proxies) if proxies else "(vacio)")
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        if os.environ.get(var):
            p(f"env {var}", "<configurado>" if redact else os.environ[var])
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
        p("probe_location", h("Location", loc) if redact else (loc or "-"))
        p("probe_body", repr(body))
        for k, v in resp.headers.items():
            p(f"probe_hdr {k}", h(k, v))
        probe_st = "online" if code == 204 else ("captive" if loc else "down")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        p("probe_status", f"HTTPError {e.code}")
        p("probe_location", h("Location", loc) if redact else (loc or "-"))
        for k, v in e.headers.items():
            p(f"probe_hdr {k}", h(k, v))
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
    # Solo si el redirect es el portal real: no seguir URLs arbitrarias que
    # un tercero haya inyectado en el 302 de HTTP plano.
    if loc and params and perform_grant:
        try:
            g = urllib.request.Request(loc, headers={"User-Agent": USER_AGENT})
            gr = urllib.request.build_opener().open(g, timeout=TIMEOUT)
            gb = gr.read()[:300]
            p("portalGET_status", gr.getcode())
            for k, v in gr.headers.items():
                if k.lower() in ("location", "set-cookie", "content-type"):
                    p(f"portalGET_hdr {k}", h(k, v))
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
                p(f"grant_hdr {k}", h(k, v))
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
