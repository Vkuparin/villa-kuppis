# Home server (Debian + Docker Compose)

Villa Kuppis and Home Assistant run as two separate Compose services. Each can
be rebuilt, restarted or stopped without touching the other. Both use the host
network: Villa Kuppis listens on `127.0.0.1:8377` only, and Home Assistant
listens on `:8123` for Villa Kuppis to call at `http://127.0.0.1:8123`. No
ports are published. Tailscale runs on the host, not in a container.

## Install

1. Install Debian, Docker Engine with the Compose plugin (from Docker's apt
   repository) and Tailscale. Add your user to the `docker` group.
2. Laptop-as-server settings (lid, suspend, battery, power loss) are in
   [docs/architecture.md](../../docs/architecture.md#deployment).
3. Clone this repository, for example to `~/villa-kuppis`.

## Folders

Defaults are below. To use other paths, copy the names into `deploy/home/.env`
(`KUPPIS_CONFIG_DIR`, `KUPPIS_DATA_DIR`, `KUPPIS_BACKUP_DIR`, `KUPPIS_SECRET_FILE`,
`HA_CONFIG_DIR`).

| Path | Purpose |
| --- | --- |
| `/srv/villa-kuppis/config/villa-kuppis.json` | Service config (read-only in the container) |
| `/srv/villa-kuppis/data` | SQLite databases and service log |
| `/mnt/backup/villa-kuppis` | Daily backups. **Mount a different disk here**, not the laptop SSD |
| `/srv/villa-kuppis/secrets/melcloud.json` | MELCloud login |
| `/srv/homeassistant/config` | Home Assistant config |

The container runs as UID 1000. The data and backup folders must be writable by
that UID:

```
sudo mkdir -p /srv/villa-kuppis/{config,data,secrets} /srv/homeassistant/config
sudo chown 1000:1000 /srv/villa-kuppis/data /mnt/backup/villa-kuppis
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

## Start and publish

```
docker compose -f deploy/home/compose.yaml up -d
docker compose -f deploy/home/compose.yaml ps       # villa-kuppis shows "healthy"
sudo tailscale serve --bg --https=443 http://127.0.0.1:8377
tailscale serve status
```

Only Villa Kuppis is published. Do not serve port 8123.

## Update

```
scripts/update-home-server.sh
```

It pulls `main`, rebuilds the image, restarts only `villa-kuppis` and waits for
the health check. To update Home Assistant, change the pinned tag in
`compose.yaml` and run `docker compose -f deploy/home/compose.yaml up -d homeassistant`.

## Check and restore the database

The service writes one backup folder per day, `<backup folder>/YYYY-MM-DD/`.
Check the newest one (no service stop needed):

```
docker compose -f deploy/home/compose.yaml run --rm --no-deps --entrypoint python \
  -v /mnt/backup/villa-kuppis:/backups:ro villa-kuppis \
  -m villa_kuppis.backup verify /backups/2026-10-20
```

Every file should print `ok`. To restore after data loss or corruption:

```
docker compose -f deploy/home/compose.yaml stop villa-kuppis
sudo mv /srv/villa-kuppis/data /srv/villa-kuppis/data.old    # keep the old files
sudo mkdir /srv/villa-kuppis/data && sudo chown 1000:1000 /srv/villa-kuppis/data
docker compose -f deploy/home/compose.yaml run --rm --no-deps --entrypoint python \
  -v /mnt/backup/villa-kuppis:/backups:ro villa-kuppis \
  -m villa_kuppis.backup restore /backups/2026-10-20 /data
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
  KUPPIS_BACKUP_DIR=/tmp/kuppis-demo/backups
mkdir -p $KUPPIS_CONFIG_DIR $KUPPIS_DATA_DIR $KUPPIS_BACKUP_DIR
echo '{"data_dir":"/data","backup_dir":"/backups"}' > $KUPPIS_CONFIG_DIR/villa-kuppis.json
sudo chown 1000:1000 $KUPPIS_DATA_DIR $KUPPIS_BACKUP_DIR
docker compose -f deploy/home/compose.yaml -f deploy/home/compose.demo.yaml up -d --build
docker compose -f deploy/home/compose.yaml -f deploy/home/compose.demo.yaml down
```

## Still to check on the real machine

Cold boot (both services come back on their own), `tailscale serve` from a
phone, and Villa Kuppis reaching Home Assistant at `127.0.0.1:8123`.
