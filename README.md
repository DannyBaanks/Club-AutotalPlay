# Club-AutotalPlay

Auto-reconnect para la red pública **Club_Totalplay_WiFi** (portal cautivo de Totalplay).

La red corta el internet cuando expira la sesión (~15 min, en la práctica ~5). El
portal redirige todo el tráfico HTTP y otorga una sesión nueva con un `POST`:

```
POST https://clubwifi.totalplay.com.mx/ClubMovil/video-completado
{"ip":…, "mac":…, "nombreAcc":…, "tipo":"DESKTOP"}   →  204 No Content
```

Ese `POST` es exactamente lo que hace tu navegador al terminar el video de
publicidad. **Este proyecto lo hace automáticamente**, sin browser ni video:

1. Cada 20 s chequea `http://connectivitycheck.gstatic.com/generate_204`.
2. Si devuelve `302` (corte), parsea IP/MAC/ACC del `Location` del portal.
3. Hace el `POST` que otorga la sesión y verifica que vuelva el `204`.
4. Si no basta, re-botea el WiFi por `nmcli` como respaldo.

## Estado

En desarrollo. Migración del watchdog funcional (`M0`): core + CLI mínima.

- `M0` ✅ core + CLI (`--once` / `--status`)
- `M1` CLI unificada + daemon con estado compartido
- `M2` icono en tray + GUI mínima
- `M3` AppImage + `.desktop` (dock)
- `M4` instalador / migración + docs

## Requisitos

- Linux con NetworkManager (`nmcli`)
- Python ≥ 3.9 (solo stdlib para el core)

## Uso (M0)

```bash
club-autotalplay --status    # estado actual
club-autotalplay --once      # un solo ciclo de reconexión
```

## Licencia

MIT
