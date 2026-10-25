# Home server (Debian host + Docker Compose)

Since 25 October 2026 the chosen home install is the Home Assistant app in
`home-haos` ([../home-assistant-app.md](../home-assistant-app.md)). This page is
the fallback: the same service in Docker Compose on the Debian host.

The home server `home-server` runs Debian 13. Villa Kuppis is a Compose service
on the host; the home Home Assistant is a separate Home Assistant OS virtual
machine with its own LAN address `HOME_HA_IP` ([deploy/haos-vm.md](../haos-vm.md)).
Each can be rebuilt, restarted or stopped without touching the other. Villa
Kuppis uses the host network and listens on `127.0.0.1:8377` only; no ports are
published. Tailscale runs on the host, not in a container.

## Install

1. Install Debian 13, Docker Engine with the Compose plugin (from Docker's apt
   repository) and Tailscale. Run Docker with `sudo` (the `docker` group is
   root-equivalent).
2. Laptop-as-server settings (lid, suspend, battery, power loss) are in
   [docs/architecture.md](../../docs/architecture.md#deployment).
3. Create the Home Assistant VM: [deploy/haos-vm.md](../haos-vm.md).
4. Clone this repository, for example to `~/villa-kuppis`.

## Folders

Defaults are below. To use other paths, copy the names into `deploy/home/.env`
(`KUPPIS_CONFIG_DIR`, `KUPPIS_DATA_DIR`, `KUPPIS_ARCHIVE_DIR`, `KUPPIS_SECRET_FILE`,
`KUPPIS_COTTAGE_TOKEN_FILE`, `KUPPIS_HOME_TOKEN_FILE`).

| Path | Purpose |
| --- | --- |
| `/srv/villa-kuppis/config/villa-kuppis.json` | Service config (read-only in the container) |
| `/srv/villa-kuppis/data` | SQLite databases and service log |
| `/srv/archive/villa-kuppis` | Daily backups, on the 5 TB WD My Passport USB drive (seen as `/archive/villa-kuppis` in the container) |
| `/srv/villa-kuppis/secrets/melcloud.json` | MELCloud login |
| `/srv/villa-kuppis/secrets/cottage-token` | Cottage Home Assistant access token |
| `/srv/villa-kuppis/secrets/home-ha-token` | Home Home Assistant access token |

Backup drive: the WD drive is ext4 with the label `HOME_BACKUP`, mounted by UUID
at `/srv/archive` with `nofail,x-systemd.device-timeout=30s` (owner's server
guide). Check it with `findmnt /srv/archive`. Compose mounts the whole
`/srv/archive` so a drive that mounts late still reaches the container. Leave
`/srv/archive` itself owned by root: with the drive missing, the container
cannot create `villa-kuppis` on the internal SSD, and the backup fails and logs
it instead.

The container runs as UID 1000. The data and backup folders must be writable by
that UID:

```
findmnt /srv/archive    # must show the HOME_BACKUP drive before you go on
sudo mkdir -p /srv/villa-kuppis/{config,data,secrets} /srv/archive/villa-kuppis
sudo chown 1000:1000 /srv/villa-kuppis/data /srv/archive/villa-kuppis
cp deploy/home/villa-kuppis.example.json /srv/villa-kuppis/config/villa-kuppis.json
```

Edit the config: keep `data_dir`, `backup_dir` and `credential_file` as in the
example (they are paths inside the container) and set `public_origin` to your
Tailscale name.

## Secret

Docker mounts the secret file with its host permissions, so the file must be
owned by UID 1000 and mode 0600. Villa Kuppis refuses a file that others can
read.

```
sudo install -m 600 -o 1000 -g 1000 /dev/null /srv/villa-kuppis/secrets/melcloud.json
sudoedit /srv/villa-kuppis/secrets/melcloud.json
```

Contents: `{"username": "you@example.com", "password": "..."}`. Do not put it in
Git. Windows development is unchanged: leave `credential_file` out and it uses
Credential Manager.

## Cottage connection

Villa Kuppis reads the cottage rooms from the cottage Home Assistant and sends
the room targets to it over Tailscale (cards H2.3, H2.4). Compose mounts two
more secrets: `cottage_token` at `/run/secrets/cottage_token` and
`home_ha_token` at `/run/secrets/home_ha_token`, plain-text files with the
long-lived access tokens of each Home Assistant's `villa-kuppis` user. Same
rules as above: owned by UID 1000, mode 0600, never in Git. Create both files,
empty if need be, before the first `docker compose up`:

```
for f in cottage-token home-ha-token; do
  sudo install -m 600 -o 1000 -g 1000 /dev/null /srv/villa-kuppis/secrets/$f
done
```

The cottage Home Assistant is a VM behind `cottage-server` and is not on the
tailnet itself. `cottage-server` routes just its address to the tailnet (a
Tailscale subnet route for one address), and `home-server` accepts the route:

```
# on cottage-server
echo 'net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
sudo tailscale set --advertise-routes=<COTTAGE_HA_IP>/32
# approve the route: Tailscale admin console > Machines > cottage-server > Edit route settings
# on home-server
sudo tailscale set --accept-routes
curl -sS -o /dev/null -w '%{http_code}\n' http://<COTTAGE_HA_IP>:8123/   # 200
```

Pick a cottage VM address that is not also used on the home LAN. Windows and
phones with Tailscale accept the route by default, so the cottage Home
Assistant UI also opens at `http://<COTTAGE_HA_IP>:8123` from anywhere on the
tailnet; no SSH tunnel needed.

Add a `sites` field to the config (JSON has no comments, so it is not in the
example file):

```
"sites": {
  "cottage": {
    "url": "ws://<COTTAGE_HA_IP>:8123/api/websocket",
    "token_file": "/run/secrets/cottage_token"
  }
}
```

The site ids are `cottage` and `home`. `url` must be `ws://` or `wss://`;
`token_file` must be an absolute path. The token is read again on every
reconnect, so replacing the file rotates it without a restart.

Bind the cottage devices once with the `sites` tool. It stores the bindings in
`sites.db` in the data directory; a running service picks changes up within
seconds. The rooms are `large` (Iso huone) and `small` (Pieni huone):

```
run="docker compose -f deploy/home/compose.yaml run --rm --no-deps --entrypoint python villa-kuppis -m villa_kuppis.sites --config /config/villa-kuppis.json"
$run discover                       # entities in the cottage Home Assistant
$run bind cottage large-temperature sensor.ENTITY sensor --room large --label ALPSTUGA
$run bind cottage large-heater-1 climate.ENTITY climate --room large --label "Mill 1050 W"
$run bind cottage large-status sensor.villa_kuppis_large_target sensor --room large
$run list
$run unbind cottage large-heater-1
```

Each room has an ALPSTUGA temperature sensor (device id `<room>-temperature`;
optional `-humidity`, `-co2`, `-pm25`), one to three Mill heaters (kind
`climate`) and the room status sensor `sensor.villa_kuppis_<room>_target`.
The Mökki section of the web app and `/api/cottage` show each reading as ok,
stale, unavailable or missing; history and mode changes are recorded in
`pumps.db`.

## Pumps through Home Assistant (H1.5)

Off by default: the pumps use the direct MELCloud Home client. After the parity test (H1.3), add the home Home Assistant VM to `sites` and switch:

```json
"sites": {
  "cottage": {"url": "ws://<COTTAGE_HA_IP>:8123/api/websocket", "token_file": "/run/secrets/cottage_token"},
  "home": {"url": "ws://<HOME_HA_IP>:8123/api/websocket", "token_file": "/run/secrets/home_ha_token"}
},
"pumps": "home_assistant"
```

Bind each pump's climate entity with its existing MELCloud unit id as the device id, so history, settings and plans carry over (the unit ids are in the app's `/api/view`; `discover --site home` lists the entities):

