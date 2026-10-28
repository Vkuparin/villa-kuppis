"""Home Assistant devices without app updates.

Each site's Home Assistant decides which devices exist and which room
(area) they are in. Villa Kuppis reads its registries (areas, floors,
devices, entities) through the site's ``HomeAssistantGateway`` and shows
every supported entity placed in an area as a tile on that site's page. A
new sensor, lamp or plug therefore appears without a release; a release is
only needed for a new kind of tile.

Rules:

- An entity is shown when it, or its device, is in an area; Home Assistant's
  hidden, disabled, ``config`` and ``diagnostic`` entities are left out, as
  are entities Villa Kuppis already runs itself (bound pumps and cottage
  devices, the cottage integration's own sensors).
- Each entity is keyed by its entity registry id, which survives a rename
  of the ``entity_id``, so order, names and history stay with the device.
- Order, hidden tiles and areas, and Finnish display names live in
  ``pumps.db``; nothing is written back to Home Assistant.
- Commands go through the gateway's ``submit_entity`` and only use services
  the entity reports as supported, so they show pending, confirmed, failed
  or unknown like the pump commands, and land in the event log.
- Numeric sensors and on/off states are recorded once a minute when they
  change (at least every 15 minutes); rows older than 30 days are thinned
  to 15-minute means and kept.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# Next to the pump and cottage history, so the daily backup covers it.
DB_FILENAME = "pumps.db"
SITE_NAMES = {"home": "Koti", "cottage": "Mökki"}
DOMAINS = ("sensor", "binary_sensor", "light", "switch", "input_boolean", "climate",
           "cover", "fan", "scene", "script")
SKIP_PLATFORMS = ("villa_kuppis_cottage",)
SKIP_CATEGORIES = ("config", "diagnostic")
UNAVAILABLE = ("unavailable", "unknown")
SAMPLE_INTERVAL = 60.0
SAMPLE_REFRESH = 900          # record an unchanged value at least this often (s)
THIN_AFTER = 30 * 86400       # older rows become 15-minute means
THIN_BUCKET = 900
SPARK_SECONDS = 24 * 3600
SPARK_POINTS = 48
SPARK_CACHE = 60.0
MAX_HISTORY_HOURS = 24 * 400
MAX_NAME = 60
RECENT_COMMAND = 600          # a failed command stays on its tile this long (s)

# Tile order inside an area until the owner sorts it.
KIND_ORDER = ("climate", "temperature", "humidity", "co2", "air", "light", "plug", "switch",
              "fan", "cover", "power", "energy", "binary", "illuminance", "battery",
              "number", "text", "scene", "script")
SENSOR_KINDS = {"temperature": "temperature", "humidity": "humidity",
                "carbon_dioxide": "co2", "pm25": "air", "pm10": "air", "pm1": "air",
                "volatile_organic_compounds": "air", "aqi": "air", "power": "power",
                "energy": "energy", "battery": "battery", "illuminance": "illuminance"}
ON_OFF_KINDS = ("light", "plug", "switch", "fan", "binary")

# Home Assistant entity feature bits.
LIGHT_DIM_MODES = ("brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white")
COVER_OPEN, COVER_CLOSE, COVER_SET_POSITION, COVER_STOP = 1, 2, 4, 8
FAN_SET_SPEED, FAN_TURN_OFF, FAN_TURN_ON = 1, 16, 32
CLIMATE_TARGET_TEMPERATURE, CLIMATE_TURN_OFF, CLIMATE_TURN_ON = 1, 128, 256


class DeviceError(ValueError):
    """A request that cannot be sent; the message is Finnish."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _features(attrs: dict) -> int:
    value = attrs.get("supported_features")
    return value if isinstance(value, int) else 0


def tile_kind(domain: str, state: dict) -> str:
    attrs = state.get("attributes") or {}
    device_class = attrs.get("device_class")
    if domain == "sensor":
        if device_class in SENSOR_KINDS:
            return SENSOR_KINDS[device_class]
        numeric = (_number(state.get("state")) is not None
                   or attrs.get("unit_of_measurement") or attrs.get("state_class"))
        return "number" if numeric else "text"
    if domain == "binary_sensor":
        return "binary"
    if domain in ("switch", "input_boolean"):
        return "plug" if device_class == "outlet" else "switch"
    return domain


