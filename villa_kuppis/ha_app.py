"""Home Assistant app (add-on) entry point: app options -> service config.

The Supervisor starts the container with the user's options in
``/data/options.json`` and a Supervisor token in ``SUPERVISOR_TOKEN``.
This module turns them into the ordinary service config and runs the
same runtime as the Debian and Windows installs:

* ``/data/db``       SQLite databases and service.log
* ``/data/backup``   daily consistent copies (Home Assistant backs up /data)
* ``/data/secrets``  0600 files: MELCloud login, Home Assistant tokens
* ``/config/import`` drop pumps.db/prices.db/sites.db here once to move an
  existing install in (``/addon_configs/<slug>/import`` in the Samba and
  SSH apps); only used while the database folder has none of them.

The home site always uses the Supervisor's proxy to this Home Assistant,
so no long-lived token is needed at home.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

from . import runtime

SUPERVISOR_WS = "ws://supervisor/core/websocket"
PORT = 8377
IMPORTABLE = ("pumps.db", "prices.db", "sites.db")


class OptionsError(Exception):
    """The app options are incomplete; the message is safe to log."""


def _write_secret(path: Path, text: str) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)
    return path


def _text(options: dict, key: str) -> str:
    value = options.get(key)
    return value.strip() if isinstance(value, str) else ""


def import_databases(import_dir: Path, db_dir: Path) -> list[str]:
    """Copy databases from an earlier install, once; returns the names copied.

    Skipped when the database folder already has any of them, so a
    leftover import folder never overwrites live data. Each file must pass
    SQLite's integrity check; the originals are renamed to *.imported.
    """
    found = [name for name in IMPORTABLE if (import_dir / name).is_file()]
    if not found:
        return []
    existing = [name for name in IMPORTABLE if (db_dir / name).exists()]
    if existing:
        logging.warning("Import skipped: the app already has %s", ", ".join(existing))
        return []
    for name in found:
        source = sqlite3.connect(f"file:{import_dir / name}?mode=ro", uri=True)
        try:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise OptionsError(f"Import refused: {name} fails the integrity check")
        finally:
            source.close()
    db_dir.mkdir(parents=True, exist_ok=True)
    for name in found:
        source = sqlite3.connect(f"file:{import_dir / name}?mode=ro", uri=True)
        target = sqlite3.connect(db_dir / name)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        (import_dir / name).rename(import_dir / f"{name}.imported")
    logging.info("Imported %s", ", ".join(found))
    return found


def build_config(options: dict, data_root: Path, supervisor_token: str | None) -> dict:
    """The service config (runtime.Config.load format) for these options."""
    secrets = data_root / "secrets"
    config = {"data_dir": str(data_root / "db"),
              "backup_dir": str(data_root / "backup"),
              "port": PORT, "ingress": True,
              "plan": options.get("automation_default_on", False) is True}

    pumps = _text(options, "pumps") or "melcloud"
    config["pumps"] = pumps
    if pumps == "melcloud":
        username, password = _text(options, "melcloud_username"), options.get("melcloud_password")
        if not username or not isinstance(password, str) or not password:
            raise OptionsError("Set melcloud_username and melcloud_password in the app "
                               "options, or switch pumps to home_assistant")
        config["credential_file"] = str(_write_secret(
            secrets / "melcloud.json",
            json.dumps({"username": username, "password": password})))

    sites = {}
    if supervisor_token:
        sites["home"] = {"url": SUPERVISOR_WS,
                         "token_file": str(_write_secret(secrets / "home-token",
                                                         supervisor_token.strip()))}
    cottage_url, cottage_token = _text(options, "cottage_url"), _text(options, "cottage_token")
    if cottage_url or cottage_token:
        if not (cottage_url and cottage_token):
            raise OptionsError("Set both cottage_url and cottage_token, or neither")
        sites["cottage"] = {"url": cottage_url,
                            "token_file": str(_write_secret(secrets / "cottage-token",
                                                            cottage_token))}
    if sites:
        config["sites"] = sites

    weather = {site: _text(options, f"weather_{site}") for site in ("home", "cottage")}
    weather = {site: spec for site, spec in weather.items() if spec}
    if weather:
        config["weather"] = weather

    origin, proxy = _text(options, "public_origin"), _text(options, "tailscale_proxy")
    if origin:
        config["public_origin"] = origin
    if proxy:
        if not origin:
            raise OptionsError("tailscale_proxy needs public_origin")
        config["trusted_proxies"] = [proxy]
    return config


def main(argv=None, *, data_root=Path("/data"), import_dir=Path("/config/import"),
         run=runtime.main) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        options_file = data_root / "options.json"
        options = (json.loads(options_file.read_text(encoding="utf-8"))
                   if options_file.exists() else {})
        config = build_config(options, data_root, os.environ.get("SUPERVISOR_TOKEN"))
        runtime.Config.load_dict(config)  # fail here, with a readable message
        import_databases(import_dir, Path(config["data_dir"]))
    except (OptionsError, ValueError, sqlite3.Error) as error:
        logging.error("%s", error)
        return 1
    config_file = data_root / "villa-kuppis.json"
    config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.chmod(config_file, 0o600)
    return run(["--config", str(config_file), "--log-stdout"] + list(argv or []))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
