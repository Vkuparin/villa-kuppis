"""Bind Home Assistant entities to Villa Kuppis devices (card H2.3).

    python -m villa_kuppis.sites --config CONFIG discover
    python -m villa_kuppis.sites --config CONFIG list
    python -m villa_kuppis.sites --config CONFIG bind cottage large-temperature \
        sensor.alpstuga_temperature sensor --room large --label ALPSTUGA
    python -m villa_kuppis.sites --config CONFIG unbind cottage large-temperature

Bindings live in ``sites.db`` in the data directory; a running service picks
up changes within seconds. ``discover`` connects once with the configured
token file and lists the entities that could be bound. Nothing is written to
Home Assistant. Rooms at the cottage: ``large`` and ``small``. Name sensor
device ids ``<room>-temperature``, ``-humidity``, ``-co2`` or ``-pm25``; the
room status sensor is ``sensor.villa_kuppis_<room>_target``.
"""

from __future__ import annotations

import argparse
import time

from .cottage import ROOMS, SITES_DB, status_entity
from .credential_file import read_token_file
from .ha_gateway import BindingError, BindingStore, HomeAssistantGateway, KINDS
from .runtime import Config
from .windows_credentials import CredentialError

DISCOVER_TIMEOUT = 20.0


def _site(config: Config, site_id: str):
    sites = dict(config.sites)
    if site_id not in sites:
        raise ValueError(f"site {site_id!r} is not configured")
    return sites[site_id]


def discover(config: Config, site_id: str, *, timeout: float = DISCOVER_TIMEOUT,
             gateway_factory=HomeAssistantGateway) -> list[dict]:
    """Connect once and return the bindable entities."""
    site = _site(config, site_id)
    token_file = site.token_file
    store = BindingStore(config.data_dir / SITES_DB)
    gateway = gateway_factory(site_id, site.url, lambda: read_token_file(token_file), store)
    try:
        gateway.start()
        deadline = time.monotonic() + timeout
        while True:
            health = gateway.health()
            if health["state"] == "ok":
                return gateway.discover()
            if health["state"] == "auth_failed":
                raise ConnectionError("Home Assistant refused the access token")
            if time.monotonic() > deadline:
                raise ConnectionError(
                    "no connection to Home Assistant" +
                    (f" ({health['error']})" if health.get("error") else ""))
            time.sleep(0.1)
    finally:
        gateway.stop()
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="service configuration file")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("discover", "list"):
        sub = commands.add_parser(name)
        sub.add_argument("--site", default="cottage")
    bind = commands.add_parser("bind")
    bind.add_argument("site")
    bind.add_argument("device_id")
    bind.add_argument("entity_id")
    bind.add_argument("kind", choices=KINDS)
    bind.add_argument("--room", choices=sorted(ROOMS))
    bind.add_argument("--label", default="")
    unbind = commands.add_parser("unbind")
    unbind.add_argument("site")
    unbind.add_argument("device_id")
    return parser


def main(argv=None, print_fn=print, *, gateway_factory=HomeAssistantGateway) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        if args.command == "discover":
            for row in discover(config, args.site, gateway_factory=gateway_factory):
                print_fn(f"{row['entity_id']}\t{row['kind']}\t{row['name']}\t"
                         f"{row['device_id'] or '-'}")
            return 0
        store = BindingStore(config.data_dir / SITES_DB)
        try:
            return _local_command(store, args, print_fn)
        finally:
            store.close()
    except (BindingError, ValueError, ConnectionError, CredentialError, OSError) as error:
        # CredentialError text never holds the token; others hold no secrets either.
        print_fn(f"error: {error}")
        return 1


def _local_command(store: BindingStore, args, print_fn) -> int:
    if args.command == "list":
        for b in store.bindings(args.site):
            print_fn(f"{b.device_id}\t{b.entity_id}\t{b.kind}\t{b.room or '-'}\t{b.label}")
        return 0
    if args.command == "bind":
        if args.site not in ("cottage", "home"):
            raise ValueError(f"unknown site {args.site!r}")
        if args.site == "home" and args.kind != "climate":
            raise ValueError("home bindings are pumps: kind climate, device id = the MELCloud unit id")
        expected = [r for r in ROOMS if args.entity_id == status_entity(r)]
        if expected and args.room != expected[0]:
            raise ValueError(f"{args.entity_id} belongs to room {expected[0]}; add --room {expected[0]}")
        b = store.bind(args.site, args.device_id, args.entity_id, args.kind,
                       args.label, args.room)
        print_fn(f"bound {b.device_id} -> {b.entity_id}" +
                 ("" if b.room else " (no --room: it is not shown in any room)"))
        return 0
    if store.unbind(args.site, args.device_id):
        print_fn(f"unbound {args.device_id}")
        return 0
    print_fn(f"{args.device_id} was not bound")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
