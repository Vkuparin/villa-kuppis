"""Unattended localhost pump app with account-scoped credentials and backups."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading

from .pump_app import DEFAULT_PLAN_INTERVAL, PumpAppServer, make_demo_service
from .pump_service import make_live_service
from .service import SpotPriceService
from .weather import parse_coord_spec
from .windows_credentials import DEFAULT_TARGET, read_credentials


@dataclass(frozen=True)
class Config:
    data_dir: Path
    backup_dir: Path
    public_origin: str | None = None
    port: int = 8377
    credential_target: str = DEFAULT_TARGET
    # Optional {"home": "lat,lon", "cottage": "lat,lon"} for FMI weather.
    weather: tuple = ()
    # Apply the price-threshold cooling plan to the pumps (off: only shown).
    plan: bool = False

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict) or set(raw) - {
                "data_dir", "backup_dir", "public_origin", "port", "credential_target",
                "weather", "plan"}:
            raise ValueError("Invalid service configuration fields")
        weather = raw.get("weather", {})
        if not isinstance(weather, dict) or set(weather) - {"home", "cottage"}:
            raise ValueError("Invalid weather configuration")
        try:
            sites = tuple((site, parse_coord_spec(spec))
                          for site, spec in sorted(weather.items()))
        except (AttributeError, ValueError):
            raise ValueError("Invalid weather coordinates") from None
        if type(raw.get("plan", False)) is not bool:
            raise ValueError("Invalid plan setting")
        config = cls(Path(raw["data_dir"]), Path(raw["backup_dir"]),
                     raw.get("public_origin"), raw.get("port", 8377),
                     raw.get("credential_target", DEFAULT_TARGET), sites,
                     raw.get("plan", False))
        if not config.data_dir.is_absolute() or not config.backup_dir.is_absolute():
            raise ValueError("Service data and backup paths must be absolute")
        if config.data_dir.resolve() == config.backup_dir.resolve():
            raise ValueError("Data and backup folders must be different")
        if type(config.port) is not int or not 1 <= config.port <= 65535:
            raise ValueError("Invalid service port")
        return config


def price_loop(data_dir, stop, *, service_factory=SpotPriceService, poll_seconds=300):
    """Fetch tomorrow's spot prices once a day (after 14:25 Helsinki) into prices.db."""
    service = service_factory(data_dir)
    try:
        while not stop.is_set():
            try:
                outcome = service.schedule_once()
                if outcome.status in ("ok", "error"):
                    logging.info("Spot price fetch: %s", outcome.status)
            except Exception as error:
                logging.error("Spot price fetch failed: %s", type(error).__name__)
            stop.wait(poll_seconds)
    finally:
        service.close()


def run(config: Config, *, demo=False, stop_event=None, service_factory=None,
        server_factory=PumpAppServer, backup_factory=None, price_factory=price_loop):
    """Own all runtime resources; injected factories keep tests offline."""
    if backup_factory is None:
        from .backup import BackupLoop
        backup_factory = BackupLoop

    stop = stop_event or threading.Event()
    if service_factory is None:
        if demo:
            service_factory = make_demo_service
        else:
            credentials = read_credentials(config.credential_target)
            service_factory = lambda data: make_live_service(
                data, credentials=credentials, weather_sites=dict(config.weather),
                automation_interval=DEFAULT_PLAN_INTERVAL,
                automation_default_on=config.plan)
    service = service_factory(config.data_dir)
    server = backups = None
    try:
        server = server_factory(service, host="127.0.0.1", port=config.port,
                                demo=demo, public_origin=config.public_origin)
        server.timeout = 0.5
        service.start()
        backups = backup_factory(config.data_dir, config.backup_dir)
        backups.start()
        if not demo:
            threading.Thread(target=price_factory, args=(config.data_dir, stop),
                             name="spot-prices", daemon=True).start()
        logging.info("Service started")
        while not stop.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            if server is not None:
                server.server_close()
        finally:
            try:
                if backups is not None:
                    backups.close()
            finally:
                service.close()
                logging.info("Service stopped")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--demo", action="store_true",
                        help="offline devices; use a temporary data/backup config")
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(config.data_dir / "service.log",
                                      maxBytes=262144, backupCount=3, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler],
                            format="%(asctime)s %(levelname)s %(message)s", force=True)
        run(config, demo=args.demo)
    except Exception as error:
        # Third-party exception bodies can contain URLs and identifiers.
        logging.error("Service stopped after %s", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
