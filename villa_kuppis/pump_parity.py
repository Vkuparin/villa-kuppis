"""Read-only parity check of the home pumps (card H1.3).

    python -m villa_kuppis.pump_parity --config CONFIG compare [--bind]

Reads every pump once through the direct MELCloud Home client and once
through the home Home Assistant's MELCloud Home climate entities, both in
the shape the pump service stores, and prints the differences. Nothing is
sent to either side. Units are matched by their MELCloud unit id in the
entity's unique id, else by name. The Home Assistant side is shown as the
service will see it, with the per-mode ranges and step the pump store
keeps from the direct client. ``--bind`` then stores each matched
entity in ``sites.db`` under its MELCloud unit id, which is what
``"pumps": "home_assistant"`` needs; a running service in ``melcloud`` mode
ignores the bindings.

Run it inside the app, where the configuration has both the MELCloud
credential file and the Supervisor token:
``python -m villa_kuppis.pump_parity --config /data/villa-kuppis.json compare``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from .credential_file import read_credentials_file, read_token_file
from .ha_gateway import Binding, BindingStore, HAConnection, reading
from .ha_pumps import unit_from_state
from .pump_service import unit_state
from .runtime import Config

PLATFORM = "melcloud_home"
FIELDS = ("power", "mode", "setpoint", "room_temp", "fan", "in_error")
# Room temperatures are polled at different moments.
ROOM_TOLERANCE = 0.5
TIMEOUT = 30.0


def ha_pumps(states: list[dict], registry: list[dict], now_iso: str) -> list[dict]:
    """The MELCloud Home climate entities as pump service state rows."""
    raw = {s["entity_id"]: s for s in states}
    out = []
    for entry in registry:
        entity_id = entry.get("entity_id", "")
        if entry.get("platform") != PLATFORM or not entity_id.startswith("climate."):
            continue
        if entry.get("disabled_by") or entity_id not in raw:
            continue
        binding = Binding("home", entity_id, entity_id, "climate")
        state = reading(binding, raw[entity_id])
        state["status"] = "ok" if state["available"] else "unavailable"
        row = unit_state(unit_from_state(state), now_iso)
        row.update(entity_id=entity_id, unique_id=str(entry.get("unique_id") or ""),
                   ha_name=state["label"], ha_caps=state["caps"], reading=state)
        out.append(row)
    return out


def match(direct: list[dict], ha: list[dict]) -> tuple[list[tuple], list, list]:
    """(direct, ha) pairs, then the unmatched of each side."""
    pairs, left = [], list(ha)
    for unit in direct:
        found = next((h for h in left if unit["id"].lower() in h["unique_id"].lower()), None)
        if found is None:
            named = [h for h in left if h["ha_name"].strip().lower() == unit["name"].strip().lower()]
            found = named[0] if len(named) == 1 else None
        if found is not None:
            left.remove(found)
        pairs.append((unit, found))
    return ([p for p in pairs if p[1] is not None],
            [u for u, h in pairs if h is None], left)


def differences(direct: dict, ha: dict) -> list[str]:
    out = []
    for name in FIELDS:
        a, b = direct.get(name), ha.get(name)
        if name == "room_temp" and a is not None and b is not None:
            if abs(a - b) <= ROOM_TOLERANCE:
                continue
        if name == "mode" and ha.get("power") is False and b is None:
            # HA hides the mode of an off unit; ha_pumps remembers it.
            continue
        if a != b:
            out.append(f"{name}: MELCloud {a!r}, HA {b!r}")
    dc, hc = direct.get("caps") or {}, ha.get("caps") or {}
    for name in ("min_cool", "max_cool", "half_step", "fan_speeds", "auto_fan", "modes"):
        a, b = dc.get(name), hc.get(name)
        if name == "modes" and isinstance(a, dict) and isinstance(b, dict):
            # MELCloud leaves some modes unreported (None).
            a = {m: v for m, v in a.items() if v is not None}
            b = {m: v for m, v in b.items() if m in a}
        if a != b:
            out.append(f"caps.{name}: MELCloud {a!r}, HA {b!r}")
    return out


def report(direct: list[dict], ha: list[dict]) -> tuple[list[str], list[tuple]]:
    lines = []
    pairs, only_direct, only_ha = match(direct, ha)
    for d, h in pairs:
        # As the service will see it after the switch: the pump store then
        # holds what the direct client reports now.
        seen = unit_state(unit_from_state(h["reading"], None, d.get("caps")), d["updated_at"])
        diff = differences(d, seen)
        lines.append(f"{d['name']} -> {h['entity_id']}: " + ("same" if not diff else "DIFFERENT"))
        lines += [f"    {x}" for x in diff]
        caps = h["ha_caps"]
        lines.append(f"    HA modes {caps.get('modes')}, fan modes {caps.get('fan_modes')}, "
                     f"range {caps.get('min')}-{caps.get('max')} step {caps.get('step')}, "
                     f"power {caps.get('power')}")
    lines += [f"{u['name']}: no Home Assistant entity" for u in only_direct]
    lines += [f"{h['entity_id']}: no MELCloud unit" for h in only_ha]
    return lines, pairs


async def read_direct(config: Config) -> list[dict]:
    from .pump_probe import _default_client_factory
    if config.credential_file is None:
        raise ValueError("the configuration has no MELCloud credential file")
    credentials = read_credentials_file(config.credential_file)
    client, close = _default_client_factory(credentials.username, credentials.password)
    try:
        context = await asyncio.wait_for(client.get_context(), TIMEOUT)
    finally:
        try:
            await client.__aexit__(None, None, None)
        except Exception:
            pass
        await close()
    now = datetime.now(timezone.utc).isoformat()
    return [unit_state(u, now) for b in context.buildings for u in b.air_to_air_units]


async def read_ha(config: Config) -> list[dict]:
    site = dict(config.sites).get("home")
    if site is None:
        raise ValueError("the configuration has no home site")
    conn = await HAConnection.open(site.url, read_token_file(site.token_file))
    try:
        states = await conn.get_states(TIMEOUT)
        registry = await conn.request({"type": "config/entity_registry/list"}, TIMEOUT)
    finally:
        await conn.close()
    return ha_pumps(states, registry, datetime.now(timezone.utc).isoformat())


def main(argv=None, *, read_direct=read_direct, read_ha=read_ha, out=print) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="service configuration file")
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--bind", action="store_true",
                         help="store the matched entities in sites.db")
    args = parser.parse_args(argv)
    config = Config.load(args.config)

    async def both():
        return await asyncio.gather(read_direct(config), read_ha(config))
    try:
        direct, ha = asyncio.run(both())
    except Exception as error:  # report, never a traceback with secrets
        out(f"Read failed: {type(error).__name__}: {error}")
        return 1
    lines, pairs = report(direct, ha)
    for line in lines:
        out(line)
    if args.bind:
        store = BindingStore(config.data_dir / "sites.db")
        try:
            for d, h in pairs:
                store.bind("home", d["id"], h["entity_id"], "climate", label=d["name"])
        finally:
            store.close()
        out(f"Bound {len(pairs)} of {len(direct)} pumps in sites.db")
    return 0 if pairs and len(pairs) == len(direct) == len(ha) else 2


if __name__ == "__main__":
    sys.exit(main())
