# GUIA — Club-AutotalPlay

App que mantiene viva la sesión de la red pública `Club_Totalplay_WiFi`
(Totalplay). Detecta el captive portal y otorga la sesión sola (el mismo
`POST` que hace el navegador al terminar el video), sin browser ni video.
Corre como daemon en segundo plano con icono en la bandeja.

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

1. Cada ~20 s sondea el probe de arriba.
2. Si responde `302` (hubo corte), parsea IP / MAC / `accname` del `Location`.
3. Manda el `POST` → `204` = sesión otorgada.
4. Si no basta, re-botea el WiFi (`nmcli` en Linux, `netsh` en Windows).

> **Aviso:** el `204` prueba que el servidor acepta la solicitud; no prueba que
> automatizarlo de forma indefinida esté permitido por los Términos de
> Totalplay. Verificalo con el proveedor antes de depender de la app o
> recomendarla. Uso bajo tu responsabilidad.

---

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
- **`debug` sale redactado por defecto** (IP/MAC/accname/cookies ocultos).
  Usa `debug --full` solo para tu propio análisis y no compartas esa salida
  sin revisarla.
- **En otra red (oficina/casa) el daemon no toca nada.** Si el WiFi está
  conectado a una red que no es `Club_Totalplay_WiFi`, ni grantea el portal
  ajeno ni re-botea el radio. Vuelve a nuestra red y sigue trabajando.
- **Windows marca "Acción necesaria" en el WiFi.** Eso lo hace el sistema
  operativo, no el portal. Mientras el daemon corra, la sesión se renueva sola
  aunque esa etiqueta aparezca un momento.
- **La sesión dura ~15 min** y se renueva sola cada vez que expira.
