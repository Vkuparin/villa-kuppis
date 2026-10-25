"""Unattended pump app: config, service, web server, price loop and backups."""
from __future__ import annotations

import argparse
import ipaddress
from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading

from .cottage import parse_sites
from .pump_app import DEFAULT_PLAN_INTERVAL, PumpAppServer, make_demo_service
from .pump_service import make_live_service
from .service import SpotPriceService
from .credential_file import read_credentials_file
from .weather import parse_coord_spec


@dataclass(frozen=True)
class Config:
    data_dir: Path
    backup_dir: Path
    public_origin: str | None = None
    port: int = 8377
    # Optional {"home": "lat,lon", "cottage": "lat,lon"} for FMI weather.
    weather: tuple = ()
    # Apply the price-threshold cooling plan to the pumps (off: only shown).
    plan: bool = False
    # Absolute path of the JSON MELCloud credential file (pumps "melcloud").
    credential_file: Path | None = None
    # ((site_id, cottage.SiteConfig), ...): Home Assistant gateways (card H2.3).
    sites: tuple = ()
    # Pump client (card H1.5): "melcloud" (direct) or "home_assistant"
    # (through sites.home); switching back is the rollback.
    pumps: str = "melcloud"
    # Home Assistant app (card H1.4): serve the ingress proxy too, and accept
    # Tailscale Serve from these proxy addresses; both listen on all
    # interfaces inside the app container.
    ingress: bool = False
    trusted_proxies: tuple = ()

    @classmethod
    def load(cls, path):
        return cls.load_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    @classmethod
    def load_dict(cls, raw):
        if not isinstance(raw, dict) or set(raw) - {
                "data_dir", "backup_dir", "public_origin", "port", "weather",
                "plan", "credential_file", "sites", "pumps",
                "ingress", "trusted_proxies"}:
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
        if type(raw.get("ingress", False)) is not bool:
            raise ValueError("Invalid ingress setting")
        proxies = raw.get("trusted_proxies", [])
        try:
            if not isinstance(proxies, list):
                raise ValueError
            proxies = tuple(str(ipaddress.ip_address(p)) for p in proxies)
        except ValueError:
            raise ValueError("trusted_proxies must be a list of IP addresses") from None
        if proxies and not raw.get("public_origin"):
            raise ValueError("trusted_proxies need public_origin")
        credential_file = raw.get("credential_file")
        if credential_file is not None and (
                not isinstance(credential_file, str) or not Path(credential_file).is_absolute()):
            raise ValueError("Credential file path must be absolute")
        config = cls(Path(raw["data_dir"]), Path(raw["backup_dir"]),
                     raw.get("public_origin"), raw.get("port", 8377), sites,
                     raw.get("plan", False),
                     Path(credential_file) if credential_file else None,
                     parse_sites(raw.get("sites", {})),
                     raw.get("pumps", "melcloud"),
                    raw.get("ingress", False), proxies)
        if config.pumps not in ("melcloud", "home_assistant"):
            raise ValueError("pumps must be melcloud or home_assistant")
        if config.pumps == "home_assistant" and "home" not in dict(config.sites):
            raise ValueError("pumps through Home Assistant need sites.home")
        if not config.data_dir.is_absolute() or not config.backup_dir.is_absolute():
            raise ValueError("Service data and backup paths must be absolute")
        if config.data_dir.resolve() == config.backup_dir.resolve():
            raise ValueError("Data and backup folders must be different")
        if type(config.port) is not int or not 1 <= config.port <= 65535:
            raise ValueError("Invalid service port")
        return config


def price_loop(data_dir, stop, *, service_factory=SpotPriceService, poll_seconds=300):
    """Fetch missing spot prices (today; tomorrow after 14:25 Helsinki) into prices.db."""
    service = service_factory(data_dir)
    try:
        while not stop.is_set():
            try:
                outcome = service.schedule_once()
                if outcome.status == "ok":
                    logging.info("Spot price fetch: ok (%s)", outcome.detail)
                elif outcome.status == "error":
                    logging.warning("Spot price fetch for %s: error: %s",
                                    outcome.target_day, outcome.detail)
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
            extra = {"sites": dict(config.sites)} if config.sites else {}
            if config.pumps == "home_assistant":
                credentials = None
                extra["pump_site"] = dict(config.sites)["home"]
            elif config.credential_file is None:
                raise ValueError("pumps through MELCloud need credential_file")
            else:
                credentials = read_credentials_file(config.credential_file)
            service_factory = lambda data: make_live_service(
                data, credentials=credentials, weather_sites=dict(config.weather),
                automation_interval=DEFAULT_PLAN_INTERVAL,
                automation_default_on=config.plan, **extra)
    service = service_factory(config.data_dir)
    server = backups = None
    try:
        extra_server = {}
        if config.ingress or config.trusted_proxies:
            extra_server = {"ingress": config.ingress,
                            "trusted_proxies": config.trusted_proxies}
        host = "0.0.0.0" if extra_server else "127.0.0.1"
        server = server_factory(service, host=host, port=config.port,
                                demo=demo, public_origin=config.public_origin,
                                **extra_server)
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
    parser.add_argument("--log-stdout", action="store_true",
                        help="also log to standard output (Home Assistant app log)")
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(config.data_dir / "service.log",
                                      maxBytes=262144, backupCount=3, encoding="utf-8")
        handlers = [handler]
        if args.log_stdout:
            handlers.append(logging.StreamHandler(sys.stdout))
        logging.basicConfig(level=logging.INFO, handlers=handlers,
                            format="%(asctime)s %(levelname)s %(message)s", force=True)
        run(config, demo=args.demo)
    except Exception as error:
        # Third-party exception bodies can contain URLs and identifiers.
        logging.error("Service stopped after %s", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
