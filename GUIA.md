# GUIA — Club-AutotalPlay

App que mantiene viva la sesión de la red pública `Club_Totalplay_WiFi`
(Totalplay). Detecta el captive portal y otorga la sesión sola (el mismo
`POST` que hace el navegador al terminar el video), sin browser ni video.
Corre como daemon en segundo plano con icono en la bandeja.

> [!IMPORTANT]
> **Activa el firewall de tu equipo antes de usar esta red** (es abierta).
> Comandos para Linux y Windows en
> [Protege tu equipo: firewall](#protege-tu-equipo-firewall).

---

## Windows

### La app (icono en la bandeja)

Doble click a **`Club-AutotalPlayTray.exe`**. Aparece un icono junto al reloj
que cambia de color:

| Color | Estado |
|---|---|
| 🟢 Verde | en línea |
| 🟠 Ámbar | reconectando |
| 🔴 Rojo | sin conexión |

Click izquierdo al icono abre el menú: **Reconectar ahora** / **Ver estado** /
**Salir**.

### Que arranque sola al iniciar sesión

```bat
Club-AutotalPlay.exe install
```

Salida real:

```
== instalando autostart (Windows) ==
  target : C:\...\Club-AutotalPlayTray.exe
  args   : (ninguno)
  lnk    : C:\Users\<tu-usuario>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Club-AutotalPlay.lnk
  OK: la app (bandeja + daemon) arrancara al iniciar sesion
```

Quitarlo:

```bat
Club-AutotalPlay.exe uninstall
```

`install`/`uninstall` son reversibles y no borran nada. El acceso directo
también puedes desactivarlo en *Administrador de tareas → Apps de inicio*.

### CLI de consola (`Club-AutotalPlay.exe`)

```bat
Club-AutotalPlay.exe status        :: estado (lee el daemon, o prueba la red)
Club-AutotalPlay.exe status --live :: fuerza sondeo real
Club-AutotalPlay.exe once          :: un ciclo de reconexión
Club-AutotalPlay.exe run           :: daemon en foreground (sin bandeja)
Club-AutotalPlay.exe debug         :: vuelca probe/portal/grant en crudo
Club-AutotalPlay.exe debug --dry   :: solo el probe (sin tocar el portal)
```

### Diagnóstico (`debug`)

Si algo falla, `debug` imprime el estado crudo de cada paso: si el probe ve el
`302`, el `Location` del portal, si hay proxy del sistema, la respuesta del
`GET` a la página del portal y del `POST` a `/video-completado`.

Además el daemon **se autovuelca esto al log** la primera vez que detecta un
captive portal, así que basta con esperar un corte y leer el log.

`status` y `status --live` enmascaran IP, MAC y el identificador de zona del
portal. La autocaptura también oculta los valores de proxy, que pueden llevar
credenciales en su URL.

### Archivos (Windows)

| Qué | Dónde |
|---|---|
| Log | `%LOCALAPPDATA%\club-autotalplay\watchdog.log` |
| Estado | `%LOCALAPPDATA%\club-autotalplay\state.json` |
| Autostart | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Club-AutotalPlay.lnk` |

---

## Linux

### La app (AppImage + bandeja)

```bash
chmod +x Club-AutotalPlay-x86_64.AppImage
./Club-AutotalPlay-x86_64.AppImage        # daemon + icono en el dock/bandeja
./Club-AutotalPlay-x86_64.AppImage install   # servicio systemd user (autoarranque)
```

### Servicio

```bash
systemctl --user status club-autotalplay
journalctl --user -u club-autotalplay -f     # logs en vivo
```

### CLI

```bash
./Club-AutotalPlay-x86_64.AppImage status        # lee el state del daemon
./Club-AutotalPlay-x86_64.AppImage status --live # fuerza sondeo real
./Club-AutotalPlay-x86_64.AppImage once          # un ciclo
./Club-AutotalPlay-x86_64.AppImage debug         # diagnostico crudo
```

### Archivos (Linux)

| Qué | Dónde |
|---|---|
| AppImage | `~/.local/bin/Club-AutotalPlay.AppImage` |
| Servicio | `~/.config/systemd/user/club-autotalplay.service` |
| Estado | `~/.local/state/club-autotalplay/state.json` |
| Log | `~/.local/state/club-autotalplay/watchdog.log` |

---

## Cómo funciona el portal

```
GET  http://connectivitycheck.gstatic.com/generate_204
  → 302 Location: https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp
                ?wlanuserip=<IP>&wlanparameter=<MAC>&accname=<ACC>

POST https://clubwifi.totalplay.com.mx/ClubMovil/video-completado
     {"ip":…, "mac":…, "nombreAcc":…, "tipo":"DESKTOP"}
  → 204 No Content   (sesión otorgada)
```

1. Cada ~20 s sondea el probe de arriba (cada 1 s cerca del minuto 15, cuando
   el portal corta la sesión).
2. Si responde `302` (hubo corte), parsea IP / MAC / `accname` del `Location`.
3. Manda el `POST` → `204` = sesión otorgada.
4. Si no basta, re-botea el WiFi (`nmcli` en Linux, `netsh` en Windows).

> **Aviso:** el `204` prueba que el servidor acepta la solicitud; no prueba que
> automatizarlo de forma indefinida esté permitido por los Términos de
> Totalplay. Verificalo con el proveedor antes de depender de la app o
> recomendarla. Uso bajo tu responsabilidad.

---

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

## Trampas

- **`install` en Windows apunta al `.exe` de bandeja que esté junto al de
  consola.** Si mueves o borras la carpeta de los `.exe`, el acceso directo de
  inicio se rompe. Vuelve a correr `install` desde la nueva ubicación.
- **`install` (Linux) verifica antes de migrar.** Arranca el servicio nuevo,
  espera a que quede `active` y solo entonces deshabilita el watchdog viejo.
  Si el nuevo no levanta, revierte todo al estado previo (nada queda a medias).
  Desde un AppImage, el servicio apunta al AppImage mismo (no al montaje
  temporal), así que sigue funcionando tras reiniciar.
- **`uninstall` (Linux) solo restaura el watchdog viejo si existía.** En una
  máquina limpia nunca hubo `clubwifi-watchdog`; ahí simplemente remueve el
  servicio y listo.
- **No corras el daemon a la vez desde la consola (`run`) y desde la bandeja.**
  No pasa nada si lo haces: solo **un** daemon vive por máquina (lock de vida
  en `daemon.lock` que el kernel libera al morir el proceso — funciona igual
  tras un reboot), y todos los ciclos (del daemon y de "Reconectar ahora")
  pasan por un lock de archivo, así que dos recuperaciones nunca compiten.
- **`debug` sale redactado por defecto** (IP/MAC/accname/cookies y valores de
  proxy ocultos). Usa `debug --full` solo para tu propio análisis y no
  compartas esa salida sin revisarla. `status` también redacta los datos del
  portal.
- **En otra red (oficina/casa) el daemon no toca nada.** Si el WiFi está
  conectado a una red que no es `Club_Totalplay_WiFi`, o no puede verificar a
  qué red está conectado, ni grantea el portal ajeno ni re-botea el radio.
  Vuelve a nuestra red y sigue trabajando.
- **Windows marca "Acción necesaria" en el WiFi.** Eso lo hace el sistema
  operativo, no el portal. Mientras el daemon corra, la sesión se renueva sola
  aunque esa etiqueta aparezca un momento.
- **La sesión dura ~15 min** y se renueva sola cada vez que expira.
