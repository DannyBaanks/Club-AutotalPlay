<div align="center">

# Club-AutotalPlay

**Auto-reconnect para la red pública `Club_Totalplay_WiFi` (Totalplay).**

Icono en la bandeja · reconexión sola · sin browser ni video.

[![Linux](https://img.shields.io/badge/platform-Linux-blue?logo=linux&logoColor=white)](#)
[![Windows](https://img.shields.io/badge/platform-Windows-blue?logo=windows&logoColor=white)](#)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#)

</div>

---

## ¿Qué hace?

La red de Totalplay otorga sesiones de ~15 minutos (900 s exactos, medido en
15 días de logs). Cuando expira, **corta el
internet** y redirige todo el tráfico a un portal cautivo que te hace ver un
video de publicidad. Al terminar, tu navegador manda un `POST` que otorga la
sesión nueva — y vuelves a tener internet.

**Este proyecto hace ese `POST` automáticamente**, sin browser ni video:

1. Cada ~20 s sondea `http://connectivitycheck.gstatic.com/generate_204` — y
   **cada 1 s** alrededor del minuto 15 de la sesión, cuando el portal corta.
2. Si responde `302` (hubo corte), parsea IP / MAC / `accname` del `Location` del portal.
3. Manda el `POST` a `/ClubMovil/video-completado` → `204` = sesión otorgada.
   Si el portal corta la respuesta (`IncompleteRead`) pero el sondeo ya da
   `204`, cuenta como otorgada — sin re-botear el radio.
4. Si el sondeo se cae del todo pero el WiFi sigue asociado, reintenta ese
   `POST` con los últimos parámetros conocidos — **sin tocar el radio**.
5. Si ni eso basta, re-botea el WiFi por `nmcli` como último recurso.

Todo en segundo plano, con un **icono en la bandeja** que cambia de color:

| Color | Estado |
|---|---|
| 🟢 Verde | en línea |
| 🟠 Ámbar | reconectando |
| 🔴 Rojo | sin conexión |

> Probado en Ubuntu 24.04 (GNOME, X11) y en Windows. Se desconecta, el icono
> se pone rojo, renueva la sesión sola y vuelve a verde — normalmente en
> **1-4 segundos**.

**¿Es seguro en una red pública?** La app solo confía en el portal real de
Totalplay (HTTPS con certificado verificado), ignora redirects falsos y
guarda sus datos con permisos privados. Ver
[Seguridad en red pública](#seguridad-en-red-pública).

> [!IMPORTANT]
> **Activa el firewall de tu equipo antes de usar esta red.** `Club_Totalplay_WiFi`
> es abierta: cualquiera conectado puede mandarle paquetes a tu computadora.
> La app no puede protegerte de eso; un firewall bien configurado sí lo limita.
> Comandos en [Protege tu equipo: firewall](#protege-tu-equipo-firewall).

## Descargar y probar

### Linux (AppImage)
1. Baja el **AppImage** de [Releases](../../releases) (`Club-AutotalPlay-x86_64.AppImage`).
2. Hazlo ejecutable y lánzalo:
   ```bash
   chmod +x Club-AutotalPlay-x86_64.AppImage
   ./Club-AutotalPlay-x86_64.AppImage      # icono en la bandeja + daemon
   ```
3. (Opcional) Para que aparezca en el app grid de GNOME, copia el `.desktop`
   y el icono — o instala el servicio para que corra solo al iniciar sesión:
   ```bash
   ./Club-AutotalPlay-x86_64.AppImage install   # servicio systemd user
   ```

> **Nota AppImage:** si tu distro no monta AppImages, instala FUSE:
> `sudo apt install libfuse2t64` (Debian/Ubuntu) o `sudo dnf install fuse-libs` (Fedora).

### Windows (.exe)

Hay **dos** `.exe` en [Releases](../../releases):

| .exe | Para qué |
|---|---|
| `Club-AutotalPlayTray.exe` | **La app.** Icono en la bandeja (verde/ámbar/rojo) + daemon en segundo plano. Doble click = corriendo. |
| `Club-AutotalPlay.exe` | **CLI de consola.** Para inspeccionar o correr a mano. |

1. Baja ambos yjalos en la misma carpeta.
2. Doble click a `Club-AutotalPlayTray.exe` → aparece el icono junto al reloj
   y el auto-reconnect empieza a sondear cada ~20 s.
3. Para que arranque **solo al iniciar sesión**:
   ```bat
   Club-AutotalPlay.exe install     :: crea el acceso directo de inicio
   Club-AutotalPlay.exe uninstall   :: lo quita
   ```
   Es reversible y no borra nada. El acceso directo queda en la carpeta de
   inicio (`shell:startup`), visible en *Administrador de tareas → Apps de
   inicio*, donde puedes desactivarlo.

**CLI de consola** (todo desde una terminal):

```bat
Club-AutotalPlay.exe status        :: estado (lee/red)
Club-AutotalPlay.exe status --live :: fuerza sondeo real
Club-AutotalPlay.exe once          :: un ciclo de reconexión
Club-AutotalPlay.exe run           :: daemon en foreground (sin bandeja)
Club-AutotalPlay.exe debug         :: diagnóstico (IP/MAC/accname redactados)
Club-AutotalPlay.exe debug --dry   :: solo el probe (sin tocar el portal)
Club-AutotalPlay.exe debug --full  :: sin redactar (para tu propio análisis)
Club-AutotalPlay.exe install       :: autostart al iniciar sesión
Club-AutotalPlay.exe uninstall     :: quita el autostart
```

> **Nota de diagnóstico.** En Windows el portal de Totalplay se comporta igual
> que en Linux: el `POST` a `/ClubMovil/video-completado` devuelve `204` y
> otorga la sesión. El motivo típico de "no me reconecta sola" **no** es el
> HTTP, es que **nadie deja el daemon corriendo** (no hay autostart ni bandeja
> y Windows exige el paso manual de *Acción necesaria*). Por eso `install` +
> la bandeja son lo que lo deja 1:1 con Linux.
>
> Si aun así falla, `Club-AutotalPlay.exe debug` imprime el estado crudo de
> cada paso (proxy del sistema, `Location` del portal, `GET` a la página, y la
> respuesta del `POST`). El daemon además se autovuelca esto al log la primera
> vez que ve un captive portal: léelo en
> `%LOCALAPPDATA%\club-autotalplay\watchdog.log`.

- Log: `%LOCALAPPDATA%\club-autotalplay\watchdog.log`
- Estado: `%LOCALAPPDATA%\club-autotalplay\state.json`

## Uso

```bash
# estado (lee el daemon, sin re-probar la red)
./Club-AutotalPlay-x86_64.AppImage status

# estado con sondeo real de la red
./Club-AutotalPlay-x86_64.AppImage status --live

# un solo ciclo de reconexión (para probar)
./Club-AutotalPlay-x86_64.AppImage once

# servicio systemd (corre solo al iniciar sesión)
systemctl --user status club-autotalplay
journalctl --user -u club-autotalplay -f     # logs en vivo
```

El icono de la bandeja tiene menú: **Reconectar ahora**, **Ver estado**, **Salir**.

`status` y `status --live` enmascaran IP, MAC y el identificador de zona del
portal. El estado local conserva los últimos parámetros necesarios para
recuperar la sesión y se guarda con permisos privados en Linux.

## Instalar / desinstalar el servicio

```bash
./Club-AutotalPlay-x86_64.AppImage install     # activa el servicio user
./Club-AutotalPlay-x86_64.AppImage uninstall   # lo remueve
```

`install` es reversible y no borra nada. (Si venías del watchdog viejo
`clubwifi-watchdog`, `install` lo desactiva como respaldo y `uninstall` lo
restaura.)

## Compilar desde el código

```bash
git clone https://github.com/DannyBaanks/Club-AutotalPlay.git
cd Club-AutotalPlay
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[gui]'                 # gui = pystray + Pillow + python-xlib
./packaging/build_appimage.sh           # genera dist/Club-AutotalPlay-x86_64.AppImage
```

**Windows** (consola + bandeja, PowerShell):

```powershell
pip install -e . pyinstaller pystray Pillow

# 1) .exe de consola (CLI)
pyinstaller --noconfirm --clean --onefile --name Club-AutotalPlay `
  --distpath dist --workpath build/pyi-work --specpath build --paths src `
  packaging/console_entry.py

# 2) .exe de bandeja (windowed; el ; es el separador de --add-data en Windows)
#    Sin --specpath: PyInstaller resuelve el --add-data contra el .spec, y si
#    el .spec vive en build/ la ruta relativa "src/..." no existiria ahi.
pyinstaller --noconfirm --clean --onefile --windowed --name Club-AutotalPlayTray `
  --add-data "src/club_autotalplay/assets;club_autotalplay/assets" `
  --hidden-import pystray._win32 --collect-all pystray --collect-all PIL `
  --distpath dist --workpath build/pyi-work-tray --paths src `
  packaging/tray_entry.py
```

> El CI construye ambas plataformas en cada push/PR:
> `.github/workflows/windows-exe.yml` (los dos `.exe`) y
> `.github/workflows/linux-appimage.yml` (el AppImage). Al haber tag, ambos
> suben sus artefactos al Release.

## Requisitos

- **Linux:** NetworkManager (`nmcli`). El core depende de él.
- **Windows:** nada extra (usa `netsh`); los dos `.exe` son standalone.
- Python ≥ 3.9 (el core es solo stdlib; la app de escritorio usa `pystray`+`Pillow`, ya empaquetados en los `.exe`).
- GNOME/X11 para el icono de bandeja Linux (en Wayland puede requerir la extensión AppIndicator). En Windows la bandeja usa el backend win32 nativo de `pystray` (sin dependencias).

## Cómo funciona el portal (detalles)

```
GET  http://connectivitycheck.gstatic.com/generate_204
  → 302 Location: https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp
                ?wlanuserip=<IP>&wlanparameter=<MAC>&accname=<ACC>

POST https://clubwifi.totalplay.com.mx/ClubMovil/video-completado
     {"ip":…, "mac":…, "nombreAcc":…, "tipo":"DESKTOP"}
  → 204 No Content   (sesión otorgada)
```

- La sesión dura **~900 s** desde el grant; después el portal vuelve a
  secuestrar el tráfico.
- El `POST` muchas veces **no regresa respuesta** (`IncompleteRead` /
  `Remote end closed`) aunque sí otorgó la sesión: el AP abre el tráfico a
  medio camino y corta la conexión. Por eso la app re-prueba unos segundos
  antes de considerarlo fallido.
- Solo se aceptan redirects a `https://clubwifi.totalplay.com.mx`.

## Pruebas e2e en Linux

Todo lo de esta sección se capturó en Ubuntu 24.04 (GNOME, X11) contra la red
`Club_Totalplay_WiFi`, con el servicio systemd user corriendo el código del
repo. Los valores de red (IP / MAC / `accname`) van enmascarados.

### 0. Lo más reciente: de ~21 s a ~3 s por corte (2026-10-28)

Análisis de `watchdog.log` del 2026-10-13 al 28 (~650 cortes reales) con la
versión anterior:

| Cómo terminó el corte | Veces | Mediana | p90 |
|---|---|---|---|
| grant directo (`granted`) | 279 | 0.9 s | 3.3 s |
| re-booteo de radio (`bounced`) | 341 | 20.9 s | 42.6 s |

Más de la mitad acababa en bounce, y en **~170** de esos el `POST` "falló"
sin respuesta, se re-booteó el radio y el internet volvió **sin ningún 204
posterior**: el grant sí había funcionado. Ahora la app re-prueba antes de
tocar el radio. Corte real con la versión nueva:

```
18:40:02 WARNING probe=captive location=https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp?...
18:40:03 INFO    captive portal detectado -> otorgando sesion: {'ip': '10.x.x.x', ...}
18:40:05 ERROR   grant POST fallo: Remote end closed connection without response
18:40:05 INFO    grant sin respuesta pero el probe ya da 204: sesion otorgada
18:40:05 INFO    SESION OTORGADA - internet restaurado
18:40:05 INFO    ciclo: granted
```

**Detección → restaurado en 3.4 s, sin bounce** (antes: 20-40 s).

Siguiente corte, ya con el sondeo de 1 s en la ventana de expiración (la
sesión anterior se otorgó a las 18:40:05; el corte llegó a los 917 s):

```
18:55:22.522 WARNING probe=captive location=https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp?...
18:55:22.579 INFO    captive portal detectado -> otorgando sesion: {'ip': '10.x.x.x', ...}
18:55:22.968 ERROR   grant POST fallo: Remote end closed connection without response
18:55:23.003 INFO    grant sin respuesta pero el probe ya da 204: sesion otorgada
18:55:23.072 INFO    ciclo: granted
```

**Detección → restaurado en 0.55 s.** Con sondeo cada 1 s en esa ventana, el
retraso de detección queda acotado a ~1 s (antes, hasta 20 s; ~10 s en
promedio). El daemon no registra los probes que dan `online`, así que ese
retraso se deduce del intervalo, no está medido línea por línea.

### 1. Un corte real: detección → bounce → grant → 204 → restaurado

La sesión expiró y el daemon lo detectó solo. Salida literal del log
(`journalctl --user -u club-autotalplay`):

```
19:02:09 WARNING probe=down location=None fail_streak=1
19:02:10 INFO    re-boteando WiFi: nmcli radio wifi off/on
19:02:27 INFO    WiFi OK (radio re-botado y reconnectado)
19:02:33 INFO    grant POST -> HTTP 204 No Content body=b''
                                     params={'ip': '10.x.x.x', 'mac': 'xx-xx-xx-xx-xx-xx',
                                             'nombreAcc': '<ACC>', 'tipo': 'DESKTOP'}
19:02:33 INFO    SESION OTORGADA
19:02:33 INFO    internet restaurado tras bounce de WiFi
19:02:34 INFO    ciclo: bounced
```

**Detección → recuperado en ~24 segundos.** El mismo corte, antes de los
fixes de sonda rápida / `fail_streak` / grant post-bounce, tardaba ~3.5
minutos (y a veces quedaba atascado hasta un toggle manual del WiFi).

### 2. Detección sin sondeo lento

El poll es adaptativo: **5 s** mientras la red no está online, **20 s** cuando
lo está, y **1 s** en la ventana de expiración: el portal corta ~900 s después
de otorgar la sesión (logs 2026-10-13..28: p10 = 899 s, mediana 906 s), así
que de los 880 s a los 960 s el daemon sondea cada segundo y el grant sale
casi en cuanto llega el corte.

### 3. Escalamiento a reinicio total (`fail_streak`)

Si **5 ciclos seguidos** quedan sin online (`FAIL_STREAK_ESCALATE`), el
daemon ignora ambos cooldowns (grant y bounce) y fuerza un reinicio completo
de la sesión + radio de inmediato — la red de seguridad contra caídas que el
bounce solo no alcanza a sanar.

### 4. Servicio systemd user + tray

```
$ systemctl --user status club-autotalplay
● club-autotalplay.service - Club-AutotalPlay auto-reconnect
   Active: active (running)

$ ./Club-AutotalPlay-x86_64.AppImage install   # reversible
```

`install` crea el servicio user y desactiva el watchdog viejo
(`clubwifi-watchdog`) como respaldo; `uninstall` lo restaura. El icono de la
bandeja cambia 🟢/🟠/🔴 según el `state.json` que escribe el daemon.

### Cómo reproducir el e2e (Linux)

```bash
# 1. deja el servicio corriendo
./Club-AutotalPlay-x86_64.AppImage install
journalctl --user -u club-autotalplay -f

# 2. espera a que expire la sesión (~15 min) y revisa el log:
#    Debes ver: probe=down/captive -> bounce/grant -> HTTP 204 -> SESION OTORGADA

# 3. diagnostico puntual
./Club-AutotalPlay-x86_64.AppImage status --live
./Club-AutotalPlay-x86_64.AppImage once
```

## Pruebas e2e en Windows

Todo lo de esta sección se capturó en una máquina Windows real contra la red
`Club_Totalplay_WiFi`, con el `.exe` empaquetado (no con `python -m`).
Los valores de red (IP / MAC / `accname`) van enmascarados.

### 1. Un corte real: detección → grant → 204 → internet restaurado

Con el daemon corriendo, la sesión expiró y el portal secuestró el tráfico.
Salida literal del log (`%LOCALAPPDATA%\club-autotalplay\watchdog.log`):

```
18:33:32 WARNING probe=captive location=https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp
                                    ?wlanuserip=10.x.x.x&wlanacname=
                                    &wlanparameter=xx-xx-xx-xx-xx-xx&accname=<ACC>
18:33:33 INFO    captive portal detectado -> otorgando sesion:
                                    {'ip': '10.x.x.x', 'mac': 'xx-xx-xx-xx-xx-xx',
                                     'nombreAcc': '<ACC>', 'tipo': 'DESKTOP'}
18:33:34 INFO    grant POST -> HTTP 204 No Content body=b''
18:33:34 INFO    SESION OTORGADA - internet restaurado
18:33:34 INFO    ciclo: granted
```

Y el `state.json` quedó con el timestamp del grant:

```json
"last_grant_ok": "2026-10-12T18:33:34-06:00"
```

Es decir: el `POST` a `/ClubMovil/video-completado` devuelve **`204`** en
Windows igual que en Linux. **La lógica HTTP no necesita browser ni video.**

### 2. Sin ventana de consola cada 20 s

Antes del fix, la bandeja (proceso *windowed*, sin consola propia) spawneaba
cada `netsh` con una **consola visible nueva** — una ventana que parpadeaba
cada ciclo de sondeo. Se arregló pasando `CREATE_NO_WINDOW` a `subprocess`.

Verificación: se muestreó el proceso `netsh` durante 28 s (cubriendo un ciclo
completo del daemon). Atrapó 2 procesos — los 2 que hace el daemon por ciclo
— y **ninguno tenía ventana**:

```
=== netsh capturados durante 28s: 2 ===
con VENTANA VISIBLE: 0
=> NINGUNO con ventana visible (CREATE_NO_WINDOW funciona)
```

### 3. `install` / `uninstall` (autostart)

```
== instalando autostart (Windows) ==
  target : C:\...\Club-AutotalPlayTray.exe
  args   : (ninguno)
  lnk    : C:\Users\<u>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Club-AutotalPlay.lnk
  OK: la app (bandeja + daemon) arrancara al iniciar sesion
```

El `.lnk` creado apunta al `.exe` de bandeja con argumentos vacíos (verificado
leyendo el shortcut: `TargetPath` = el `.exe` de bandeja, `Arguments` = `''`).
`uninstall` lo elimina.

### 4. Guard de doble-daemon

Con un daemon ya vivo (desde la consola), se lanzó la bandeja. No spawneó un
segundo daemon — solo leyó el estado:

```
18:47:01 INFO tray arrancando (daemon en background)
18:47:01 INFO daemon ya corriendo; el tray solo lee el estado
```

Y con la bandeja spawneando su propio daemon (sin otro vivo):

```
18:58:16 INFO tray arrancando (daemon en background)
18:58:16 INFO daemon iniciado (poll=20s, ssid=Club_Totalplay_WiFi, dev=None, pid=22348)
```

### 5. Bug de locale corregido (`bring_up_wifi`)

El log original mostraba un **falso fallo** en un éxito real:

```
18:48:56 ERROR netsh fallo rc=0 out=La solicitud de conexión se completó correctamente.
```

`netsh` devolvió `rc=0` (éxito) y el mensaje en español no matcheaba los
tokens `"se conect"` / `"connected"` con los que se decidía el resultado. Ahora
se confía en `rc==0`, igual que en el branch de Linux.

### Cómo reproducir el e2e

```bat
:: 1. deja el daemon corriendo
Club-AutotalPlay.exe run

:: 2. espera a que expire la sesión (~15 min) y revisa el log
::    %LOCALAPPDATA%\club-autotalplay\watchdog.log
::    Debes ver: probe=captive -> grant POST -> HTTP 204 -> SESION OTORGADA

:: 3. diagnostico puntual si algo se desvía
Club-AutotalPlay.exe debug
```

## Seguridad en red pública

`Club_Totalplay_WiFi` es una red **abierta**: el tráfico entre tu equipo y el
AP no va cifrado y cualquiera conectado puede intentar hablarle a tu máquina.

**Lo que hace la app para no ser el eslabón débil:**

- **Solo confía en el portal real.** El sondeo de captive viaja por HTTP plano
  (así funciona en todos los sistemas), y alguien en la red puede falsificar
  ese `302`. La app solo acepta redirects a `https://clubwifi.totalplay.com.mx`;
  cualquier otro host se ignora (`foreign-portal`): no extrae datos, no hace
  `POST` y **no re-botea el radio**. Así nadie puede hacerte flapear el WiFi
  con redirects falsos.
- **Valida lo que manda.** IP privada IPv4, MAC bien formada y zona sin
  caracteres de control; lo demás no sale de tu equipo.
- **El `POST` va por HTTPS con certificado verificado**, directo al host fijo
  del portal (no al que diga el redirect).
- **Datos locales cerrados.** El directorio de estado queda `0700` y
  `state.json` `0600` (Linux). Logs y `status` enmascaran IP, MAC y zona.
- **Nunca toca otra red.** Si estás en otro WiFi, o no se puede saber en cuál,
  no hace nada.

### Protege tu equipo: firewall

> [!IMPORTANT]
> En una red abierta, **cualquiera conectado al mismo WiFi puede intentar
> conectarse a tu computadora**: SSH, servidores de desarrollo, carpetas
> compartidas, etc. Un firewall con política de **negar lo entrante** bloquea
> las conexiones que no hayas permitido explícitamente. Lo que queda abierto
> depende de las **reglas efectivas**: sus excepciones y la interfaz/zona de
> cada red. Por eso cada sección de abajo termina con una verificación.
> Club-AutotalPlay sigue funcionando con el firewall activo (solo hace
> conexiones salientes).

> [!CAUTION]
> **¿Administras tu equipo a distancia (SSH, escritorio remoto, Tailscale)?**
> Revisa tus reglas **antes** de activar o endurecer el firewall y deja
> permitido el acceso que necesitas, o puedes quedarte sin entrada.

**Linux (Ubuntu / Debian / Mint)** — `ufw`:

```bash
sudo ufw status numbered        # 1. revisa las reglas que ya existen (excepciones)
sudo ufw default deny incoming  # 2. política explícita: negar lo entrante...
sudo ufw default allow outgoing #    ...y permitir lo saliente
sudo ufw enable                 # 3. activa (también al arrancar el sistema)
sudo ufw status verbose         # 4. verifica: "Estado: activo" y "deny (entrantes)"
```

En el paso 4, cada línea `ALLOW IN` de la tabla es una excepción que **sigue
abierta**: confirma que todas sean intencionales. Si entras por Tailscale,
`sudo ufw allow in on tailscale0` la limita a esa interfaz.

> [!WARNING]
> Que `systemctl status ufw` diga *active* **no** significa que el firewall
> filtre: puede estar instalado pero deshabilitado. Lo que cuenta es que
> `sudo ufw status` diga **Estado: activo**.

**Linux (Fedora / RHEL)** — `firewalld`:

Que firewalld corra y la zona sea `public` **no** demuestra que todo esté
bloqueado: `public` trae servicios permitidos de fábrica (en Fedora, `ssh`
y `dhcpv6-client`), y Fedora Workstation asigna por defecto la zona
`FedoraWorkstation`, que abre los puertos 1025-65535. Lo que importa es la
zona **de la interfaz WiFi** y lo que esa zona permite:

```bash
sudo systemctl enable --now firewalld
sudo firewall-cmd --get-active-zones                 # 1. ¿qué zona tiene tu WiFi?
nmcli connection show --active                       # 2. nombre de tu conexión WiFi
#    (el nombre de la conexión puede no ser el SSID; usa el que salga ahí)
nmcli connection modify "<conexión>" connection.zone public   # 3. asígnale public
nmcli connection up "<conexión>"
sudo firewall-cmd --zone=public --list-all           # 4. revisa services: y ports:
# 5. quita lo que no necesites recibir desde esta red, p.ej.:
sudo firewall-cmd --zone=public --remove-service=ssh --permanent
sudo firewall-cmd --reload
sudo firewall-cmd --get-active-zones                 # 6. verifica: tu WiFi en public
sudo firewall-cmd --zone=public --list-all           #    y solo lo intencional permitido
```

Más estricto: la zona `block` rechaza todo lo entrante salvo respuestas a
conexiones que tú iniciaste (`connection.zone block`).

**Windows** — PowerShell **como administrador**:

```powershell
# 1. Encuentra el perfil de TU WiFi. Su Name no siempre es el SSID:
Get-NetConnectionProfile | Format-Table InterfaceIndex, InterfaceAlias, Name, NetworkCategory

# 2. Márcalo como Público usando el InterfaceIndex que viste arriba (ej. 12)
Set-NetConnectionProfile -InterfaceIndex 12 -NetworkCategory Public

# 3. Firewall encendido en todos los perfiles
Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True

# 4. Verifica: tu WiFi en Public; Enabled = True; DefaultInboundAction = Block
#    (NotConfigured también equivale a Block por defecto)
Get-NetConnectionProfile | Format-Table InterfaceAlias, Name, NetworkCategory
Get-NetFirewallProfile | Format-Table Name, Enabled, DefaultInboundAction, AllowInboundRules
```

`AllowInboundRules = True` significa que las reglas de "apps permitidas"
siguen aplicando en ese perfil. Para que el perfil Público ignore esas
excepciones (equivale a *"Bloquear todas las conexiones entrantes"*):
`Set-NetFirewallProfile -Profile Public -AllowInboundRules False`.

Sin terminal: *Configuración → Red e Internet → Wi-Fi → (tu red) → Tipo de
perfil de red: **Pública***, y *Seguridad de Windows → Firewall y protección
de red*: los tres perfiles **activados**.

**Revisa qué está escuchando** (lo que quedaría expuesto sin firewall):

- Linux: `ss -tulnp` — todo lo que diga `0.0.0.0`, `*` o `[::]` acepta
  conexiones de la red.
- Windows: `netstat -ano | findstr LISTENING`

**Además:**

- **No expongas servicios en `0.0.0.0`** sin necesidad (servidores de
  desarrollo, SSH con contraseña); usa `127.0.0.1`.
- **Solo HTTPS**, o una VPN (Tailscale/WireGuard) para lo sensible.
- Un AP falso con el mismo SSID es indistinguible en una red abierta: la app
  no le entrega nada útil (el `POST` exige el certificado del portal real),
  pero tu demás tráfico sí pasa por él. Por eso lo de arriba.

## Tests

```bash
.venv/bin/python -m pytest -q        # 62 tests, sin red real
```

Cada test corre con `STATE_DIR` / `state.json` / `watchdog.log` en un
directorio temporal (`tests/conftest.py`): la suite nunca toca el estado ni
el log reales del daemon. El CI los corre en cada push/PR.

## Aviso / Disclaimer

La respuesta `204` del portal demuestra que el servidor acepta la solicitud;
**no demuestra por sí sola que automatizar ese procedimiento de forma
indefinida esté permitido por los Términos y Condiciones de Totalplay**.

Este proyecto es una herramienta personal de conveniencia, publicada para que
otros la prueben bajo su propio criterio. Antes de depender de ella o
recomendarla públicamente, verifica con el proveedor que el uso automatizado
del portal es consistente con sus condiciones del servicio. El uso queda bajo
responsabilidad de quien la ejecuta.

## Licencia

MIT
