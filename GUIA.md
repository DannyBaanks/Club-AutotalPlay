# GUIA — Club-AutotalPlay

App de escritorio que mantiene viva la sesión de la red pública
`Club_Totalplay_WiFi` (Totalplay). Detecta el captive portal y otorga la
sesión sola (mismo POST que hace el navegador al terminar el video), sin
browser ni video. Corre como daemon en segundo plano con icono en el dock.

> Reemplaza al watchdog anterior (`~/.local/bin/clubwifi-watchdog.py`).
> El anterior **se conserva como respaldo**: `club-autotalplay uninstall`
> lo restaura.

## Cómo funciona

1. Cada ~20 s sondea `http://connectivitycheck.gstatic.com/generate_204`.
2. Si responde `302` con `Location: .../ClubMovil/inicio.jsp?...`, hay captive
   portal → hace `POST https://clubwifi.totalplay.com.mx/ClubMovil/video-completado`
   con `{ip, mac, nombreAcc, tipo:"DESKTOP"}` → `204` = sesión otorgada.
3. Si el WiFi se cayó, lo re-botea con `nmcli connection up`.

## Uso diario

- **Icono en el dock:** aparece como "Club-AutotalPlay" en el app grid de
  GNOME. Ábrelo = corre el daemon + icono de tray (verde=online,
  ámbar=reconectando, rojo=sin conexión). Menú del tray: *Reconectar ahora*,
  *Ver estado*, *Salir*.
- **Estado (CLI):**
  ```bash
  ~/.local/bin/Club-AutotalPlay.AppImage status        # lee el state del daemon
  ~/.local/bin/Club-AutotalPlay.AppImage status --live # fuerza sondeo real
  ```
- **Servicio (corre solo al iniciar sesión, sin abrir la app):**
  ```bash
  systemctl --user status club-autotalplay
  journalctl --user -u club-autotalplay -f             # logs en vivo
  ```

## Instalar / desinstalar

El servicio ya quedó instalado en el corte M4. Para mover el control:

```bash
# volver al core nuevo (servicio club-autotalplay)
~/.venv/bin/club-autotalplay install     # o: Club-AutotalPlay.AppImage install

# restaurar el watchdog viejo (respaldo)
~/.local/bin/Club-AutotalPlay.AppImage uninstall
```

`install` desactiva el viejo (no lo borra) y activa el nuevo.
`uninstall` hace lo contrario. Ambos son reversibles.

## Archivos

| Qué | Dónde |
|---|---|
| AppImage | `~/.local/bin/Club-AutotalPlay.AppImage` |
| Servicio | `~/.config/systemd/user/club-autotalplay.service` |
| Estado | `~/.local/state/club-autotalplay/state.json` |
| Log | `~/.local/state/club-autotalplay/watchdog.log` |
| Repo | `~/Development/ISyCo Git/Club-AutotalPlay` |

## Notas

- La sesión del portal dura ~15 min; el daemon la renueva sola cada vez que
  expira. Un blip puntual (`IncompleteRead` en el POST) se re-intenta solo.
- El `.exe` (Windows) no está en esta versión: el core depende de
  `nmcli`/NetworkManager (Linux).