```bash
docker compose -f deploy/home/compose.yaml run --rm --no-deps --entrypoint python villa-kuppis \
  -m villa_kuppis.sites --config /config/villa-kuppis.json bind home UNIT_ID climate.olohuone climate
```

The MELCloud credential is not read in this mode. Rollback: set `"pumps": "melcloud"` and restart `villa-kuppis`. Only one automation writer at a time: turn off any Home Assistant automations for the pumps.

## Start and publish

```
docker compose -f deploy/home/compose.yaml up -d
docker compose -f deploy/home/compose.yaml ps       # villa-kuppis shows "healthy"
sudo tailscale serve --bg --https=443 http://127.0.0.1:8377
tailscale serve status
```

Only Villa Kuppis is published. Do not serve or forward port 8123.

## Update

```
scripts/update-home-server.sh
```

It pulls `main`, rebuilds the image, restarts only `villa-kuppis` and waits for
the health check. Home Assistant updates itself from its own UI (Settings >
System > Updates) after a backup; it does not touch Villa Kuppis.

## Check and restore the database

The service writes one backup folder per day, `<backup folder>/YYYY-MM-DD/`.
Check the newest one (no service stop needed):

```
docker compose -f deploy/home/compose.yaml run --rm --no-deps --entrypoint python \
  villa-kuppis -m villa_kuppis.backup verify /archive/villa-kuppis/2026-10-20
```

