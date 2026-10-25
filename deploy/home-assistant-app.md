# Villa Kuppis as a Home Assistant app (home)

Chosen on 25 October 2026: Villa Kuppis runs as an app (formerly add-on) in
the home Home Assistant VM `home-haos`. It keeps its own web app at
`https://home-server.corvus-stairs.ts.net` (no Home Assistant login; Tailscale
identifies the user as before) and also appears in the Home Assistant sidebar.
Villa Kuppis stays the one UI and planner for both sites; Home Assistant is the
device gateway. The Docker Compose install in [home/README.md](home/README.md)
remains a fallback.

| | |
| --- | --- |
| App files | [ha-app/](../ha-app/) (`config.yaml`, `Dockerfile`), repository [repository.yaml](../repository.yaml) |
| Entry point | `python -m villa_kuppis.ha_app`: options to config, one-time import, then the normal runtime |
| Data | `/data/db` (SQLite, service.log), `/data/backup` (daily copies, left out of HA backups), `/data/secrets` (0600) |
| Home Home Assistant | Through the Supervisor (`ws://supervisor/core/websocket`), no token to create |
| Web app | Port 8377 on the VM's host network. Accepted: the Supervisor ingress proxy (sidebar, HA user as the actor), the `tailscale_proxy` address with Tailscale identity headers, and loopback. Everything else gets 403. |
| Backups | Home Assistant backs the app up cold (stopped for a few seconds), so the databases are consistent |
| Updates | `scripts/release-ha-app.sh X.Y.Z` points `ha-app/build.yaml` at the current commit and bumps the version; Home Assistant offers the update and builds that commit |

## Install

1. Reserve the LAN addresses in the router: home-server 192.168.86.36 and
   home-haos 192.168.86.37. The proxy setting and Tailscale Serve use them.
2. In Home Assistant: Settings > Apps > App store > ⋮ > Repositories, add
   `https://github.com/Vkuparin/villa-kuppis`. Install **Villa Kuppis** (it
   builds on the VM, a few minutes).
3. Configuration tab:

   | Option | Value |
   | --- | --- |
   | `pumps` | `melcloud` (until the H1.3 parity test) |
   | `melcloud_username`, `melcloud_password` | the MELCloud Home login |
   | `weather_home` | as on the old install, `lat,lon` |
   | `public_origin` | `https://home-server.corvus-stairs.ts.net` |
   | `tailscale_proxy` | `192.168.86.36` |
   | `automation_default_on` | off (the header switch decides after the first start) |

   Leave the cottage options empty until the cottage is commissioned (H2.1).
   Do not start the app yet when moving an existing install.

## Move the existing install from akvaario

Only one Villa Kuppis may run at a time: both would control the pumps.

1. On akvaario (PowerShell): `Stop-ScheduledTask VillaKuppis; Disable-ScheduledTask VillaKuppis`
   and `tailscale serve --https=443 off`.
2. Copy `pumps.db` and `prices.db` (and `sites.db` if present) from
   `%LOCALAPPDATA%\VillaKuppis\data` into the app's import folder:
   `/addon_configs/<id>_villa_kuppis/import/` as seen from the Terminal & SSH
   app (`ls -d /addon_configs/*villa_kuppis`). Leave the originals on akvaario.
3. Start the app. Its log shows `Imported pumps.db, prices.db` and
   `Service started`; the import runs only while the app has no databases and
   renames the copies to `*.imported`.
4. On home-server: check that `tailscale serve status` has nothing on 443 (Cockpit was
   moved to `--https=9090` on 2026-10-25), then
   `sudo tailscale serve --bg --https=443 http://192.168.86.37:8377`.
5. Check from the phone: `https://home-server.corvus-stairs.ts.net` opens
   without a Home Assistant login, the three pumps show live readings, the
   history is there, and the log shows today's and tomorrow's prices fetched.
   The Villa Kuppis sidebar entry in Home Assistant shows the same app.

Rollback: stop the app, then on akvaario `Enable-ScheduledTask VillaKuppis;
Start-ScheduledTask VillaKuppis` and restore its Serve
(`%LOCALAPPDATA%\VillaKuppis\serve-status.bak-2026-10-25.json`). akvaario's
data is untouched, so only history recorded in the app meanwhile is missing.
Decommission akvaario's copy after two quiet weeks.

## Pumps through Home Assistant (phase 2)

1. The owner adds the MELCloud Home integration in home-haos with his own
   sign-in (Settings > Devices & services). No Home Assistant automations on
   the pumps: one writer at a time.
2. Read-only comparison (card H1.3), from the HAOS root shell while the app
   still runs with `pumps: melcloud`:

   ```sh
   docker exec addon_b51a7d83_villa_kuppis \
     python -m villa_kuppis.pump_parity --config /data/villa-kuppis.json compare
   ```

   It reads all pumps through both routes and prints each unit as `same` or
   its differences (room temperatures within 0.5 °C count as the same; an
   off unit's hidden mode is expected). Nothing is sent to the pumps. Exit
   code 0 means every unit matched an entity.
3. When the comparison is clean, run it again with `--bind`: it stores each
   matched entity under its MELCloud unit id in `sites.db`, so history,
   settings and plans carry over.
4. With the owner's go-ahead: set `pumps` to `home_assistant`, restart the
   app, and change one setpoint and one fan speed per unit by one step from
   the web app, then restore them. Check each event is confirmed and matches
   the MELCloud Home app, and that the other units stay unchanged.

Rollback: set `pumps` back to `melcloud` (the credentials stay in the
options) and restart the app.

## Cottage later

`cottage-haos` has its own Tailscale name, so `cottage_url` is
`wss://cottage-haos.corvus-stairs.ts.net/api/websocket` with the token of its
`villa-kuppis` user; no subnet route is needed. Check first that the app can
reach the tailnet from home-haos: the Tailscale app's userspace networking
(on by default) hides the tailnet from other apps.