def _name(state: dict, entry: dict | None, device: dict | None) -> str:
    attrs = state.get("attributes") or {}
    name = attrs.get("friendly_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    if entry and (entry.get("name") or entry.get("original_name")):
        return entry.get("name") or entry.get("original_name")
    if device and (device.get("name_by_user") or device.get("name")):
        return device.get("name_by_user") or device.get("name")
    return state.get("entity_id", "")


def describe(key: str, area: str, state: dict, entry: dict | None = None,
             device: dict | None = None) -> dict:
    """One shown entity: what a tile needs, from its Home Assistant state."""
    entity_id = state["entity_id"]
    domain = entity_id.partition(".")[0]
    attrs = state.get("attributes") or {}
    raw = state.get("state")
    available = raw is not None and raw not in UNAVAILABLE
    kind = tile_kind(domain, state)
    out = {
        "key": key, "entity_id": entity_id, "domain": domain, "kind": kind,
        "device_class": attrs.get("device_class"), "area": area,
        "ha_name": _name(state, entry, device), "available": available,
        "state": raw, "unit": attrs.get("unit_of_measurement"),
        "changed": state.get("last_changed"), "value": None, "on": None, "caps": {},
        "device": ({"name": device.get("name_by_user") or device.get("name"),
                    "manufacturer": device.get("manufacturer"),
                    "model": device.get("model")} if device else None),
    }
    if kind in SENSOR_KINDS.values() or kind == "number":
        out["value"] = _number(raw) if available else None
    elif kind == "text":
        out["value"] = raw if available else None
    if domain in ("light", "switch", "input_boolean", "fan", "binary_sensor"):
        out["on"] = (raw == "on") if available else None
    if domain == "light":
        modes = [m for m in attrs.get("supported_color_modes") or [] if isinstance(m, str)]
        dim = any(m in LIGHT_DIM_MODES for m in modes)
        ct = "color_temp" in modes
        brightness = _number(attrs.get("brightness"))
        out["brightness"] = (round(brightness * 100 / 255) if brightness is not None
                             and out["on"] else None)
        out["color_temp"] = _number(attrs.get("color_temp_kelvin"))
        out["caps"] = {"power": True, "dim": dim, "ct": ct,
                       "ct_min": _number(attrs.get("min_color_temp_kelvin")) or 2000,
                       "ct_max": _number(attrs.get("max_color_temp_kelvin")) or 6500}
    elif domain in ("switch", "input_boolean"):
        out["caps"] = {"power": True}
    elif domain == "fan":
        features = _features(attrs)
        out["percentage"] = _number(attrs.get("percentage"))
        out["caps"] = {"power": True, "speed": bool(features & FAN_SET_SPEED),
                       "step": _number(attrs.get("percentage_step")) or 1}
    elif domain == "cover":
        features = _features(attrs)
        out["position"] = _number(attrs.get("current_position"))
        out["caps"] = {"open": bool(features & COVER_OPEN), "close": bool(features & COVER_CLOSE),
                       "stop": bool(features & COVER_STOP),
                       "position": bool(features & COVER_SET_POSITION)}
    elif domain == "climate":
        features = _features(attrs)
        out["on"] = (raw != "off") if available else None
        out["setpoint"] = _number(attrs.get("temperature"))
        out["current"] = _number(attrs.get("current_temperature"))
        out["caps"] = {"setpoint": bool(features & CLIMATE_TARGET_TEMPERATURE),
                       "power": bool(features & CLIMATE_TURN_OFF) and bool(features & CLIMATE_TURN_ON),
                       "min": _number(attrs.get("min_temp")),
                       "max": _number(attrs.get("max_temp")),
                       "step": _number(attrs.get("target_temp_step")) or 0.5}
    elif domain in ("scene", "script"):
        out["caps"] = {"run": True}
        out["last_triggered"] = attrs.get("last_triggered") if domain == "script" else (
            raw if available else None)
    return out


def build(registry: dict | None, states: dict[str, dict], skip_entities=()) -> dict:
    """Areas with their shown entities, and the site's HA automations.

    Pure: ``registry`` is the gateway's registry lists, ``states`` the latest
    state of every entity, ``skip_entities`` the entity ids Villa Kuppis
    already runs itself.
    """
    if registry is None:
        return {"registry": False, "areas": [], "automations": []}
    floors = {f.get("floor_id"): f for f in registry.get("floors", [])}
    areas = {}
    for a in registry.get("areas", []):
        floor = floors.get(a.get("floor_id")) or {}
        areas[a.get("area_id")] = {
            "id": a.get("area_id"), "name": a.get("name") or a.get("area_id"),
            "floor": floor.get("name"), "level": floor.get("level"), "devices": []}
    devices = {d.get("id"): d for d in registry.get("devices", [])}
    entries = {e.get("entity_id"): e for e in registry.get("entities", [])}
    skip = set(skip_entities)
    automations = []
    for entity_id, state in states.items():
        entry = entries.get(entity_id)
        domain = entity_id.partition(".")[0]
        if entry is None or entry.get("disabled_by") or entry.get("hidden_by"):
            continue
        if domain == "automation":
            attrs = state.get("attributes") or {}
            raw = state.get("state")
            automations.append({
                "key": entry.get("id") or entity_id, "entity_id": entity_id,
                "name": _name(state, entry, None), "available": raw not in UNAVAILABLE,
                "on": raw == "on", "last_triggered": attrs.get("last_triggered")})
            continue
        if (domain not in DOMAINS or entity_id in skip
                or entry.get("entity_category") in SKIP_CATEGORIES
                or entry.get("platform") in SKIP_PLATFORMS):
            continue
        device = devices.get(entry.get("device_id"))
        if device is not None and device.get("disabled_by"):
            continue
        area_id = entry.get("area_id") or (device or {}).get("area_id")
        if area_id not in areas:
            continue
        areas[area_id]["devices"].append(
            describe(entry.get("id") or entity_id, area_id, state, entry, device))
    automations.sort(key=lambda a: a["name"].lower())
    return {"registry": True, "areas": list(areas.values()), "automations": automations}


# --- commands ------------------------------------------------------------------------

def _state_is(on: bool):
    return lambda raw: (raw.get("state") == "on") == on


def action_call(device: dict, action: str, value):
    """(domain, service, data, confirm, text) for one tile action.

    ``confirm`` checks the entity's Home Assistant state (None: confirmed
    when Home Assistant accepts the call); ``text`` is the event log line.
    Raises DeviceError with a Finnish message.
    """
    domain, caps = device["domain"], device.get("caps") or {}
    if not device.get("available"):
        raise DeviceError("Laite ei ole saatavilla Home Assistantissa.")
    if action == "power":
        if not isinstance(value, bool):
            raise DeviceError("Arvon täytyy olla päällä tai pois.")
        if domain == "climate":
            if not caps.get("power"):
                raise DeviceError("Laitetta ei voi kytkeä päälle tai pois.")
            return ("climate", "turn_on" if value else "turn_off", {},
                    lambda raw: (raw.get("state") != "off") == value,
                    "Päälle" if value else "Pois")
        if domain == "automation" or caps.get("power"):
            return (domain, "turn_on" if value else "turn_off", {}, _state_is(value),
                    "Päälle" if value else "Pois")
        raise DeviceError("Laitetta ei voi kytkeä päälle tai pois.")
    if action == "brightness":
        number = _number(value)
        if domain != "light" or not caps.get("dim"):
            raise DeviceError("Valoa ei voi himmentää.")
        if number is None or not 1 <= number <= 100:
            raise DeviceError("Kirkkauden täytyy olla 1–100 %.")
        pct = round(number)

        def bright(raw):
            b = _number((raw.get("attributes") or {}).get("brightness"))
            return raw.get("state") == "on" and b is not None and abs(b * 100 / 255 - pct) <= 1.5
        return "light", "turn_on", {"brightness_pct": pct}, bright, f"Kirkkaus {pct} %"
    if action == "color_temp":
        number = _number(value)
        if domain != "light" or not caps.get("ct"):
            raise DeviceError("Valon värilämpötilaa ei voi säätää.")
        lo, hi = caps.get("ct_min") or 2000, caps.get("ct_max") or 6500
        if number is None or not lo <= number <= hi:
            raise DeviceError(f"Värilämpötilan täytyy olla {lo:.0f}–{hi:.0f} K.")
        kelvin = round(number)

        def ct(raw):
            k = _number((raw.get("attributes") or {}).get("color_temp_kelvin"))
            return k is not None and abs(k - kelvin) <= 60
        return "light", "turn_on", {"color_temp_kelvin": kelvin}, ct, f"Värilämpötila {kelvin} K"
    if action == "position":
        number = _number(value)
        if domain != "cover" or not caps.get("position"):
            raise DeviceError("Asentoa ei voi säätää.")
        if number is None or not 0 <= number <= 100:
            raise DeviceError("Asennon täytyy olla 0–100 %.")
        pos = round(number)
        return ("cover", "set_cover_position", {"position": pos},
                lambda raw: _number((raw.get("attributes") or {}).get("current_position")) == pos,
                f"Asento {pos} %")
    if action == "cover":
        services = {"open": ("open_cover", "Avaa"), "close": ("close_cover", "Sulje"),
                    "stop": ("stop_cover", "Pysäytä")}
        if domain != "cover" or value not in services or not caps.get(value):
            raise DeviceError("Toiminto ei ole tuettu.")
        service, text = services[value]
        return "cover", service, {}, None, text
    if action == "speed":
        number = _number(value)
        if domain != "fan" or not caps.get("speed"):
            raise DeviceError("Nopeutta ei voi säätää.")
        if number is None or not 0 <= number <= 100:
            raise DeviceError("Nopeuden täytyy olla 0–100 %.")
        return "fan", "set_percentage", {"percentage": round(number)}, None, f"Nopeus {round(number)} %"
    if action == "setpoint":
        number = _number(value)
        if domain != "climate" or not caps.get("setpoint"):
            raise DeviceError("Laitteella ei ole tavoitelämpötilaa.")
        lo, hi, step = caps.get("min"), caps.get("max"), caps.get("step") or 0.5
        if number is None or lo is None or hi is None or not lo <= number <= hi:
            raise DeviceError("Tavoitelämpötila on laitteen sallitun alueen ulkopuolella.")
        number = round(round((number - lo) / step) * step + lo, 2)
        return ("climate", "set_temperature", {"temperature": number},
                lambda raw: _number((raw.get("attributes") or {}).get("temperature")) == number,
                f"Tavoite {number:g} °C".replace(".", ","))
    if action == "run":
        if domain not in ("scene", "script"):
            raise DeviceError("Vain tilanteen tai skriptin voi käynnistää.")
        return domain, "turn_on", {}, None, "Käynnistetty"
    raise DeviceError("Tuntematon toiminto.")


# --- storage -------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_layout (
    site_id TEXT NOT NULL,
    key TEXT NOT NULL,
    entity_id TEXT NOT NULL DEFAULT '',
    position REAL,
    hidden INTEGER NOT NULL DEFAULT 0,
    name TEXT,
    seen INTEGER NOT NULL DEFAULT 0,
    first_seen_utc TEXT NOT NULL,
    PRIMARY KEY (site_id, key)
);
CREATE TABLE IF NOT EXISTS area_layout (
    site_id TEXT NOT NULL,
    area_id TEXT NOT NULL,
    position REAL,
    hidden INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (site_id, area_id)
);
CREATE TABLE IF NOT EXISTS device_reading (
    site_id TEXT NOT NULL,
    key TEXT NOT NULL,
    ts INTEGER NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (site_id, key, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS device_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    site_id TEXT NOT NULL,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    actor TEXT NOT NULL,
    change TEXT NOT NULL,
    request_id TEXT,
    result TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


class DeviceStore:
    """Layout, readings and events of Home Assistant devices (in pumps.db)."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # layout

    def layout(self, site_id: str) -> tuple[dict, dict]:
        with self._lock:
            devices = {r[0]: {"position": r[1], "hidden": bool(r[2]), "name": r[3],
                              "seen": bool(r[4])}
                       for r in self._conn.execute(
                           "SELECT key, position, hidden, name, seen FROM device_layout "
                           "WHERE site_id = ?", (site_id,))}
            areas = {r[0]: {"position": r[1], "hidden": bool(r[2])}
                     for r in self._conn.execute(
                         "SELECT area_id, position, hidden FROM area_layout WHERE site_id = ?",
                         (site_id,))}
        return devices, areas

    def note(self, site_id: str, keys: dict[str, str], now: datetime) -> None:
        """Remember devices seen for the first time; on a site's first catalog
        every device counts as already seen, later ones are new."""
        with self._lock, self._conn:
            known = {r[0] for r in self._conn.execute(
                "SELECT key FROM device_layout WHERE site_id = ?", (site_id,))}
            fresh = [k for k in keys if k not in known]
            if not fresh:
                return
            seen = 0 if known else 1
            self._conn.executemany(
                "INSERT INTO device_layout (site_id, key, entity_id, seen, first_seen_utc) "
                "VALUES (?, ?, ?, ?, ?)",
                [(site_id, k, keys[k], seen, now.isoformat()) for k in fresh])

    def update(self, site_id: str, key: str, now: datetime, **fields) -> None:
        allowed = {"hidden", "name", "seen", "position"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO device_layout (site_id, key, first_seen_utc, seen) "
                "VALUES (?, ?, ?, 1)", (site_id, key, now.isoformat()))
            self._conn.execute(
                f"UPDATE device_layout SET {', '.join(k + ' = ?' for k in sets)} "
                "WHERE site_id = ? AND key = ?", (*sets.values(), site_id, key))

    def order(self, site_id: str, keys: list[str], now: datetime) -> None:
        for i, key in enumerate(keys):
            self.update(site_id, key, now, position=float(i))

    def update_area(self, site_id: str, area_id: str, **fields) -> None:
        sets = {k: v for k, v in fields.items() if k in ("hidden", "position")}
        if not sets:
            return
        with self._lock, self._conn:
            self._conn.execute("INSERT OR IGNORE INTO area_layout (site_id, area_id) VALUES (?, ?)",
                               (site_id, area_id))
            self._conn.execute(
                f"UPDATE area_layout SET {', '.join(k + ' = ?' for k in sets)} "
                "WHERE site_id = ? AND area_id = ?", (*sets.values(), site_id, area_id))

    def order_areas(self, site_id: str, area_ids: list[str]) -> None:
        for i, area_id in enumerate(area_ids):
            self.update_area(site_id, area_id, position=float(i))

    # readings

    def latest(self, site_id: str) -> dict[str, tuple[int, float]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, MAX(ts), value FROM device_reading WHERE site_id = ? GROUP BY key",
                (site_id,)).fetchall()
        return {k: (ts, v) for k, ts, v in rows}

    def record(self, site_id: str, rows: list[tuple[str, int, float]]) -> None:
        if not rows:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO device_reading (site_id, key, ts, value) VALUES (?, ?, ?, ?)",
                [(site_id, k, ts, v) for k, ts, v in rows])

    def series(self, site_id: str, key: str, start: int, end: int,
               max_points: int) -> list[tuple[int, float]]:
        bucket = max(60, (end - start) // max(1, max_points))
        with self._lock:
            return self._conn.execute(
                "SELECT (ts / ?) * ? AS b, AVG(value) FROM device_reading "
                "WHERE site_id = ? AND key = ? AND ts >= ? AND ts < ? GROUP BY b ORDER BY b",
                (bucket, bucket, site_id, key, start, end)).fetchall()

    def thin(self, before: int) -> int:
        """Rows older than ``before`` become one 15-minute mean each."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT site_id, key, (ts / ?) * ? AS b, AVG(value), COUNT(*) FROM device_reading "
                "WHERE ts < ? GROUP BY site_id, key, b", (THIN_BUCKET, THIN_BUCKET, before)).fetchall()
            if all(r[4] == 1 for r in rows):
                return 0
            self._conn.execute("DELETE FROM device_reading WHERE ts < ?", (before,))
            self._conn.executemany(
                "INSERT INTO device_reading (site_id, key, ts, value) VALUES (?, ?, ?, ?)",
                [r[:4] for r in rows])
            return len(rows)

    # events

    def add_event(self, now: datetime, site_id: str, key: str, name: str, actor: str,
                  change: str, request_id: str | None, result: str, detail: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO device_event (ts_utc, site_id, key, name, actor, change, request_id, "
                "result, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now.isoformat(), site_id, key, name, actor, change, request_id, result, detail))

    def pending_events(self) -> list[tuple[int, str, str]]:
        with self._lock:
            return self._conn.execute(
                "SELECT id, site_id, request_id FROM device_event "
                "WHERE result = 'pending' AND request_id IS NOT NULL").fetchall()

    def finish_event(self, event_id: int, result: str, detail: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE device_event SET result = ?, detail = ? WHERE id = ?",
                               (result, detail, event_id))

    def events(self, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts_utc, site_id, key, name, actor, change, result, detail "
                "FROM device_event ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": f"device-{r[0]}", "ts": r[1], "site": r[2], "unit": f"device:{r[3]}",
                 "unit_name": r[4], "actor": r[5], "action": "device", "requested": r[6],
                 "result": r[7], "detail": r[8]} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- one site's catalog -------------------------------------------------------------

def _sort_key(layout: dict, fallback: tuple):
    pos = layout.get("position") if layout else None
    return (0, pos, ()) if pos is not None else (1, 0, fallback)


class DeviceCatalog:
    """One site's Home Assistant devices for the web page.

    ``source`` is the site's ``HomeAssistantGateway`` (or a stand-in with
    ``registry()``, ``raw_states()``, ``bound_entities()``, ``health()``,
    ``submit_entity()``, ``command()`` and ``call()``).
    """

    def __init__(self, site_id: str, source, store: DeviceStore, *, now=utc_now):
        self.site_id = site_id
        self._source = source
        self._store = store
        self._now = now
        self._lock = threading.Lock()
        self._commands: dict[str, str] = {}      # key -> last request id
        self._sparks: tuple[float, dict] | None = None

    def _build(self) -> dict:
        states = self._source.raw_states()
        skip = self._source.bound_entities()
        return build(self._source.registry(), states, skip)

    def _find(self, key: str) -> dict:
        built = self._build()
        for area in built["areas"]:
            for device in area["devices"]:
                if device["key"] == key:
                    return device
        for automation in built["automations"]:
            if automation["key"] == key:
                return {**automation, "domain": "automation", "kind": "automation",
                        "caps": {"power": True}, "ha_name": automation["name"]}
        raise DeviceError("Laitetta ei löydy Home Assistantista tai se on piilotettu siellä.")

    def view(self, *, editing: bool = False) -> dict:
        built = self._build()
        health = self._source.health()
        now = self._now()
        if built["registry"]:
            keys = {d["key"]: d["entity_id"] for a in built["areas"] for d in a["devices"]}
            self._store.note(self.site_id, keys, now)
        dev_layout, area_layout = self._store.layout(self.site_id)
        sparks = self._spark_map(built)
        areas = []
        for area in built["areas"]:
            al = area_layout.get(area["id"], {})
            devices = []
            for d in area["devices"]:
                lay = dev_layout.get(d["key"], {})
                d["name"] = lay.get("name") or d["ha_name"]
                d["hidden"] = lay.get("hidden", False)
                d["new"] = not lay.get("seen", True)
                d["command"] = self._command_state(d["key"])
                if d["key"] in sparks:
                    d["spark"] = sparks[d["key"]]
                devices.append(d)
            devices.sort(key=lambda d: _sort_key(dev_layout.get(d["key"]), (
                KIND_ORDER.index(d["kind"]) if d["kind"] in KIND_ORDER else len(KIND_ORDER),
                d["name"].lower())))
            areas.append({**area, "devices": devices, "hidden": al.get("hidden", False)})
        areas.sort(key=lambda a: _sort_key(area_layout.get(a["id"]), (
            a["level"] if isinstance(a["level"], (int, float)) else 1e9, a["name"].lower())))
        for a in built["automations"]:
            a["command"] = self._command_state(a["key"])
        return {"site": self.site_id, "configured": True,
                "connection": health.get("state"), "registry": built["registry"],
                "registry_error": health.get("registry_error"),
                "areas": areas, "automations": built["automations"]}

    def _command_state(self, key: str) -> dict | None:
        with self._lock:
            request_id = self._commands.get(key)
        if request_id is None:
            return None
        cmd = self._source.command(request_id)
        if cmd is None:
            return None
        if cmd["state"] != "pending":
            finished = cmd.get("finished_at")
            try:
                age = (self._now() - datetime.fromisoformat(finished)).total_seconds()
            except (TypeError, ValueError):
                age = 0
            if cmd["state"] == "confirmed" or age > RECENT_COMMAND:
                return None
        return {k: cmd.get(k) for k in ("request_id", "state", "detail", "action", "value",
                                        "finished_at")}

    def _spark_map(self, built: dict) -> dict:
        now = time.monotonic()
        with self._lock:
            if self._sparks is not None and now - self._sparks[0] < SPARK_CACHE:
                return self._sparks[1]
        end = int(self._now().timestamp())
        out = {}
        for area in built["areas"]:
            for d in area["devices"]:
                if d["kind"] in SENSOR_KINDS.values() or d["kind"] == "number":
                    rows = self._store.series(self.site_id, d["key"], end - SPARK_SECONDS,
                                              end + 1, SPARK_POINTS)
                    if len(rows) > 1:
                        out[d["key"]] = [round(v, 2) for _, v in rows]
        with self._lock:
            self._sparks = (now, out)
        return out

    # -- changes --

    def layout(self, body: dict) -> None:
        now = self._now()
        if "areas" in body:
            areas = body["areas"]
            if not isinstance(areas, list) or not all(isinstance(a, str) for a in areas):
                raise DeviceError("areas: lista huoneiden tunnisteista")
            self._store.order_areas(self.site_id, areas)
        if "area" in body and "area_hidden" in body:
            if not isinstance(body["area"], str) or not isinstance(body["area_hidden"], bool):
                raise DeviceError("area_hidden: päällä tai pois")
            self._store.update_area(self.site_id, body["area"], hidden=int(body["area_hidden"]))
        if "order" in body:
            order = body["order"]
            if not isinstance(order, list) or not all(isinstance(k, str) for k in order):
                raise DeviceError("order: lista laitteiden tunnisteista")
            self._store.order(self.site_id, order, now)
        key = body.get("key")
        if key is None:
            return
        if not isinstance(key, str):
            raise DeviceError("key puuttuu")
        fields = {}
        if "hidden" in body:
            if not isinstance(body["hidden"], bool):
                raise DeviceError("hidden: päällä tai pois")
            fields["hidden"] = int(body["hidden"])
        if "seen" in body:
            fields["seen"] = 1
        if "name" in body:
            name = body["name"]
            if name is not None and not isinstance(name, str):
                raise DeviceError("Nimen täytyy olla tekstiä.")
            name = " ".join((name or "").split())[:MAX_NAME]
            fields["name"] = name or None
        self._store.update(self.site_id, key, now, **fields)

    def command(self, key: str, action: str, value, actor: str) -> dict:
        device = self._find(key)
        domain, service, data, confirm, text = action_call(device, action, value)
        result = self._source.submit_entity(device["entity_id"], domain, service, data,
                                            actor, confirm)
        with self._lock:
            self._commands[key] = result["request_id"]
        name = f"{SITE_NAMES.get(self.site_id, self.site_id)}: {device.get('name') or device['ha_name']}"
        self._store.add_event(self._now(), self.site_id, key, name, actor, text,
                              result["request_id"], result["state"], result.get("detail") or "")
        return result

    def lights_off(self, area_id: str, actor: str) -> None:
        built = self._build()
        area = next((a for a in built["areas"] if a["id"] == area_id), None)
        if area is None:
            raise DeviceError("Huonetta ei löydy.")
        try:
            self._source.call("light", "turn_off", {}, target={"area_id": area_id})
        except Exception as err:
            self._store.add_event(self._now(), self.site_id, f"area:{area_id}",
                                  f"{SITE_NAMES.get(self.site_id, self.site_id)}: {area['name']}",
                                  actor, "Valot pois", None, "failed", str(err))
            raise DeviceError("Home Assistant ei sammuttanut valoja.") from err
        self._store.add_event(self._now(), self.site_id, f"area:{area_id}",
                              f"{SITE_NAMES.get(self.site_id, self.site_id)}: {area['name']}",
                              actor, "Valot pois", None, "confirmed")

    def history(self, key: str, hours: float) -> dict:
        hours = max(1.0, min(float(hours), MAX_HISTORY_HOURS))
        end = int(self._now().timestamp()) + 1
        rows = self._store.series(self.site_id, key, end - int(hours * 3600), end, 300)
        return {"key": key, "points": [
            [datetime.fromtimestamp(ts, timezone.utc).isoformat(), round(v, 3)] for ts, v in rows]}

    def update_events(self, pending: list) -> None:
        for event_id, _site, request_id in pending:
            cmd = self._source.command(request_id)
            if cmd is not None and cmd["state"] != "pending":
                self._store.finish_event(event_id, cmd["state"], cmd.get("detail") or "")

    # -- history --

    def sample(self) -> int:
        """Record changed values; returns the number of rows written."""
        built = self._build()
        if not built["registry"]:
            return 0
        now = int(self._now().timestamp())
        latest = self._store.latest(self.site_id)
        rows = []
        for area in built["areas"]:
            for d in area["devices"]:
                if d["kind"] in ON_OFF_KINDS:
                    value = None if d["on"] is None else float(d["on"])
                elif d["kind"] in SENSOR_KINDS.values() or d["kind"] == "number":
                    value = d["value"]
                else:
                    continue
                if value is None:
                    continue
                last = latest.get(d["key"])
                if (last is None or abs(last[1] - value) > 1e-9
                        or (d["kind"] not in ON_OFF_KINDS and now - last[0] >= SAMPLE_REFRESH)):
                    rows.append((d["key"], now, value))
        self._store.record(self.site_id, rows)
        return len(rows)


class DeviceHub:
    """Every site's catalog, their store and the history sampler."""

    def __init__(self, data_dir, sources: dict, *, now=utc_now,
                 sample_interval: float = SAMPLE_INTERVAL):
        self._now = now
        self._store = DeviceStore(Path(data_dir) / DB_FILENAME)
        self.catalogs = {site: DeviceCatalog(site, src, self._store, now=now)
                         for site, src in sources.items()}
        self._sample_interval = sample_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thinned: int | None = None

    def catalog(self, site: str) -> DeviceCatalog:
        if site not in self.catalogs:
            raise DeviceError("Kohteessa ei ole Home Assistantia.")
        return self.catalogs[site]

    def view(self, site: str) -> dict:
        if site not in self.catalogs:
            return {"site": site, "configured": False, "areas": [], "automations": []}
        return self.catalogs[site].view()

    def events(self, limit: int) -> list[dict]:
        pending = self._store.pending_events()
        for catalog in self.catalogs.values():
            catalog.update_events([p for p in pending if p[1] == catalog.site_id])
        return self._store.events(limit)

    def sample(self) -> None:
        for catalog in self.catalogs.values():
            try:
                catalog.sample()
            except Exception:
                log.exception("device history sample failed at %s", catalog.site_id)
        day = int(self._now().timestamp()) // 86400
        if self._thinned != day:
            self._thinned = day
            self._store.thin(int(self._now().timestamp()) - THIN_AFTER)

    def _loop(self) -> None:
        while not self._stop.wait(self._sample_interval):
            self.sample()

    def start(self) -> None:
        if self._thread is not None or not self.catalogs:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="device-history", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        self._store.close()


# --- demo ------------------------------------------------------------------------------

class FakeDeviceSource:
    """Offline stand-in for a site's Home Assistant: a few areas and devices
    that answer commands at once."""

    def __init__(self, site_id: str, *, now=utc_now):
        self.site_id = site_id
        self._now = now
        self._lock = threading.Lock()
        self._commands: dict[str, dict] = {}
        self._registry, self._states = _demo_site(site_id, now().isoformat())

    def registry(self) -> dict:
        return self._registry

    def raw_states(self) -> dict:
        t = self._now().timestamp()
        with self._lock:
            out = dict(self._states)
            for entity_id, state in out.items():
                if entity_id.startswith("sensor.") and "temperature" in entity_id:
                    base = float(state["attributes"].get("base", 21))
                    out[entity_id] = {**state, "state": f"{base + 0.6 * math.sin(t / 3600):.1f}"}
            return out

    def bound_entities(self) -> set:
        return set()

    def health(self) -> dict:
        return {"site_id": self.site_id, "state": "ok", "registry_error": None}

    def command(self, request_id: str) -> dict | None:
        with self._lock:
            return self._commands.get(request_id)

    def call(self, domain, service, data, entity_id=None, *, target=None) -> None:
        area = (target or {}).get("area_id")
        with self._lock:
            for entry in self._registry["entities"]:
                if entry["entity_id"].startswith("light.") and entry.get("area_id") == area:
                    st = self._states[entry["entity_id"]]
                    self._states[entry["entity_id"]] = {**st, "state": "off"}

    def submit_entity(self, entity_id, domain, service, data, actor, confirm=None) -> dict:
        import uuid
        with self._lock:
            st = dict(self._states[entity_id])
            attrs = dict(st["attributes"])
            if service == "turn_on" and domain in ("light", "switch", "input_boolean", "fan",
                                                   "automation"):
                st["state"] = "on"
                if "brightness_pct" in data:
                    attrs["brightness"] = round(data["brightness_pct"] * 255 / 100)
                if "color_temp_kelvin" in data:
                    attrs["color_temp_kelvin"] = data["color_temp_kelvin"]
            elif service == "turn_off":
                st["state"] = "off"
            elif service == "set_cover_position":
                attrs["current_position"] = data["position"]
                st["state"] = "open" if data["position"] else "closed"
            elif service in ("open_cover", "close_cover"):
                attrs["current_position"] = 100 if service == "open_cover" else 0
                st["state"] = "open" if service == "open_cover" else "closed"
            elif service == "set_temperature":
                attrs["temperature"] = data["temperature"]
            elif domain in ("scene", "script"):
                attrs["last_triggered"] = self._now().isoformat()
                if domain == "scene":
                    st["state"] = self._now().isoformat()
            st["attributes"] = attrs
            st["last_changed"] = self._now().isoformat()
            self._states[entity_id] = st
            cmd = {"request_id": uuid.uuid4().hex, "site_id": self.site_id,
                   "device_id": "entity:" + entity_id, "action": f"{domain}.{service}",
                   "value": data, "actor": actor, "state": "confirmed", "detail": "",
                   "created_at": self._now().isoformat(), "finished_at": self._now().isoformat()}
            self._commands[cmd["request_id"]] = cmd
            return cmd


def _demo_site(site_id: str, ts: str) -> tuple[dict, dict]:
    if site_id == "cottage":
        areas = [("sauna", "Sauna", None), ("kuisti", "Kuisti", None)]
        devices = [
            ("sauna", "sensor.sauna_temperature", "Saunan lämpötila",
             {"device_class": "temperature", "unit_of_measurement": "°C", "base": 18}, "18.0"),
            ("kuisti", "light.kuisti", "Kuistin valo",
             {"supported_color_modes": ["onoff"]}, "off"),
            ("kuisti", "switch.jouluvalot", "Jouluvalot",
             {"device_class": "outlet"}, "off"),
        ]
        automations = []
    else:
        areas = [("olohuone", "Olohuone", "1"), ("keittio", "Keittiö", "1"),
                 ("piha", "Piha", None)]
        devices = [
            ("olohuone", "sensor.olohuone_temperature", "Olohuoneen lämpötila",
             {"device_class": "temperature", "unit_of_measurement": "°C", "base": 22}, "22.0"),
            ("olohuone", "sensor.olohuone_humidity", "Olohuoneen kosteus",
             {"device_class": "humidity", "unit_of_measurement": "%"}, "38"),
            ("olohuone", "light.olohuone_katto", "Kattovalo",
             {"supported_color_modes": ["color_temp"], "brightness": 180,
              "color_temp_kelvin": 2700, "min_color_temp_kelvin": 2200,
              "max_color_temp_kelvin": 6500}, "on"),
            ("olohuone", "scene.elokuvailta", "Elokuvailta", {}, ts),
            ("keittio", "switch.kahvinkeitin", "Kahvinkeitin", {"device_class": "outlet"}, "off"),
            ("keittio", "sensor.kahvinkeitin_power", "Kahvinkeittimen teho",
             {"device_class": "power", "unit_of_measurement": "W"}, "0"),
            ("keittio", "binary_sensor.jaakaappi_ovi", "Jääkaapin ovi",
             {"device_class": "door"}, "off"),
            ("piha", "switch.jouluvalot", "Jouluvalot", {"device_class": "outlet"}, "off"),
            ("piha", "light.pihavalo", "Pihavalo", {"supported_color_modes": ["brightness"],
                                                    "brightness": 255}, "off"),
        ]
        automations = [("automation.ulkovalot_iltaisin", "Ulkovalot iltaisin", "on"),
                       ("automation.yovalo", "Yövalo käytävään", "off")]
    floors = [{"floor_id": "1", "name": "1. kerros", "level": 1}] if site_id == "home" else []
    registry = {"areas": [{"area_id": a, "name": n, "floor_id": f} for a, n, f in areas],
                "floors": floors, "devices": [], "entities": []}
    states = {}
    for area, entity_id, name, attrs, state in devices:
        registry["entities"].append({"entity_id": entity_id, "id": "demo-" + entity_id,
                                     "area_id": area, "device_id": None})
        states[entity_id] = {"entity_id": entity_id, "state": state,
                             "attributes": {"friendly_name": name, **attrs},
                             "last_changed": ts}
    for entity_id, name, state in automations:
        registry["entities"].append({"entity_id": entity_id, "id": "demo-" + entity_id,
                                     "area_id": None, "device_id": None})
        states[entity_id] = {"entity_id": entity_id, "state": state,
                             "attributes": {"friendly_name": name, "last_triggered": ts},
                             "last_changed": ts}
    return registry, states