Every file should print `ok`. To restore after data loss or corruption:

```
docker compose -f deploy/home/compose.yaml stop villa-kuppis
sudo mv /srv/villa-kuppis/data /srv/villa-kuppis/data.old    # keep the old files
sudo mkdir /srv/villa-kuppis/data && sudo chown 1000:1000 /srv/villa-kuppis/data
docker compose -f deploy/home/compose.yaml run --rm --no-deps --entrypoint python \
  villa-kuppis -m villa_kuppis.backup restore /archive/villa-kuppis/2026-10-20 /data
docker compose -f deploy/home/compose.yaml up -d villa-kuppis
```

`restore` checks the backup first, refuses to overwrite existing database files
unless you add `--force`, and checks the copied files afterwards. Home Assistant
keeps running throughout.

## Try it offline

`compose.demo.yaml` runs Villa Kuppis with demo devices: no credentials, no
network, no Home Assistant. Use throw-away folders:

```
export KUPPIS_CONFIG_DIR=/tmp/kuppis-demo/config KUPPIS_DATA_DIR=/tmp/kuppis-demo/data \
  KUPPIS_ARCHIVE_DIR=/tmp/kuppis-demo/archive
mkdir -p $KUPPIS_CONFIG_DIR $KUPPIS_DATA_DIR $KUPPIS_ARCHIVE_DIR/villa-kuppis
echo '{"data_dir":"/data","backup_dir":"/archive/villa-kuppis"}' > $KUPPIS_CONFIG_DIR/villa-kuppis.json
sudo chown 1000:1000 $KUPPIS_DATA_DIR $KUPPIS_ARCHIVE_DIR/villa-kuppis
docker compose -f deploy/home/compose.yaml -f deploy/home/compose.demo.yaml up -d --build
docker compose -f deploy/home/compose.yaml -f deploy/home/compose.demo.yaml down
```

If Docker says the folder "is not a shared or slave mount", the host's root mount
is private (systemd makes it shared): `sudo mount --make-rshared /`.

## Still to check on the real machine

Cold boot (the drive mounts, the `haos` VM and `villa-kuppis` come back on their
own), `tailscale serve` from a phone, Villa Kuppis reaching `HOME_HA_IP:8123`
and, through the subnet route, `COTTAGE_HA_IP:8123`, and a backup with the
drive unplugged failing without writing to the SSD.
