<div align="center">

# Club-AutotalPlay

**Auto-reconnect para la red pública `Club_Totalplay_WiFi` (Totalplay).**

Icono en la bandeja · reconexión sola · sin browser ni video.

[![Linux](https://img.shields.io/badge/platform-Linux-blue?logo=linux&logoColor=white)](#)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#)

</div>

---

## ¿Qué hace?

La red de Totalplay otorga sesiones de ~15 minutos. Cuando expira, **corta el
internet** y redirige todo el tráfico a un portal cautivo que te hace ver un
video de publicidad. Al terminar, tu navegador manda un `POST` que otorga la
sesión nueva — y vuelves a tener internet.

**Este proyecto hace ese `POST` automáticamente**, sin browser ni video:

1. Cada ~20 s sondea `http://connectivitycheck.gstatic.com/generate_204`.
2. Si responde `302` (hubo corte), parsea IP / MAC / `accname` del `Location` del portal.
3. Manda el `POST` a `/ClubMovil/video-completado` → `204` = sesión otorgada.
4. Si no basta, re-botea el WiFi por `nmcli` como respaldo.

Todo en segundo plano, con un **icono en la bandeja** que cambia de color:

| Color | Estado |
|---|---|
| 🟢 Verde | en línea |
| 🟠 Ámbar | reconectando |
| 🔴 Rojo | sin conexión |

> Probado en Ubuntu 24.04 (GNOME, X11). Se desconecta, el icono se pone rojo,
> renueva la sesión sola y vuelve a verde.

## Descargar y probar (Linux)

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

## Requisitos

- Linux con **NetworkManager** (`nmcli`) — el core depende de él.
- Python ≥ 3.9 (el core es solo stdlib; la app de escritorio usa `pystray`+`Pillow`).
- GNOME/X11 para el icono de bandeja (en Wayland puede requerir la extensión AppIndicator).

## Cómo funciona el portal (detalles)

```
GET  http://connectivitycheck.gstatic.com/generate_204
  → 302 Location: https://clubwifi.totalplay.com.mx/ClubMovil/inicio.jsp
                ?wlanuserip=<IP>&wlanparameter=<MAC>&accname=<ACC>

POST https://clubwifi.totalplay.com.mx/ClubMovil/video-completado
     {"ip":…, "mac":…, "nombreAcc":…, "tipo":"DESKTOP"}
  → 204 No Content   (sesión otorgada)
```

## Licencia

MIT
