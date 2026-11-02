"""Cottage site in Villa Kuppis: bound devices, room readings, history and
mode events (card H2.3, read-only).

The cottage Home Assistant (reached through Tailscale) is wrapped by
``HomeAssistantGateway``. ``CottageSite`` owns that gateway for the service
lifetime and adds what the product shows:

* ``view()``: per room the ALPSTUGA readings, the Mill heaters and the room
  status sensor (``sensor.villa_kuppis_<room>_target``), each with its
  ok/stale/unavailable/missing/unknown status and source time, plus the
  connection state of the site;
* a sampling loop that records readings into ``pumps.db`` with the pump
  history rule (a row on change or every HISTORY_INTERVAL_SECONDS). Only
  readings whose status is ``ok`` are recorded, at their source time, so a
  stale or unavailable device leaves a gap instead of repeated old values;
* mode events: when a room's status sensor changes mode, a ``site_event``
  row says who: ``Mökin ohjain`` for follow/thermostat/fallback, ``käsin``
  for manual (the manual_by entity goes to the detail). No Home Assistant
  user names are invented.

Rooms are stored on the bindings (``device_binding.room``). A sensor's role
comes from its device id (``large-temperature``, ``large-humidity``,
``large-co2``, ``large-pm25``) or else from the entity's device class.
``FakeSiteGateway`` has the same interface and drives the offline demo.

Bindings follow the cottage Home Assistant: each room status sensor
publishes the room's devices (``devices`` attribute, from the cottage
package), and ``CottageSite`` binds them as ``<room>-status``,
``<room>-temperature``/``-humidity``/``-co2``/``-pm25`` and
``<room>-heater-N``. Moving from the stand-in devices to the real ones is
an edit of the cottage package; the device ids, and so the history, stay.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .cottage_targets import ACTOR as PLAN_ACTOR
from .cottage_targets import CottageTargetSender
from .credential_file import read_token_file
from . import learning
from . import settings as app_settings
from .ha_gateway import (DEFAULT_ENTITY_STALE_AFTER, Binding, BindingError,
                         BindingStore, HomeAssistantGateway)
from .pump_service import (DB_FILENAME, HISTORY_INTERVAL_SECONDS, _iso,
                           _to_epoch)
from .prices import HELSINKI
from .visit import MAX_TARGET, MIN_TARGET, VisitError, VisitPlan, VisitStore

SITE_ID = "cottage"
SITE_LABEL = "Mökki"
SITES_DB = "sites.db"
# Room names follow the cottage Home Assistant areas (Ville, 2026-10-31).
ROOMS = {"large": "Mökki", "small": "Kammari"}


def room_label(name: str) -> str:
    """A cottage room with the site in front, without "Mökki: Mökki"."""
    return name if name == SITE_LABEL else f"{SITE_LABEL}: {name}"
SENSOR_ROLES = ("temperature", "humidity", "co2", "pm25")
_ROLE_BY_CLASS = {"temperature": "temperature", "humidity": "humidity",
                  "carbon_dioxide": "co2", "pm25": "pm25"}
ACTOR_CONTROLLER = "Mökin ohjain"
ACTOR_MANUAL = "käsin"
INTEGRATION_DOMAIN = "villa_kuppis_cottage"
DEFAULT_SAMPLE_INTERVAL = 15.0
HEARTBEAT_EVERY = timedelta(minutes=5)   # the cottage alarm waits for 60 min of silence
DEFAULT_TARGET_INTERVAL = 60.0     # price targets are sent at most this often
VISITS_DB = "visits.db"
PLAN_REASONS = {"base": "Perustaso", "threshold": "Hintaraja", "noprice": "Ei hintaa",
                "optimized": "Optimoitu", "visit": "Vierailu", "humidity": "Kosteusvahti"}
VISIT_ERRORS = (
    ("arrival time needs", "Saapumisajalla on oltava aikavyöhyke."),
    ("arrival time must be at most a day ago", "Saapumisaika voi olla enintään vuorokauden takana."),
    ("departure must be in the future", "Lähtöajan on oltava tulevaisuudessa."),
    ("no active visit", "Vierailua ei ole käynnissä."),
    ("arrival time must be within", "Saapumisaika saa olla enintään 14 päivän päässä."),
    ("target must be", f"Tavoitteen on oltava {MIN_TARGET:g}–{MAX_TARGET:g} °C."),
    ("choose at least one room", "Valitse vähintään yksi huone."),
    ("departure must be", "Lähdön on oltava saapumisen jälkeen ja enintään 30 päivän päästä siitä."),
)
VISIT_REACHED_MARGIN = 0.5     # a room counts as warm this close below the target
VISIT_LEFT_AFTER = timedelta(hours=72)   # reminder of a visit without departure
WARMING_WINDOW = timedelta(minutes=75)   # the warming rate looks this far back
# The rise just after the heaters start lags, so the rate may call a room late
# only after this much heating (no rise in an hour and arrival still count).
VISIT_LATE_FROM_RATE_AFTER = timedelta(minutes=45)
VISIT_AWAY_ACTOR = "Mökkivierailu"
# Worst first: the state shown for a room is its worst reading.
_STATUS_ORDER = ("missing", "unavailable", "stale", "unknown", "ok")
_CONNECTION = {"ok": "ok", "stale": "stale", "auth_failed": "auth_failed"}
MAX_ERROR_LENGTH = 200
MODES = ("follow", "thermostat", "fallback", "manual")
# Controller modes in which the heaters work towards the target in force.
HEATING_MODES = ("follow", "thermostat", "fallback")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _heater_state(row: tuple | None, ts: int, room_c: float | None,
                  target: float | None) -> str | None:
    """off | idle | on for one heater's latest row, None when unknown.

    ``idle``: in heat mode but set at least the deadband below the room,
    so its own thermostat does not heat (the low drive). ``on``: in heat
    mode and set at or above the target in force, so it heats while the
    room is below the target.
    """
    if row is None or ts - row[0] > learning.MAX_READING_AGE:
        return None
    _ts, mode, setpoint = row
    if mode == "off":
        return "off"
    if mode != "heat" or setpoint is None:
        return None
    if room_c is not None and setpoint <= room_c - learning.DEADBAND_C:
        return "idle"
    if target is not None and setpoint >= target - learning.DEADBAND_C:
        return "on"
    return None


def deg_text(value: float) -> str:
    """21.5 -> "21,5 °C", 5.0 -> "5 °C" (for texts and phone notices)."""
    text = f"{value:.1f}".removesuffix(".0")
    return text.replace(".", ",") + " °C"


_WATTS_RE = re.compile(r"(\d{3,4})\s*w(?![a-z])", re.IGNORECASE)


def rated_kw(*names: str | None) -> float | None:
    """A heater's rated power from its name ("Mökki 500W patteri",
    ``climate.kammari_1000w``), or None. Matter heaters report no power."""
    for name in names:
        match = _WATTS_RE.search(name or "")
        if match:
            return int(match.group(1)) / 1000
    return None


# A room heater is sized to hold about this much over the outdoor temperature
# at full power (rule of thumb), so holding a room takes (room - outdoor) / 40
# of it until the room's own rates are learned.
HEATER_DESIGN_DELTA_C = 40.0


def hold_share(room_c: float, outdoor_c: float | None, model=None) -> float | None:
    """Share of full power that holds the room where it is: the learned heat
    loss over the learned heating rate, else the sizing rule above; None
    without an outdoor reading."""
    if outdoor_c is None:
        return None
    if model is not None and getattr(model, "heat_c_per_h", None):
        share = -model.passive_rate(room_c, outdoor_c) / model.heat_c_per_h
    else:
        share = (room_c - outdoor_c) / HEATER_DESIGN_DELTA_C
    return min(1.0, max(0.0, share))


def heating_share(setpoint: float, room_c: float, hold: float | None = None) -> float:
    """Estimated share of full power a heater in heat mode uses: all of it
    when set a degree or more above the room (the controller's high drive),
    none a degree or more below, and ``hold`` (half when unknown) when set
    to the room, in between linearly."""
    base = 0.5 if hold is None else hold
    d = setpoint - room_c
    share = base + (1.0 - base) * d if d >= 0 else base * (1.0 + d)
    return min(1.0, max(0.0, share))


# --- site configuration ------------------------------------------------------------

@dataclass(frozen=True)
class SiteConfig:
    url: str
    token_file: Path


def parse_sites(raw) -> tuple:
    """Validate the ``sites`` config field -> ((site_id, SiteConfig), ...).

    Error messages never include the configured values.
    """
    if not isinstance(raw, dict) or set(raw) - {SITE_ID, "home"}:
        raise ValueError("Invalid sites configuration")
    out = []
    for site_id, entry in sorted(raw.items()):
        if not isinstance(entry, dict) or set(entry) != {"url", "token_file"}:
            raise ValueError("Invalid site configuration")
        url, token_file = entry["url"], entry["token_file"]
        try:
            parts = urlsplit(url) if isinstance(url, str) else None
            valid_url = bool(parts and parts.scheme in ("ws", "wss") and parts.hostname)
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("Site URL must be a ws:// or wss:// address")
        if not isinstance(token_file, str) or not Path(token_file).is_absolute():
            raise ValueError("Site token file path must be absolute")
        out.append((site_id, SiteConfig(url, Path(token_file))))
    return tuple(out)


def make_cottage_site(data_dir, site: SiteConfig, *, now=utc_now,
                      sample_interval: float = DEFAULT_SAMPLE_INTERVAL) -> "CottageSite":
    """The live cottage: a Home Assistant gateway over sites.db bindings."""
    store = BindingStore(Path(data_dir) / SITES_DB)
    token_file = site.token_file
    gateway = HomeAssistantGateway(SITE_ID, site.url,
                                   lambda: read_token_file(token_file), store, now=now)
    return CottageSite(data_dir, gateway, lambda: store.bindings(SITE_ID), now=now,
                       sample_interval=sample_interval, on_close=store.close,
                       binding_store=store)


# --- roles ---------------------------------------------------------------------------

def status_entity(room: str) -> str:
    return f"sensor.villa_kuppis_{room}_target"


def published_bindings(room: str, devices, label_of: Callable[[str], str],
                       bound_heaters: dict | None = None) -> list:
    """The bindings a room status sensor's ``devices`` attribute asks for.

    ``bound_heaters`` maps heater entities already bound in the room to
    their device ids: those keep their id (and so their history) when the
    cottage lists the room's heaters in another order, for example after a
    new heater joins the area. New heaters take the lowest free number.
    """
    out = [Binding(SITE_ID, f"{room}-status", status_entity(room), "sensor",
                   f"{ROOMS[room]}: tila", room)]
    if not isinstance(devices, dict):
        return out
    for role in SENSOR_ROLES:
        entity_id = devices.get(role)
        if isinstance(entity_id, str) and entity_id.startswith("sensor."):
            out.append(Binding(SITE_ID, f"{room}-{role}", entity_id, "sensor",
                               label_of(entity_id), room))
    heaters = devices.get("heaters")
    heaters = [e for e in (heaters if isinstance(heaters, list) else [])
               if isinstance(e, str) and e.startswith("climate.")]
    heaters = list(dict.fromkeys(heaters))
    kept = {e: d for e, d in (bound_heaters or {}).items()
            if e in heaters and d.startswith(f"{room}-heater-")}
    used = set(kept.values())
    index = 0
    for entity_id in heaters:
        device_id = kept.get(entity_id)
        if device_id is None:
            index += 1
            while f"{room}-heater-{index}" in used:
                index += 1
            device_id = f"{room}-heater-{index}"
            used.add(device_id)
        out.append(Binding(SITE_ID, device_id, entity_id, "climate",
                           label_of(entity_id), room))
    return out


def role_of(binding: Binding, state: dict | None = None) -> str | None:
    """heater | status | temperature | humidity | co2 | pm25, or None."""
    if binding.kind == "climate":
        return "heater"
    if binding.kind != "sensor":
        return None
    if binding.room and binding.entity_id == status_entity(binding.room):
        return "status"
    name = binding.device_id.lower()
    for role, keys in (("co2", ("co2",)), ("pm25", ("pm25", "pm2")),
                       ("humidity", ("humid",)), ("temperature", ("temp",))):
        if any(key in name for key in keys):
            return role
    device_class = ((state or {}).get("caps") or {}).get("device_class")
    return _ROLE_BY_CLASS.get(device_class)


# --- store -----------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_reading (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    ts_utc INTEGER NOT NULL,
    value REAL,
    mode TEXT,
    setpoint REAL
);
CREATE INDEX IF NOT EXISTS idx_site_reading_device
    ON site_reading (site_id, device_id, ts_utc);
CREATE TABLE IF NOT EXISTS site_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    site_id TEXT NOT NULL,
    room TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_site_event_id_desc ON site_event (id DESC);
CREATE TABLE IF NOT EXISTS site_learning (
    site_id TEXT NOT NULL,
    room TEXT NOT NULL,
    since_utc INTEGER NOT NULL,
    PRIMARY KEY (site_id, room)
);
"""


class CottageStore:
    """Site readings and events in pumps.db (own connection, one lock)."""

    def __init__(self, path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record(self, site_id: str, device_id: str, ts: int, value=None, mode=None,
               setpoint=None) -> tuple[bool, str | None]:
        """Write a row on change or after the interval -> (written, previous mode)."""
        with self._lock, self._conn:
            last = self._conn.execute(
                "SELECT ts_utc, value, mode, setpoint FROM site_reading "
                "WHERE site_id = ? AND device_id = ? ORDER BY ts_utc DESC, id DESC "
                "LIMIT 1", (site_id, device_id)).fetchone()
            previous_mode = last[2] if last else None
            if last is not None and (value, mode, setpoint) == tuple(last[1:]) \
                    and ts - last[0] < HISTORY_INTERVAL_SECONDS:
                return False, previous_mode
            self._conn.execute(
                "INSERT INTO site_reading (site_id, device_id, ts_utc, value, mode, "
                "setpoint) VALUES (?, ?, ?, ?, ?, ?)",
                (site_id, device_id, ts, value, mode, setpoint))
        return True, previous_mode

    def series(self, site_id: str, device_id: str, column: str, start, end,
               max_points: int) -> list[tuple]:
        """Downsampled (ts, value) rows of one column, last sample per bucket."""
        if column not in ("value", "setpoint"):
            raise ValueError("unknown column")
        s, e = _to_epoch(start), _to_epoch(end)
        bucket = max(1, e - s) / max(1, max_points)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT ts_utc, {column} FROM ("
                f"  SELECT ts_utc, {column}, "
                "         ROW_NUMBER() OVER (PARTITION BY CAST((ts_utc - ?) / ? AS INTEGER) "
                "                            ORDER BY ts_utc DESC, id DESC) AS rn "
                "  FROM site_reading WHERE site_id = ? AND device_id = ? "
                f"    AND ts_utc >= ? AND ts_utc < ? AND {column} IS NOT NULL"
                ") WHERE rn = 1 ORDER BY ts_utc", (s, bucket, site_id, device_id, s, e)
            ).fetchall()
        return [tuple(r) for r in rows]

    def add_event(self, ts: int, site_id: str, room: str, actor: str, action: str,
                  old_value, new_value, detail: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO site_event (ts_utc, site_id, room, actor, action, "
                "old_value, new_value, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_iso(ts), site_id, room, actor, action, old_value, new_value, detail))

    def learning_readings(self, site_id: str, temperature: str, status: str,
                          start, end, heaters=()) -> list:
        """One room's history as learning.Reading rows (card F5.2).

        The room temperature is the temperature sensor's. The heaters decide
        the control: every heater off, or set below the room, is a room
        without heating (passive). Every heater working towards the status
        sensor's target in force, while the controller runs the room
        (follow, thermostat or fallback), is heating to that target. Anything
        else (a heater unreported, unavailable, switched off while others
        heat, or a manual change) leaves the control unknown, so learning
        skips those steps. A room without heaters has nothing to learn.
        """
        heaters = tuple(heaters)
        if not heaters:
            return []
        s, e = _to_epoch(start), _to_epoch(end)
        devices = (temperature, status, *heaters)
        marks = ", ".join("?" * len(devices))
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, device_id, value, mode, setpoint FROM site_reading "
                f"WHERE site_id = ? AND device_id IN ({marks}) AND ts_utc >= ? "
                "AND ts_utc < ? ORDER BY ts_utc, id",
                (site_id, *devices, s, e)).fetchall()
        out, room, control = [], None, None
        last: dict[str, tuple] = {}    # heater -> (ts, mode, setpoint)
        for ts, device, value, mode, setpoint in rows:
            if device == temperature:
                room = (ts, value)
            elif device == status:
                control = (ts, value, mode)
            else:
                last[device] = (ts, mode, setpoint)
            room_c = room[1] if room and ts - room[0] <= learning.MAX_READING_AGE else None
            target = (control[1] if control and ts - control[0] <= learning.MAX_READING_AGE
                      and control[2] in HEATING_MODES else None)
            states = {_heater_state(last.get(h), ts, room_c, target) for h in heaters}
            if states <= {"off", "idle"}:
                out.append(learning.Reading(ts, room_c, None, False, None))
            elif states == {"on"}:
                out.append(learning.Reading(ts, room_c, target, True, "Heat"))
            else:
                out.append(learning.Reading(ts, room_c, None, None, None))
        return out

    def energy_estimate(self, site_id: str, temperature: str, heaters: dict[str, float],
                        start, end, hold: Callable[[int, float], float | None] | None = None
                        ) -> list[tuple[int, float]]:
        """Estimated (hour start, kWh) of a room's heaters: each heater's
        rated power (``heaters``: device id -> kW) times ``heating_share`` of
        its setpoint against the room reading, while both are fresh.
        ``hold(ts, room_c)`` gives the share that holds the room then."""
        if not heaters:
            return []
        s, e = _to_epoch(start), _to_epoch(end)
        devices = (temperature, *heaters)
        marks = ", ".join("?" * len(devices))
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, device_id, value, mode, setpoint FROM site_reading "
                f"WHERE site_id = ? AND device_id IN ({marks}) AND ts_utc >= ? "
                "AND ts_utc < ? ORDER BY ts_utc, id",
                (site_id, *devices, s - learning.MAX_READING_AGE, e)).fetchall()
        hours: dict[int, float] = {}
        room: tuple | None = None
        last: dict[str, tuple] = {}

        def power(t: int) -> float:
            if room is None or room[1] is None or t - room[0] > learning.MAX_READING_AGE:
                return 0.0
            total = 0.0
            base = hold(t, room[1]) if hold is not None else None
            for device, kw in heaters.items():
                row = last.get(device)
                if (row is not None and t - row[0] <= learning.MAX_READING_AGE
                        and row[1] == "heat" and row[2] is not None):
                    total += kw * heating_share(row[2], room[1], base)
            return total

        def add(a: int, b: int, kw: float) -> None:
            while a < b:
                hour = a - a % 3600
                upto = min(b, hour + 3600)
                hours[hour] = hours.get(hour, 0.0) + kw * (upto - a) / 3600
                a = upto

        prev = None
        for ts, device, value, mode, setpoint in [*rows, (e, None, None, None, None)]:
            if prev is not None:
                # A silent stretch counts only as long as the readings stay fresh.
                a, b = max(prev, s), min(ts, e, prev + learning.MAX_READING_AGE)
                kw = power(prev)
                if kw and a < b:
                    add(a, b, kw)
            if device == temperature:
                room = (ts, value)
            elif device is not None:
                last[device] = (ts, mode, setpoint)
            prev = ts
        return sorted((hour, round(kwh, 4)) for hour, kwh in hours.items() if kwh > 0)

    def learn_since(self, site_id: str, room: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT since_utc FROM site_learning WHERE site_id = ? AND room = ?",
                (site_id, room)).fetchone()
        return row[0] if row else None

    def set_learn_since(self, site_id: str, room: str, ts: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO site_learning (site_id, room, since_utc) VALUES (?, ?, ?) "
                "ON CONFLICT (site_id, room) DO UPDATE SET since_utc = excluded.since_utc",
                (site_id, room, ts))

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts_utc, site_id, room, actor, action, new_value, detail "
                "FROM site_event ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": f"{r[2]}-{r[0]}", "ts": r[1], "site": r[2],
                 "unit": f"{r[2]}:{r[3]}",
                 "unit_name": room_label(ROOMS.get(r[3], r[3])),
                 "actor": r[4], "action": r[5], "requested": r[6],
                 "result": "observed", "detail": r[7]} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- site ----------------------------------------------------------------------------------

def _worst(statuses) -> str:
    known = [s if s in _STATUS_ORDER else "unknown" for s in statuses]
    return min(known, key=_STATUS_ORDER.index, default="unknown")


def reading_status(role: str | None, state: dict, now: datetime) -> str:
    """The gateway's status, except that a live room status sensor is not unavailable.

    In manual mode the sensor has no target, so Home Assistant shows it as
    ``unknown``; its ``mode`` attribute still proves the controller is alive.
    """
    status = state.get("status", "unknown")
    if (role == "status" and status == "unavailable"
            and (state.get("attributes") or {}).get("mode") in MODES):
        try:
            age = (now - datetime.fromisoformat(state["source_ts"])).total_seconds()
        except (KeyError, TypeError, ValueError):
            return "unknown"
        return "stale" if age > DEFAULT_ENTITY_STALE_AFTER else "ok"
    return status


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


class HeaterSwitchError(ValueError):
    """A heater on/off request that cannot be sent; the message is Finnish."""


def heater_switched(entity_id: str, enabled: bool) -> Callable[[dict], bool]:
    """Read-back check: the room status sensor lists the heater as switched off or not."""
    def check(state: dict) -> bool:
        disabled = (state.get("attributes") or {}).get("disabled_heaters")
        return isinstance(disabled, list) and (entity_id not in disabled) == enabled
    return check


def room_sensor_chosen(entity_id: str | None) -> Callable[[dict], bool]:
    """Read-back check: the room status sensor shows the chosen sensor."""
    def check(state: dict) -> bool:
        attrs = state.get("attributes") or {}
        return "chosen_sensor" in attrs and attrs["chosen_sensor"] == entity_id
    return check


def manual_cleared(state: dict) -> bool:
    """Read-back check: the room is out of its manual pause."""
    attrs = state.get("attributes") or {}
    return attrs.get("mode") != "manual" or attrs.get("manual_until") is None


class CottageSite:
    """The cottage's gateway, readings, history and events for PumpService.

    ``gateway`` offers ``start()``, ``stop()``, ``snapshot()`` and ``health()``
    (``HomeAssistantGateway`` or ``FakeSiteGateway``); ``bindings`` returns the
    site's current ``Binding`` list.
    """

    def __init__(self, data_dir, gateway, bindings: Callable[[], list], *,
                 now=utc_now, sample_interval: float = DEFAULT_SAMPLE_INTERVAL,
                 on_close: Callable[[], None] | None = None,
                 target_interval: float = DEFAULT_TARGET_INTERVAL,
                 binding_store: BindingStore | None = None):
        self.site_id = SITE_ID
        self._binding_store = binding_store
        self._gateway = gateway
        self._bindings = bindings
        self._now = now
        self._sample_interval = sample_interval
        self._on_close = on_close
        self._store = CottageStore(Path(data_dir) / DB_FILENAME)
        self._visits = VisitStore(Path(data_dir) / VISITS_DB)
        self._target_interval = float(target_interval)
        self._targets_cfg: dict | None = None
        self._sender: CottageTargetSender | None = None
        self._sender_map: dict | None = None
        self._tracked: dict[str, tuple] = {}     # room -> (request_id, target, revision)
        self._acked: dict[str, str] = {}         # room -> time of the last acknowledgement
        self._logged: set = set()
        self._target_lock = threading.Lock()
        self._heartbeat_at: datetime | None = None
        self._heartbeat_known = False      # the integration has the service
        self._known: frozenset = frozenset()
        self._roles: dict[str, str] = {}   # device id -> role seen with a reading
        self._switches: dict[str, str] = {}   # heater device id -> last on/off request id
        self._sample_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._target_thread: threading.Thread | None = None

    @property
    def gateway(self):
        """The cottage Home Assistant gateway (also the source of its other devices)."""
        return self._gateway

    # -- lifecycle --

    def start(self) -> None:
        if self._thread is not None:
            return
        self._gateway.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cottage-sampler",
                                        daemon=True)
        self._thread.start()
        if self._targets_cfg is not None:
            self._target_thread = threading.Thread(target=self._target_loop,
                                                   name="cottage-targets", daemon=True)
            self._target_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        if self._target_thread is not None:
            self._target_thread.join(timeout=10.0)
            self._target_thread = None
        try:
            self._gateway.stop()
        finally:
            self._store.close()
            self._visits.close()
            if self._on_close is not None:
                self._on_close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as error:
                logging.error("Cottage sampling failed: %s", type(error).__name__)
            self.heartbeat()
            self._stop.wait(self._sample_interval)

    def heartbeat(self) -> bool:
        """Tell the cottage integration that Villa Kuppis runs, at most every
        HEARTBEAT_EVERY: its own alarm speaks up only when these stop (the
        integration's alarm.py). True when one was sent."""
        now = self._now()
        if self._heartbeat_at is not None and now - self._heartbeat_at < HEARTBEAT_EVERY:
            return False
        call = getattr(self._gateway, "call", None)
        if call is None or self._gateway.health()["state"] not in ("ok", "stale"):
            return False
        self._heartbeat_at = now
        try:
            if not self._heartbeat_known:
                if "heartbeat" not in self._gateway.services(INTEGRATION_DOMAIN):
                    return False        # an integration older than 0.7.0
                self._heartbeat_known = True
            call(INTEGRATION_DOMAIN, "heartbeat", {})
            return True
        except Exception as error:
            self._heartbeat_known = False   # look again: the integration may be gone
            logging.warning("Cottage heartbeat failed: %s", type(error).__name__)
            return False

    # -- price targets and visits (cards H2.4, F4.3) --

    def configure_targets(self, *, prices_db, overrides: Callable[[], dict],
                          automation_on: Callable[[], bool],
                          models: Callable[[], tuple] | None = None,
                          after_tick: Callable[[], None] | None = None) -> None:
        """Enable the price target sender; call before ``start``.

        ``models`` returns the validated room models and the outdoor
        forecast for the heating optimizer (card F5.2); ``after_tick`` runs
        after each target tick (the home away mode during a visit).
        """
        self._targets_cfg = {"prices_db": Path(prices_db), "overrides": overrides,
                             "automation_on": automation_on, "models": models,
                             "room_temps": self.room_temperatures,
                             "room_humidity": self.room_humidity}
        self._after_tick = after_tick

    def _target_loop(self) -> None:
        while not self._stop.wait(self._target_interval):
            try:
                self.tick_targets()
            except Exception as error:
                logging.error("Cottage target tick failed: %s", type(error).__name__)
            after = getattr(self, "_after_tick", None)
            if after is not None:
                try:
                    after()
                except Exception as error:
                    logging.error("After the cottage target tick: %s", type(error).__name__)

    def _ensure_sender(self) -> CottageTargetSender | None:
        """The sender for the rooms whose status sensor is bound (rebuilt when that changes)."""
        if self._targets_cfg is None:
            return None
        mapping = {b.room: b.device_id for b in self._bindings()
                   if b.room in ROOMS and role_of(b) == "status"}
        if mapping != self._sender_map:
            missing = sorted(set(ROOMS) - set(mapping))
            if missing:
                logging.warning("Cottage price targets skipped for rooms without a "
                                "status sensor binding: %s", ", ".join(missing))
            self._sender_map = mapping
            self._sender = (CottageTargetSender(
                self._gateway, mapping, now=self._now, visits=self._visits,
                **self._targets_cfg) if mapping else None)
        return self._sender

    def tick_targets(self) -> list[dict]:
        """Send the due targets and log failed writes; returns the commands sent."""
        with self._target_lock:
            sender = self._ensure_sender()
            if sender is None:
                return []
            sent = sender.tick()
            for room, cmd in zip(self._rooms_of(sender, sent), sent):
                self._tracked[room] = (cmd["request_id"], cmd["value"]["target"],
                                       cmd["value"]["revision"])
            self._collect()
            return sent

    @staticmethod
    def _rooms_of(sender, sent) -> list:
        return [cmd["value"]["room"] for cmd in sent]

    def _collect(self) -> None:
        """Note acknowledgements; failed or unknown writes go to the event log.

        Confirmed writes are not logged (a heartbeat every period would flood it).
        """
        for room, (request_id, target, revision) in list(self._tracked.items()):
            cmd = self._gateway.command(request_id)
            if cmd is None:
                del self._tracked[room]
            elif cmd["state"] == "confirmed":
                self._acked[room] = cmd.get("finished_at") or self._now().isoformat()
                del self._tracked[room]
            elif cmd["state"] in ("failed", "unknown"):
                del self._tracked[room]
                key = (room, revision, cmd["state"])
                if key in self._logged:
                    continue
                self._logged.add(key)
                if len(self._logged) > 200:
                    self._logged.clear()
                text = ("Tavoitteen kirjoitus epäonnistui" if cmd["state"] == "failed"
                        else "Tavoitteen kirjoitusta ei voitu varmistaa")
                self._store.add_event(int(self._now().timestamp()), SITE_ID, room,
                                      PLAN_ACTOR, "target", None, f"{target:g}",
                                      f"{text}: {cmd.get('detail') or '-'}")

    def _refresh_plan(self) -> None:
        with self._target_lock:
            sender = self._ensure_sender()
            if sender is not None:
                sender.preview()

    def set_heater_enabled(self, device_id, enabled, actor: str) -> dict:
        """Switch one heater off or back on through its room's integration.

        Raises ``HeaterSwitchError`` when the heater or its room status
        sensor is not bound, or the integration is too old to switch heaters.
        """
        if not isinstance(device_id, str) or not isinstance(enabled, bool):
            raise HeaterSwitchError("Patteri ja päälle/pois-tila puuttuvat.")
        bindings = {b.device_id: b for b in self._bindings() if b.room in ROOMS}
        heater = bindings.get(device_id)
        if heater is None or role_of(heater) != "heater":
            raise HeaterSwitchError("Patteria ei löydy mökin laitteista.")
        status = next((b for b in bindings.values()
                       if b.room == heater.room and role_of(b) == "status"), None)
        attrs = (self._gateway.snapshot().get(status.device_id) or {}).get("attributes") \
            if status is not None else None
        if not isinstance(attrs, dict) or "disabled_heaters" not in attrs:
            raise HeaterSwitchError("Mökin integraatio ei vielä tue pattereiden "
                                    "kytkemistä: päivitä se (install.sh).")
        cmd = self._gateway.submit_service(
            status.device_id, INTEGRATION_DOMAIN, "set_heater_enabled",
            {"entity_id": heater.entity_id, "enabled": enabled}, actor,
            heater_switched(heater.entity_id, enabled))
        self._switches[device_id] = cmd["request_id"]
        if cmd["state"] != "failed":
            self._store.add_event(int(self._now().timestamp()), SITE_ID, heater.room, actor,
                                  "heater", None, "on" if enabled else "off",
                                  heater.label or heater.entity_id)
        return cmd

    def set_room_sensor(self, room: str, entity_id: str | None, label: str | None,
                        actor: str) -> dict:
        """Use another temperature sensor for a room (None: the package's own).

        Raises ``HeaterSwitchError`` when the room status sensor is not bound
        or the integration is too old to choose sensors.
        """
        if room not in ROOMS or not (entity_id is None or isinstance(entity_id, str)):
            raise HeaterSwitchError("Tuntematon mökin huone.")
        status = next((b for b in self._bindings()
                       if b.room == room and role_of(b) == "status"), None)
        attrs = (self._gateway.snapshot().get(status.device_id) or {}).get("attributes") \
            if status is not None else None
        if not isinstance(attrs, dict) or "chosen_sensor" not in attrs:
            raise HeaterSwitchError("Mökin integraatio ei vielä tue anturin valintaa: "
                                    "päivitä se (install.sh).")
        data = {"room": room, **({"entity_id": entity_id} if entity_id else {})}
        cmd = self._gateway.submit_service(status.device_id, INTEGRATION_DOMAIN,
                                           "set_room_sensor", data, actor,
                                           room_sensor_chosen(entity_id))
        if cmd["state"] != "failed":
            self._store.add_event(int(self._now().timestamp()), SITE_ID, room, actor,
                                  "room_sensor", None, label or entity_id or "oletus",
                                  ROOMS[room])
        return cmd

    def clear_manual(self, room, actor: str) -> dict:
        """End a room's manual pause at once ("Palauta automaatio").

        Raises ``HeaterSwitchError`` when the room status sensor is not bound.
        """
        if room not in ROOMS:
            raise HeaterSwitchError("Tuntematon mökin huone.")
        status = next((b for b in self._bindings()
                       if b.room == room and role_of(b) == "status"), None)
        if status is None:
            raise HeaterSwitchError("Huoneen tilatietoa ei ole liitetty.")
        cmd = self._gateway.submit_service(status.device_id, INTEGRATION_DOMAIN,
                                           "clear_manual", {"room": room}, actor,
                                           manual_cleared)
        if cmd["state"] != "failed":
            self._store.add_event(int(self._now().timestamp()), SITE_ID, room, actor,
                                  "clear_manual", None, "auto", ROOMS[room])
        return cmd

    def active_visit(self):
        return self._visits.active()

    def visit_notice_sent(self, visit_id: int, kind: str) -> bool:
        return self._visits.notice_sent(visit_id, kind)

    def mark_visit_notice(self, visit_id: int, kind: str) -> None:
        self._visits.mark_notice(visit_id, kind, self._now())

    def _warming(self, room_id: str, now: datetime, since: datetime | None = None) -> dict | None:
        """The room's fresh temperature and how fast it rose over the last
        hour (°C/h, None with under 30 minutes of readings). With ``since``
        (the heat-up start) the rate leaves out the flat readings before it."""
        snapshot = self._gateway.snapshot()
        for binding in sorted(self._bindings(), key=lambda b: b.device_id):
            state = snapshot.get(binding.device_id) or {}
            if binding.room != room_id or self._role(binding, state) != "temperature":
                continue
            value = _num(state.get("value"))
            if reading_status("temperature", state, now) != "ok" or value is None:
                return {"value": value, "fresh": False, "rate": None}
            # Rows are stored on change, so the end point is the reading now.
            start = now - WARMING_WINDOW if since is None else max(since, now - WARMING_WINDOW)
            rows = [(ts, v) for ts, v in self._store.series(
                SITE_ID, binding.device_id, "value", start, now, 100)
                if v is not None]
            span = now.timestamp() - rows[0][0] if rows else 0
            rate = (value - rows[0][1]) * 3600 / span if span >= 1800 else None
            return {"value": value, "fresh": True, "rate": rate}
        return None

    def _progress(self, visit, now: datetime) -> dict:
        """Each visit room on its way to the target, once the heat-up has
        begun: reached, the estimated time it gets there (eta) and late,
        when it will not make the arrival or has not made it."""
        plan = self._sender.visit_plan if self._sender is not None else None
        start = visit.heat_start or (plan.start if plan is not None and plan.reason == "started" else None)
        if start is None or now < start:
            return {}
        # A visit started on site has nobody to warn about being late.
        planned = start < visit.arrival
        out = {}
        for room in visit.rooms:
            info = self._warming(room, now, since=start)
            if info is None:
                continue
            item = {**info, "reached": False, "eta": None, "late": False}
            goal = visit.target_c - VISIT_REACHED_MARGIN
            if info["fresh"] and info["value"] >= goal:
                item["reached"] = True
            elif info["fresh"] and planned:
                rate = info["rate"]
                if rate is not None and rate > 0.1:
                    eta = now + timedelta(hours=(goal - info["value"]) / rate)
                    item["eta"] = eta.isoformat()
                    item["late"] = (now - start >= VISIT_LATE_FROM_RATE_AFTER
                                    and eta > visit.arrival + timedelta(minutes=15))
                elif now - start >= timedelta(hours=1):
                    item["late"] = True        # an hour of heating and no rise
                if now >= visit.arrival:
                    item["late"] = True
            if info["rate"] is not None:
                item["rate"] = round(info["rate"], 2)
            out[room] = item
        return out

    def visit_view(self) -> dict:
        visit = self._visits.active()
        if visit is None:
            return {"visit": None, "plan": None}
        plan = self._sender.visit_plan if self._sender is not None else None
        if plan is None and visit.heat_start is not None:
            plan = VisitPlan(visit.heat_start, "started", None)
        return {
            "visit": {"arrival": visit.arrival.isoformat(), "target": visit.target_c,
                      "rooms": list(visit.rooms), "created_by": visit.created_by,
                      "heat_start": visit.heat_start.isoformat() if visit.heat_start else None,
                      "departure": visit.departure.isoformat() if visit.departure else None},
            "plan": ({"start": plan.start.isoformat(), "reason": plan.reason}
                     if plan is not None else None),
            "progress": self._progress(visit, self._now())}

    def visit_notices(self) -> list[tuple[int, str, str, str]]:
        """Due phone notices of the active visit: (visit id, kind, title,
        message), kind warm | late | left. The caller sends each once."""
        visit = self._visits.active()
        if visit is None:
            return []
        now = self._now()
        out = []
        progress = self._progress(visit, now)
        names = lambda rooms: ", ".join(ROOMS.get(r, r) for r in rooms)
        if progress and len(progress) == len(visit.rooms) and all(
                p["reached"] for p in progress.values()):
            out.append((visit.visit_id, "warm", "Mökki on lämmin",
                        ", ".join(f"{ROOMS.get(r, r)} {deg_text(p['value'])}" for r, p in progress.items())
                        + f". Tavoite {deg_text(visit.target_c)}."))
        late = [r for r, p in progress.items() if p["late"] and not p["reached"]]
        if late:
            out.append((visit.visit_id, "late", "Mökki ei ehdi lämmetä ajoissa",
                        f"{names(late)}: " + ", ".join(
                            deg_text(progress[r]["value"]) if progress[r]["fresh"] else "ei lukemaa"
                            for r in late)
                        + f". Tavoite {deg_text(visit.target_c)} saapumiseen "
                        + visit.arrival.astimezone(HELSINKI).strftime("%-d.%-m. klo %H.%M")
                        + " mennessä ei ehkä toteudu."))
        if visit.departure is None and now >= visit.arrival + VISIT_LEFT_AFTER:
            out.append((visit.visit_id, "left", "Mökkivierailu on yhä päällä",
                        f"Huoneet pidetään {deg_text(visit.target_c)} lämmössä. Lopeta vierailu "
                        "Villa Kuppisissa, jos olet jo lähtenyt mökiltä."))
        return out

    @staticmethod
    def _visit_arrival(arrival) -> datetime:
        if not isinstance(arrival, str):
            raise VisitError("Saapumisaika puuttuu.")
        try:
            return datetime.fromisoformat(arrival)
        except ValueError:
            raise VisitError("Saapumisaika ei ole kelvollinen.") from None

    @staticmethod
    def _visit_departure(departure) -> datetime | None:
        if departure in (None, ""):
            return None
        try:
            value = datetime.fromisoformat(departure) if isinstance(departure, str) else None
        except ValueError:
            value = None
        if value is None:
            raise VisitError("Lähtöaika ei ole kelvollinen.")
        return value

    @staticmethod
    def _visit_target(target) -> float:
        if isinstance(target, bool) or not isinstance(target, (int, float)) \
                or not math.isfinite(target):
            raise VisitError("Tavoitteen on oltava luku.")
        return float(target)

    @staticmethod
    def _visit_rooms(rooms) -> list:
        if not isinstance(rooms, list) or not all(isinstance(r, str) for r in rooms) \
                or not set(rooms) <= set(ROOMS):
            raise VisitError("Tuntematon huone.")
        return [r for r in ROOMS if r in rooms]

    @staticmethod
    def _visit_error(error: VisitError, fallback: str) -> VisitError:
        message = str(error)
        return VisitError(next((fi for key, fi in VISIT_ERRORS if message.startswith(key)),
                               fallback))

    def start_visit(self, arrival, target, rooms, actor: str, departure=None) -> dict:
        """Start a visit from request values; raises ``VisitError`` (Finnish message).
        An arrival now or earlier (on site, up to a day) heats at once."""
        arrival_dt = self._visit_arrival(arrival)
        departure_dt = self._visit_departure(departure)
        target_c = self._visit_target(target)
        room_ids = self._visit_rooms(rooms)
        try:
            self._visits.start(arrival_dt, target_c, room_ids, actor, self._now(), departure_dt)
        except VisitError as error:
            raise self._visit_error(error, "Vierailua ei voitu aloittaa.") from None
        self._retarget()
        return self.visit_view()

    def update_visit(self, body: dict) -> dict:
        """Change the active visit from request values (any of arrival,
        departure, target, rooms; departure null clears it); raises
        ``VisitError`` (Finnish message)."""
        changes = {}
        if "arrival" in body:
            changes["arrival"] = self._visit_arrival(body["arrival"])
        if "departure" in body:
            changes["departure"] = self._visit_departure(body["departure"])
        if "target" in body:
            changes["target_c"] = self._visit_target(body["target"])
        if "rooms" in body:
            changes["rooms"] = self._visit_rooms(body["rooms"])
        try:
            self._visits.update(self._now(), **changes)
        except VisitError as error:
            raise self._visit_error(error, "Vierailua ei voitu muuttaa.") from None
        self._retarget()
        return self.visit_view()

    def end_visit(self) -> dict:
        self._visits.end(self._now())
        self._retarget()
        return self.visit_view()

    def _retarget(self) -> None:
        """After a visit change: send the targets now (Automaatio on), so the
        heat-up or its end does not wait for the next target tick."""
        try:
            self.tick_targets()
        except Exception as error:
            logging.error("Cottage target tick after a visit change failed: %s",
                          type(error).__name__)
            self._refresh_plan()

    def _plan_view(self, room_id: str) -> dict | None:
        sender = self._sender
        target = sender.last.get(room_id) if sender is not None else None
        if target is None:
            return None
        overrides = self._targets_cfg["overrides"]() if self._targets_cfg else {}
        return {"target": target.target_c, "reason": target.reason,
                "reason_text": PLAN_REASONS.get(target.reason, target.reason),
                "price": float(target.price_c_kwh) if target.price_c_kwh is not None else None,
                "vat": app_settings.vat_factor(overrides, SITE_ID) != 1.0,
                "acked_at": self._acked.get(room_id)}

    def room_temperatures(self) -> dict[str, float]:
        """Each room's current temperature sensor value, when it is ok."""
        return self._room_values("temperature")

    def room_humidity(self) -> dict[str, float]:
        """Each room's current relative humidity (%), when it is ok."""
        return self._room_values("humidity")

    def above_since(self, room_id: str, role: str, limit: float,
                    lookback: timedelta) -> datetime | None:
        """Since when the room's stored ``role`` readings have stayed over
        ``limit`` without a break (at most ``lookback`` back), or None when
        the latest one is not over it. Survives an app restart."""
        now = self._now()
        snapshot = self._gateway.snapshot()
        for binding in sorted(self._bindings(), key=lambda b: b.device_id):
            if binding.room != room_id or self._role(binding, snapshot.get(binding.device_id)) != role:
                continue
            since = None
            for ts, value in reversed(self._store.series(
                    SITE_ID, binding.device_id, "value", now - lookback, now, 1000)):
                if value is None or value <= limit:
                    break
                since = ts
            return datetime.fromtimestamp(since, timezone.utc) if since is not None else None
        return None

    def _room_values(self, role: str) -> dict[str, float]:
        snapshot = self._gateway.snapshot()
        out = {}
        for binding in sorted(self._bindings(), key=lambda b: b.device_id):
            state = snapshot.get(binding.device_id) or {}
            if (binding.room in ROOMS and binding.room not in out
                    and self._role(binding, state) == role
                    and reading_status(role, state, self._now()) == "ok"):
                value = _num(state.get("value"))
                if value is not None:
                    out[binding.room] = value
        return out

    def learning_readings(self, room_id: str, start, end) -> list:
        """The room's history for learning its rates (card F5.2), from
        ``learning_since`` on."""
        found, heaters = {}, []
        for binding in sorted(self._bindings(), key=lambda b: b.device_id):
            if binding.room == room_id:
                role = self._role(binding)
                found.setdefault(role, binding.device_id)
                if role == "heater":
                    heaters.append(binding.device_id)
        if "temperature" not in found or "status" not in found or not heaters:
            return []
        since = self.learning_since(room_id)
        if since is not None:
            start = max(_to_epoch(start), int(since.timestamp()))
            if start >= _to_epoch(end):
                return []
        return self._store.learning_readings(SITE_ID, found["temperature"],
                                             found["status"], start, end, heaters)

    def energy(self, room_id: str, start, end,
               hold: Callable[[int, float], float | None] | None = None) -> list[tuple[int, float]]:
        """Estimated (hour start, kWh) of the room's heaters whose rated power
        is in their name; [] without a temperature sensor or such heaters.
        ``hold`` as in ``CottageStore.energy_estimate``."""
        temperature, heaters = None, {}
        for binding in sorted(self._bindings(), key=lambda b: b.device_id):
            if binding.room != room_id:
                continue
            role = self._role(binding)
            if role == "temperature" and temperature is None:
                temperature = binding.device_id
            elif role == "heater":
                kw = rated_kw(binding.label, binding.entity_id)
                if kw is not None:
                    heaters[binding.device_id] = kw
        if temperature is None:
            return []
        return self._store.energy_estimate(SITE_ID, temperature, heaters, start, end, hold)

    def learning_since(self, room_id: str) -> datetime | None:
        """Where the room's learning history starts: when its temperature
        sensor or a heater was last bound to another entity (the stand-ins
        replaced by real devices, a heater added), or a fresh start chosen
        in Villa Kuppis (``restart_learning``), whichever is later."""
        times = []
        if self._binding_store is not None:
            bound = self._binding_store.bound_at(SITE_ID)
            for binding in self._bindings():
                if (binding.room == room_id and binding.device_id in bound
                        and self._role(binding) in ("temperature", "heater")):
                    times.append(bound[binding.device_id])
        chosen = self._store.learn_since(SITE_ID, room_id)
        if chosen is not None:
            times.append(datetime.fromtimestamp(chosen, timezone.utc))
        return max(times) if times else None

    def restart_learning(self, room_id: str, actor: str) -> datetime:
        """Forget the room's learned history from now on (after moving the
        heaters to the cottage, for example)."""
        if room_id not in ROOMS:
            raise ValueError("unknown cottage room")
        now = self._now()
        self._store.set_learn_since(SITE_ID, room_id, int(now.timestamp()))
        self._store.add_event(int(now.timestamp()), SITE_ID, room_id, actor, "learning",
                              None, "restart", ROOMS[room_id])
        return now

    def _role(self, binding: Binding, state: dict | None = None) -> str | None:
        role = role_of(binding, state) or self._roles.get(binding.device_id)
        if role is not None:
            self._roles[binding.device_id] = role
        return role

    # -- sampling --

    def _sync_bindings(self, bindings: list) -> None:
        """Tell the gateway when bindings changed (CLI bind/unbind while running)."""
        known = frozenset((b.device_id, b.entity_id, b.kind, b.room) for b in bindings)
        if known != self._known:
            self._known = known
            reload = getattr(self._gateway, "reload_bindings", None)
            if reload is not None:
                reload()

    def follow_published(self) -> bool:
        """Bind what the room status sensors publish; True when anything changed."""
        attributes = getattr(self._gateway, "attributes", None)
        store = self._binding_store
        if store is None or attributes is None:
            return False

        def label_of(entity_id: str) -> str:
            name = (attributes(entity_id) or {}).get("friendly_name")
            return name if isinstance(name, str) and name else entity_id

        current = {b.device_id: b for b in store.bindings(SITE_ID)}
        changed = False
        for room in ROOMS:
            attrs = attributes(status_entity(room))
            if attrs is None or "devices" not in attrs:
                continue   # an older integration or not connected: keep what is bound
            ours = [d for d, b in current.items() if b.room == room]
            bound_heaters = {current[d].entity_id: d for d in ours
                             if current[d].kind == "climate"}
            wanted = {b.device_id: b for b in published_bindings(
                room, attrs["devices"], label_of, bound_heaters)}
            # Unbind first, so an entity can move between device ids.
            for device_id in ours:
                if device_id not in wanted or current[device_id].entity_id != wanted[device_id].entity_id:
                    store.unbind(SITE_ID, device_id)
                    current.pop(device_id)
                    changed = True
            for device_id, b in wanted.items():
                if current.get(device_id) == b:
                    continue
                try:
                    store.bind(SITE_ID, device_id, b.entity_id, b.kind, b.label, room)
                except (BindingError, ValueError) as error:
                    logging.warning("Cottage binding %s skipped: %s", device_id, error)
                    continue
                if device_id not in current:
                    logging.info("Cottage %s bound to %s", device_id, b.entity_id)
                current[device_id] = b
                changed = True
        return changed

    def sample(self) -> int:
        """Record every fresh reading once; returns the rows written."""
        with self._sample_lock:
            now = int(self._now().timestamp())
            self.follow_published()
            bindings = self._bindings()
            self._sync_bindings(bindings)
            snapshot = self._gateway.snapshot()
            written = 0
            for binding in bindings:
                state = snapshot.get(binding.device_id)
                if not binding.room or state is None:
                    continue
                role = self._role(binding, state)
                if reading_status(role, state, self._now()) != "ok":
                    continue
                try:
                    ts = _to_epoch(state["source_ts"])
                except (KeyError, TypeError, ValueError):
                    ts = now
                ts = min(ts, now)
                if role in SENSOR_ROLES:
                    value = _num(state.get("value"))
                    if value is not None:
                        written += self._store.record(SITE_ID, binding.device_id, ts,
                                                      value=value)[0]
                elif role == "heater":
                    written += self._store.record(
                        SITE_ID, binding.device_id, ts, mode=state.get("mode"),
                        setpoint=_num(state.get("setpoint")))[0]
                elif role == "status":
                    written += self._record_status(binding, state, ts)
            return written

    def _record_status(self, binding: Binding, state: dict, ts: int) -> int:
        attrs = state.get("attributes") or {}
        mode = attrs.get("mode") if isinstance(attrs.get("mode"), str) else None
        wrote, previous = self._store.record(
            SITE_ID, binding.device_id, ts, value=_num(state.get("value")), mode=mode)
        if wrote and mode is not None and previous is not None and previous != mode:
            reason = attrs.get("reason") if isinstance(attrs.get("reason"), str) else ""
            if mode == "manual":
                actor = ACTOR_MANUAL
                by = attrs.get("manual_by")
                detail = reason if not by else f"{reason} (muuttaja: {by})".strip()
            else:
                actor, detail = ACTOR_CONTROLLER, reason
            self._store.add_event(ts, SITE_ID, binding.room, actor, "mode",
                                  previous, mode, detail)
        return int(wrote)

    # -- reads --

    def health(self) -> dict:
        health = dict(self._gateway.health())
        error = health.get("error")
        return {
            "site_id": SITE_ID,
            "label": SITE_LABEL,
            "state": health.get("state"),
            "connection": _CONNECTION.get(health.get("state"), "offline"),
            "last_contact": health.get("last_contact"),
            "error": error[:MAX_ERROR_LENGTH] if isinstance(error, str) else None,
            "missing_bindings": list(health.get("missing") or []),
        }

    def view(self) -> dict:
        bindings = [b for b in self._bindings() if b.room in ROOMS]
        snapshot = self._gateway.snapshot()
        rooms = []
        with self._target_lock:
            self._collect()
        for room_id, name in ROOMS.items():
            room = {"id": room_id, "name": name, "heaters": [], "status": None,
                    "plan": self._plan_view(room_id),
                    **{role: None for role in SENSOR_ROLES}}
            statuses = []
            for binding in sorted((b for b in bindings if b.room == room_id),
                                  key=lambda b: b.device_id):
                state = snapshot.get(binding.device_id) or {}
                role = self._role(binding, state)
                status = reading_status(role, state, self._now())
                statuses.append(status)
                base = {"device_id": binding.device_id,
                        "label": state.get("label") or binding.label or binding.device_id,
                        "status": status, "source_ts": state.get("source_ts")}
                if role in SENSOR_ROLES:
                    if room[role] is None:
                        room[role] = {**base, "value": _num(state.get("value")),
                                      "unit": (state.get("caps") or {}).get("unit")}
                elif role == "heater":
                    request_id = self._switches.get(binding.device_id)
                    room["heaters"].append({
                        **base, "entity_id": binding.entity_id,
                        "available": bool(state.get("available")),
                        "setpoint": _num(state.get("setpoint")),
                        "mode": state.get("mode"),
                        "switch": self._gateway.command(request_id) if request_id else None})
                elif role == "status" and room["status"] is None:
                    attrs = state.get("attributes") or {}
                    room["status"] = {
                        **base, "target": _num(state.get("value")),
                        **{key: attrs.get(key) for key in (
                            "mode", "reason", "drive", "revision", "requested_target",
                            "valid_until", "manual_until", "manual_by", "disabled_heaters",
                            "chosen_sensor", "alarm")}}
            disabled = (room["status"] or {}).get("disabled_heaters")
            for heater in room["heaters"]:
                # Older integrations have no disabled_heaters: no switch.
                heater["switchable"] = isinstance(disabled, list)
                heater["enabled"] = not (isinstance(disabled, list)
                                         and heater["entity_id"] in disabled)
            room["state"] = _worst(statuses) if statuses else "unbound"
            rooms.append(room)
        automation = self._targets_cfg["automation_on"]() if self._targets_cfg else False
        # The cottage's own alarm (integration 0.7.1+): the same on every room.
        alarm = next((r["status"]["alarm"] for r in rooms
                      if r["status"] and isinstance(r["status"].get("alarm"), dict)), None)
        return {"configured": True, "site": self.health(), "rooms": rooms,
                "automation_on": bool(automation), "alarm": alarm}

    def history(self, room_id: str, start, end, max_points: int) -> dict:
        """Series for one room: ALPSTUGA temperature, target in force, heaters."""
        if room_id not in ROOMS:
            raise ValueError("unknown cottage room")
        bindings = sorted((b for b in self._bindings() if b.room == room_id),
                          key=lambda b: b.device_id)
        out: dict = {"room_temp": [], "plan": [], "extra": []}
        for binding in bindings:
            role = self._role(binding)
            if role == "temperature" and not out["room_temp"]:
                rows = self._store.series(SITE_ID, binding.device_id, "value",
                                          start, end, max_points)
                out["room_temp"] = [[_iso(ts), v] for ts, v in rows]
            elif role == "status":
                rows = self._store.series(SITE_ID, binding.device_id, "value",
                                          start, end, max_points)
                out["plan"] = [[_iso(ts), v] for ts, v in rows]
            elif role == "heater":
                rows = self._store.series(SITE_ID, binding.device_id, "setpoint",
                                          start, end, max_points)
                out["extra"].append({
                    "key": f"heater:{binding.device_id}",
                    "label": f"Patterin asetus: {binding.label or binding.device_id}",
                    "points": [[_iso(ts), v] for ts, v in rows]})
        out["labels"] = {"plan": "Voimassa oleva tavoite",
                         "room_temp": "Huonelämpötila (ALPSTUGA)"}
        return out

    def events(self, limit: int = 100) -> list[dict]:
        return self._store.events(limit)


# --- demo ---------------------------------------------------------------------------------------

DEMO_BINDINGS = (
    Binding(SITE_ID, "large-temperature", "sensor.large_alpstuga_temperature", "sensor",
            "ALPSTUGA", "large"),
    Binding(SITE_ID, "large-humidity", "sensor.large_alpstuga_humidity", "sensor",
            "ALPSTUGA kosteus", "large"),
    Binding(SITE_ID, "large-heater-1", "climate.large_mill_1", "climate",
            "Mill 1050 W (1)", "large"),
    Binding(SITE_ID, "large-heater-2", "climate.large_mill_2", "climate",
            "Mill 1050 W (2)", "large"),
    Binding(SITE_ID, "large-heater-3", "climate.large_mill_3", "climate",
            "Mill 500 W", "large"),
    Binding(SITE_ID, "large-status", status_entity("large"), "sensor",
            "Mökin tila", "large"),
    Binding(SITE_ID, "small-temperature", "sensor.small_alpstuga_temperature", "sensor",
            "ALPSTUGA", "small"),
    Binding(SITE_ID, "small-heater-1", "climate.small_mill_1", "climate",
            "Mill 1050 W", "small"),
    Binding(SITE_ID, "small-status", status_entity("small"), "sensor",
            "Kammarin tila", "small"),
)


class FakeSiteGateway:
    """Offline stand-in for the cottage gateway: same read interface, no network.

    Large room follows the sensor, the small room is in fallback; values drift
    slowly with the clock.
    """

    def __init__(self, *, now=utc_now):
        self._now = now
        self._started = False
        self._lock = threading.Lock()
        self._written: dict[str, dict] = {}     # room -> the integration's last accepted write
        self._commands: dict[str, dict] = {}
        self._disabled: set[str] = set()         # heater entities switched off
        self._chosen: dict[str, str] = {}        # room -> sensor picked in Villa Kuppis

    def submit_service(self, device_id, domain, service, data, actor, confirm, *,
                       target_entity: bool = False) -> dict:
        """Act like the cottage integration: accept a write and report it back."""
        now = self._now()
        cmd = {"request_id": uuid.uuid4().hex, "site_id": SITE_ID, "device_id": device_id,
               "action": f"{domain}.{service}", "value": dict(data), "actor": actor,
               "state": "confirmed", "detail": "", "created_at": now.isoformat(),
               "finished_at": now.isoformat()}
        with self._lock:
            if not self._started:
                cmd.update(state="failed", detail="Home Assistant is not connected")
            elif service == "set_room_sensor":
                if data.get("entity_id"):
                    self._chosen[data["room"]] = data["entity_id"]
                else:
                    self._chosen.pop(data["room"], None)
            elif service == "set_heater_enabled":
                if data["enabled"]:
                    self._disabled.discard(data["entity_id"])
                else:
                    self._disabled.add(data["entity_id"])
            elif service == "clear_manual":
                pass   # the demo rooms are never in a manual pause
            else:
                until = now + timedelta(minutes=float(data.get("valid_minutes", 45)))
                self._written[data["room"]] = {
                    "target": float(data["target"]), "revision": data["revision"],
                    "valid_until": until}
            self._commands[cmd["request_id"]] = cmd
            while len(self._commands) > 50:
                self._commands.pop(next(iter(self._commands)))
        return dict(cmd)

    def command(self, request_id: str) -> dict | None:
        with self._lock:
            cmd = self._commands.get(request_id)
            return dict(cmd) if cmd else None

    def bindings(self) -> list:
        return list(DEMO_BINDINGS)

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def health(self) -> dict:
        return {"site_id": SITE_ID, "state": "ok" if self._started else "stopped",
                "last_contact": self._now().isoformat() if self._started else None,
                "error": None, "missing": [], "ha_version": "demo"}

    def snapshot(self) -> dict:
        now = self._now()
        ts = now.isoformat()
        heard = datetime.fromtimestamp(int(now.timestamp()) // 300 * 300,
                                       timezone.utc).isoformat()
        wave = math.sin(now.timestamp() / 1800.0)
        large_temp = round(18.6 + 0.5 * wave, 1)
        small_temp = round(7.4 + 0.3 * wave, 1)
        values = {
            "large-temperature": large_temp,
            "large-humidity": round(42 + 4 * wave, 1),
            "small-temperature": small_temp,
        }
        status = {
            "large-status": (19.0, {
                "mode": "follow", "reason": f"room {large_temp:g} °C, target 19 °C",
                "drive": "boost" if large_temp < 19.0 else "low", "revision": 1,
                "requested_target": 19.0, "valid_until": (now + timedelta(minutes=40)).isoformat(),
                "manual_until": None, "manual_by": None}),
            "small-status": (7.0, {
                "mode": "fallback", "reason": "no target from Villa Kuppis",
                "drive": None, "revision": None, "requested_target": None,
                "valid_until": None, "manual_until": None, "manual_by": None}),
        }
        with self._lock:
            disabled = set(self._disabled)
            chosen = dict(self._chosen)
            for room, write in self._written.items():
                if write["valid_until"] <= now:
                    continue
                sensor = f"{room}-status"
                temp = large_temp if room == "large" else small_temp
                status[sensor] = (max(write["target"], 7.0), {
                    "mode": "follow", "reason": f"room {temp:g} °C, target {write['target']:g} °C",
                    "drive": "boost" if temp < write["target"] else "low",
                    "revision": write["revision"], "requested_target": write["target"],
                    "valid_until": write["valid_until"].isoformat(),
                    "manual_until": None, "manual_by": None})
        out = {}
        for binding in DEMO_BINDINGS:
            state = {"device_id": binding.device_id, "site_id": SITE_ID,
                     "entity_id": binding.entity_id, "kind": binding.kind,
                     "label": binding.label, "available": True, "source_ts": ts,
                     "caps": {}, "status": "ok", "pending": None}
            if binding.kind == "climate":
                boost = binding.room == "large" and large_temp < 19.0
                off = binding.entity_id in disabled
                state.update({
                    "power": not off, "mode": "off" if off else "heat", "fan": None,
                    "room_temp": None,
                    "setpoint": 22.0 if boost else (19.0 if binding.room == "large" else 7.0)})
            elif binding.device_id in status:
                target, attrs = status[binding.device_id]
                attrs = {**attrs, "disabled_heaters": sorted(
                    b.entity_id for b in DEMO_BINDINGS
                    if b.room == binding.room and b.entity_id in disabled),
                    "chosen_sensor": chosen.get(binding.room),
                    "alarm": {"enabled": True, "phones": ["demo_puhelin"],
                              "silent_minutes": 60, "heard": heard}}
                state.update({"value": target, "attributes": attrs,
                              "caps": {"unit": "°C", "device_class": "temperature"}})
            else:
                temperature = "temp" in binding.device_id
                state.update({"value": values[binding.device_id],
                              "caps": {"unit": "°C" if temperature else "%",
                                       "device_class": "temperature" if temperature
                                       else "humidity"}})
            out[binding.device_id] = state
        return out


# --- web page section ------------------------------------------------------------------------------

SECTION_HTML = r"""
  <section class="cottage" id="cottage-section" aria-label="Mökki" hidden>
    <div class="cottage-head">
      <h2><span data-icon="cabin"></span>Mökin huoneet</h2>
      <span id="cottage-conn" class="badge info"></span>
    </div>
    <p id="cottage-note" class="detail"></p>
    <div id="cottage-rooms" class="cottage-rooms"></div>
    <article class="card visit" id="cottage-visit">
      <header class="card-head"><h3>Vierailu</h3></header>
      <div id="visit-state"></div>
      <div class="visit-form">
        <label title="Milloin saavut mökille (Suomen aika). Jos olet jo mökillä, valitse nykyhetki: lämmitys alkaa heti. Ei oletusta.">Saapuminen
          <input type="datetime-local" id="visit-arrival"></label>
        <label title="Milloin lähdet mökiltä (Suomen aika). Valinnainen: vierailu päättyy silloin itsestään. Tyhjänä vierailu jatkuu, kunnes lopetat sen.">Lähtö
          <input type="datetime-local" id="visit-departure"></label>
        <label title="Huoneiden tavoitelämpötila vierailun ajaksi. Oletus 21 °C, sallittu 5–25 °C.">Tavoite (°C)
          <input type="number" id="visit-target" value="21" min="5" max="25" step="0.5"></label>
        <fieldset title="Huoneet, jotka lämmitetään vierailua varten. Oletuksena molemmat.">
          <legend>Huoneet</legend>
          <label><input type="checkbox" id="visit-large" checked> Mökki</label>
          <label><input type="checkbox" id="visit-small" checked> Kammari</label>
        </fieldset>
        <button type="button" id="visit-start" class="primary"
          title="Aloittaa vierailun. Vierailun aikana muutokset tallentuvat itsestään.">Aloita vierailu</button>
        <button type="button" id="visit-end" hidden
          title="Lopettaa vierailun; huoneet palaavat hintaohjaukseen.">Lopeta vierailu</button>
      </div>
      <p id="visit-msg" class="detail" role="status"></p>
    </article>
  </section>
"""

STYLE = r"""
section.cottage { display: flex; flex-direction: column; gap: 1rem; }
section.cottage[hidden] { display: none; }
.cottage-head { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem .8rem; }
.cottage-head h2 { display: flex; align-items: center; gap: .55rem; margin: 0; font-size: 1.05rem; }
.cottage-head h2 .ico { color: var(--text3); }
#cottage-note:empty { display: none; }
.cottage-rooms { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
                 gap: 1rem; }
.cottage-rooms .card.offline { border-color: var(--err); }
.cot-main { display: grid; grid-template-columns: 132px minmax(0, 1fr); gap: .9rem; align-items: center;
            margin-bottom: .5rem; }
.cot-side { display: flex; flex-direction: column; gap: .5rem; min-width: 0; }
.cot-side .fld b { font-size: 1.05rem; font-variant-numeric: tabular-nums; }
.cot-stats { display: flex; flex-wrap: wrap; gap: .4rem; margin: .2rem 0 .5rem; }
.cot-stats span { padding: .3rem .6rem; border-radius: 999px; background: var(--surface2);
                  font-size: .8rem; color: var(--text2); font-variant-numeric: tabular-nums; }
.cot-stats span b { color: var(--text); font-weight: 600; margin-left: .25rem; }
.cottage-rooms .row { border-top: 1px solid var(--line2); font-size: .9rem; }
.cottage-rooms .heater { display: flex; justify-content: space-between; gap: .6rem;
                         padding: .2rem 0; font-size: .85rem; color: var(--text2); }
.cottage-rooms .heater:first-of-type { margin-top: .3rem; }
.heater-sw { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center;
             gap: .3rem .8rem; padding: .45rem 0; border-top: 1px solid var(--line2);
             font-size: .88rem; }
.heater-sw .hs-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.heater-sw .hs-val { color: var(--text2); font-variant-numeric: tabular-nums; }
.heater-sw.off .hs-name, .heater-sw.off .hs-val { color: var(--text3); }
.heater-sw button.switch { min-height: 44px; font-size: .82rem; color: var(--text2); }
.heater-sw button.switch span:last-child { min-width: 3.2rem; text-align: left; }
.heater-sw .hs-msg { grid-column: 1 / -1; font-size: .8rem; color: var(--info); }
.heater-sw .hs-msg.err { color: var(--err); }
.heater-sw .hs-msg:empty { display: none; }
#cottage-note:not(:empty) { margin: 0; padding: .6rem .8rem; border-radius: 10px;
                            background: var(--surface2); border-left: 3px solid var(--info); }
.visit-form { display: flex; flex-wrap: wrap; gap: .8rem 1.2rem; align-items: flex-end;
              padding-top: .8rem; border-top: 1px solid var(--line2); }
.visit-form label, .visit-form fieldset { display: flex; flex-direction: column; gap: .25rem;
                                          font-size: .82rem; color: var(--text3); }
.visit-form input[type=datetime-local], .visit-form input[type=number] {
  min-height: 44px; padding: 0 .7rem; border: 1px solid var(--line3); border-radius: 10px;
  background: var(--surface); color: var(--text); font-size: .95rem; }
.visit-form input[type=number] { width: 6.5rem; }
.visit-form fieldset { border: 0; padding: 0; margin: 0; flex-direction: row; flex-wrap: wrap;
                       gap: .2rem .8rem; }
.visit-form fieldset legend { padding: 0; margin-bottom: .25rem; }
.visit-form fieldset label { flex-direction: row; align-items: center; gap: .35rem; min-height: 44px;
                             color: var(--text); font-size: .92rem; }
.visit-form input[type=checkbox] { width: 1.2rem; height: 1.2rem; accent-color: var(--accent); }
#visit-state { margin-bottom: .6rem; }
#visit-state .visit-line { margin: .15rem 0; }
#visit-state .visit-line.late { color: var(--warn); }
.visit-form button[hidden], #cottage-conn[hidden] { display: none; }
.cottage-rooms .value.old { color: var(--text3); text-decoration: line-through; }
@media (max-width: 560px) {
  .cottage-rooms { grid-template-columns: 1fr; }
  .cot-main { grid-template-columns: 116px minmax(0, 1fr); }
}
"""

SCRIPT = r"""
// One heater's on/off switch: a cottage heater from /api/cottage in, a row
// with a slider switch out. It shows a sent switch until the cottage
// confirms it, and calls onChange after a request so the caller can reload.
function vkHeaterSwitch(heater, onChange) {
  var num = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  function el(tag, cls, text, title) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    if (title) e.title = title;
    return e;
  }
  var cmd = heater.switch;
  var recent = cmd && cmd.finished_at &&
    Date.now() - new Date(cmd.finished_at).getTime() < 10 * 60000;
  var sending = cmd && cmd.state === "pending";
  var on = sending ? cmd.value.enabled : heater.enabled !== false;
  var row = el("div", "heater-sw" + (on ? "" : " off"));
  row.appendChild(el("span", "hs-name", heater.label, heater.entity_id || ""));
  var value = !heater.available ? "ei saatavilla"
    : heater.mode === "off" ? "pois" : heater.setpoint === null ? "–" : num.format(heater.setpoint) + " °C";
  row.appendChild(el("span", "hs-val", value, "Patterin oma asetus ja tila. Päivitetty " +
    (heater.source_ts ? new Date(heater.source_ts).toLocaleTimeString("fi-FI") : "ei tiedossa") + "."));
  var btn = el("button", "switch", null, heater.switchable
    ? "Pois kytketty patteri pidetään pois päältä. Turvalämpö käyttää sitä silti jäätymisen estoon."
    : "Mökin integraatio ei vielä tue pattereiden kytkemistä; päivitä se (install.sh).");
  btn.type = "button";
  btn.setAttribute("role", "switch");
  btn.setAttribute("aria-checked", on ? "true" : "false");
  btn.setAttribute("aria-label", heater.label + " käytössä");
  btn.disabled = !heater.switchable || sending;
  btn.appendChild(el("span", "track"));
  btn.appendChild(el("span", "", on ? "Päällä" : "Pois"));
  row.appendChild(btn);
  var msg = el("span", "hs-msg", sending ? "Lähetetään mökille…" : "");
  if (cmd && recent && (cmd.state === "failed" || cmd.state === "unknown")) {
    msg.className = "hs-msg err";
    msg.textContent = "Kytkentä ei onnistunut.";
    msg.title = cmd.detail || "";
  }
  row.appendChild(msg);
  btn.addEventListener("click", function () {
    var want = btn.getAttribute("aria-checked") !== "true";
    btn.disabled = true;
    btn.setAttribute("aria-checked", want ? "true" : "false");
    btn.lastChild.textContent = want ? "Päällä" : "Pois";
    msg.className = "hs-msg";
    msg.textContent = "Lähetetään mökille…";
    fetch("api/cottage/heater", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: heater.device_id, enabled: want }) })
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (res) {
        if (!res[0] || res[1].state === "failed") {
          msg.className = "hs-msg err";
          msg.textContent = res[0] ? "Kytkentä ei onnistunut." : (res[1].detail || "Kytkentä ei onnistunut.");
          msg.title = res[1].detail || "";
        }
        if (onChange) onChange();
      }).catch(function () {
        msg.className = "hs-msg err";
        msg.textContent = "Yhteys palveluun katkesi.";
      });
  });
  return row;
}

(function () {
  "use strict";
  var COTTAGE_MODES = { follow: "Seuraa anturia", thermostat: "Patterien termostaatti",
                        fallback: "Turvalämpö", manual: "Käsiohjaus" };
  var MODE_HELP = {
    follow: "Mökin ohjain pitää huoneen tavoitelämpötilassa huoneanturin mukaan.",
    thermostat: "Huoneanturin lukema puuttuu tai on vanhentunut; patterit toimivat omalla termostaatillaan tavoitteeseen.",
    fallback: "Villa Kuppiksen tavoite puuttuu tai on vanhentunut; patterit ovat turvalämmöllä.",
    manual: "Joku muutti pattereita käsin; ohjain odottaa, ettei sotke muutosta." };
  var HEATER_MODES = { heat: "Lämmitys", off: "Pois", auto: "Automaattinen",
                       heat_cool: "Lämmitys/jäähdytys", cool: "Jäähdytys" };
  var CONNECTION = { ok: ["ok", "ok"], stale: ["warn", "vanhentunut"],
                     offline: ["err", "ei yhteyttä"], auth_failed: ["err", "tunnus hylätty"] };
  var STATE_TEXT = { ok: ["", ""],
    stale: ["warn", "Tiedot vanhentuneet"], unavailable: ["err", "Laite ei vastaa"],
    missing: ["err", "Laite puuttuu Home Assistantista"], unknown: ["info", "Ei vielä tietoja"],
    unbound: ["info", "Laitteita ei ole liitetty"] };
  var STATE_HELP = {
    stale: "Mökiltä ei ole saatu tuoretta lukemaa vähään aikaan; näytetyt arvot voivat olla vanhoja.",
    unavailable: "Home Assistant ilmoittaa laitteen tilaksi ei saatavilla.",
    missing: "Liitetty laite ei löydy Home Assistantista; tarkista liitos (python -m villa_kuppis.sites).",
    unknown: "Yhteys on juuri muodostettu eikä laite ole vielä ilmoittanut arvoaan.",
    unbound: "Huoneelle ei ole liitetty laitteita." };
  var $section = document.getElementById("cottage-section");
  var $conn = document.getElementById("cottage-conn");
  var $note = document.getElementById("cottage-note");
  var $rooms = document.getElementById("cottage-rooms");
  var AUTOMATION_ON = true;
  var ROOM_NAMES = { large: "Mökki", small: "Kammari" };
  var ACK_LATE_MS = 45 * 60000;        // targets go out every quarter hour
  var PAGE_STARTED = Date.now();
  var num = new Intl.NumberFormat("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var clock = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki",
    hour: "2-digit", minute: "2-digit" });
  function temp(v) { return v === null || v === undefined ? "–" : num.format(v) + " °C"; }
  function when(iso) {
    if (!iso) return "ei tiedossa";
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "juuri nyt";
    if (s < 3600) return Math.round(s / 60) + " min sitten";
    if (s < 86400) return Math.round(s / 3600) + " h sitten";
    return clock.format(new Date(iso));
  }
  function el(tag, cls, text, title) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    if (title) e.title = title;
    return e;
  }
  function row(label, valueText, ok, title, labelTitle) {
    var r = el("div", "row");
    r.appendChild(el("span", "label", label, labelTitle));
    r.appendChild(el("span", "value" + (ok ? "" : " old"), valueText, title));
    return r;
  }
  function sensorChip(label, s, fmt, help) {
    if (!s) return null;
    var ok = s.status === "ok";
    var chip = el("span", ok ? "" : "old", label, (help ? help + " " : "") + "Päivitetty " +
      when(s.source_ts) + (ok ? "" : " (" + (STATE_TEXT[s.status] || ["", s.status])[1] + ")"));
    chip.appendChild(el("b", "", s.value === null ? "–" : fmt(s.value)));
    return chip;
  }
  function card(room) {
    var st = room.status;
    var offline = room.state === "missing" || room.state === "unavailable";
    var c = el("article", "card" + (room.state === "ok" ? "" : (offline ? " offline" : " stale")));
    var head = el("header", "card-head");
    var ttl = el("div");
    ttl.appendChild(el("h3", "", room.name));
    var mode = st && st.mode ? COTTAGE_MODES[st.mode] || st.mode : null;
    if (mode) {
      var sub = el("span", "sub", null, "Mökin ohjaimen toimintatila. " + (st.reason || "") +
        (MODE_HELP[st.mode] ? (st.reason ? " – " : "") + MODE_HELP[st.mode] : ""));
      sub.innerHTML = vkIcon("flame");
      sub.appendChild(document.createTextNode(mode));
      ttl.appendChild(sub);
    }
    head.appendChild(ttl);
    var badge = STATE_TEXT[room.state];
    if (badge && badge[1]) head.appendChild(el("span", "badge " + badge[0], badge[1], STATE_HELP[room.state]));
    c.appendChild(head);
    // Reading in a gauge (the dot is the target in force), target and mode beside it.
    var temp0 = room.temperature;
    var main = el("div", "cot-main");
    var g = el("div", "gauge", null, "ALPSTUGA-anturin mittaama lämpötila. Se ei ole turvatermostaatti." +
      (temp0 ? " Päivitetty " + when(temp0.source_ts) + "." : ""));
    var tv = temp0 && temp0.value !== null ? temp0.value : null;
    g.innerHTML = vkGauge(tv, st && st.target !== null ? st.target : null,
      temp0 && temp0.status === "ok" ? "var(--heat)" : "var(--ctl)", 4, 26) +
      '<div class="gauge-num"><b>' + (tv === null ? "–" : num.format(tv) + "<small>°C</small>") +
      "</b><span>huone</span></div>";
    main.appendChild(g);
    var side = el("div", "cot-side");
    var plan = room.plan;
    if (st) {
      var ok = st.status === "ok";
      // One target: the one in force, why, and the planned one only when it differs.
      var f1 = el("div", "fld");
      f1.appendChild(el("span", "fld-l", "Tavoite", "Lämpötila, johon mökin ohjain pyrkii juuri nyt."));
      var tip = [];
      if (plan) tip.push(plan.price === null ? "Jakson hintaa ei ole tiedossa." :
        "Jakson pörssihinta " + num.format(plan.price) + " snt/kWh" + (plan.vat ? " (sis. ALV)." : " (ilman ALV:ta)."));
      if (plan && plan.acked_at) tip.push("Mökki vahvisti tavoitteen klo " + clock.format(new Date(plan.acked_at)) + ".");
      if (st.valid_until && st.mode !== "fallback" && st.mode !== "manual")
        tip.push("Voimassa klo " + clock.format(new Date(st.valid_until)) + " asti.");
      if (!AUTOMATION_ON) tip.push("Automaatio on pois päältä, joten tavoitetta ei lähetetä mökille.");
      f1.appendChild(el("b", "value" + (ok ? "" : " old"), st.target === null ? "–" : temp(st.target), tip.join(" ")));
      if (plan && plan.reason_text) f1.appendChild(el("span", "fld-l", plan.reason_text));
      if (plan && AUTOMATION_ON && st.target !== null && Math.abs(plan.target - st.target) >= 0.05)
        f1.appendChild(el("span", "fld-l", "→ " + temp(plan.target),
          "Suunniteltu tavoite tälle jaksolle; mökki saa sen seuraavalla lähetyksellä."));
      side.appendChild(f1);
    }
    main.appendChild(side);
    c.appendChild(main);
    var stats = el("div", "cot-stats");
    [sensorChip("Kosteus", room.humidity, function (v) { return num.format(v) + " %"; }),
     sensorChip("CO₂", room.co2, function (v) { return Math.round(v) + " ppm"; }),
     sensorChip("PM2,5", room.pm25, function (v) { return num.format(v) + " µg/m³"; }, "Hiukkaset PM2,5.")]
      .forEach(function (chip) { if (chip) stats.appendChild(chip); });
    if (stats.children.length) c.appendChild(stats);
    if (st && st.chosen_sensor) {
      // The room reads a sensor picked in Laitteet; one tap returns the package's own.
      var pick = el("div", "heater-sw");
      pick.appendChild(el("span", "hs-name", "Huoneanturi: " + (temp0 ? temp0.label : st.chosen_sensor),
        "Valittu Laitteet-osiossa anturin tiedoista."));
      var back = el("button", "", "Palauta oletus", "Palauttaa mökin asetuksissa nimetyn anturin.");
      back.type = "button";
      back.addEventListener("click", function () {
        back.disabled = true;
        fetch("api/cottage/room_sensor", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ room: room.id, entity_id: null }) }).then(function () { load(); });
      });
      pick.appendChild(el("span"));
      pick.appendChild(back);
      c.appendChild(pick);
    }
    if (st) {
      // Only a late acknowledgement is worth a line of its own.
      var acked = plan && plan.acked_at ? new Date(plan.acked_at).getTime() : null;
      if (plan && AUTOMATION_ON && room.state === "ok" && (acked === null ?
          Date.now() - PAGE_STARTED > ACK_LATE_MS : Date.now() - acked > ACK_LATE_MS))
        c.appendChild(row("Mökki ei ole vahvistanut tavoitetta", acked === null ? "ei vielä" : when(plan.acked_at), false,
          "Mökin ohjain vahvistaa jokaisen Villa Kuppiksen lähettämän tavoitteen, yleensä vartin välein. " +
          "Jos vahvistuksia ei tule, mökki seuraa viimeisintä saamaansa suunnitelmaa ja sen jälkeen turvalämpöä."));
      if (st.mode === "manual" && st.manual_until) {
        // One tap ends the pause; the room follows Villa Kuppis again.
        var resume = el("div", "heater-sw");
        resume.appendChild(el("span", "hs-name", "Käsiohjaus päättyy klo " +
          clock.format(new Date(st.manual_until)), st.manual_by ? "Muutettu: " + st.manual_by : ""));
        var rbtn = el("button", "", "Palauta automaatio",
          "Lopettaa käsiohjauksen heti; mökin ohjain jatkaa Villa Kuppiksen tavoitteella.");
        rbtn.type = "button";
        rbtn.addEventListener("click", function () {
          rbtn.disabled = true;
          fetch("api/cottage/clear_manual", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ room: room.id }) }).then(function () { load(); },
            function () { rbtn.disabled = false; });
        });
        resume.appendChild(el("span"));
        resume.appendChild(rbtn);
        c.appendChild(resume);
      }
    } else {
      c.appendChild(el("p", "detail", "Huoneen tilatietoa ei ole liitetty."));
    }
    room.heaters.forEach(function (hh) {
      if (hh.switchable) { c.appendChild(vkHeaterSwitch(hh, load)); return; }
      var ok = hh.status === "ok";
      var text = !hh.available ? "ei saatavilla" : temp(hh.setpoint) +
        (hh.mode && hh.mode !== "heat" ? " (" + (HEATER_MODES[hh.mode] || hh.mode) + ")" : "");
      var line = el("div", "heater");
      line.appendChild(el("span", "", hh.label, "Patterin asetettu lämpötila"));
      line.appendChild(el("span", "value" + (ok ? "" : " old"), text,
        "Päivitetty " + when(hh.source_ts)));
      c.appendChild(line);
    });
    return c;
  }
  // The cottage's own alarm only shows here when it needs a look; Asetukset
  // tells whether it is ready.
  var HEARD_LATE_MS = 20 * 60000;      // heartbeats every 5 min, seen to 5 min
  function alarmNote(data) {
    var a = data.alarm;
    if (!a || !a.enabled || data.site.connection !== "ok") return "";
    if (!a.phones.length)
      return "Mökin oma hälytys ei tavoita ketään: yhdistä puhelin mökin Home Assistantiin.";
    if (a.heard && Date.now() - new Date(a.heard).getTime() < HEARD_LATE_MS) return "";
    return "Mökki ei ole kuullut kodin palvelinta" + (a.heard ? " klo " + clock.format(new Date(a.heard)) +
      " jälkeen" : "") + ": jos tämä jatkuu tunnin, mökki ilmoittaa puhelimiin, " +
      "että Villa Kuppis ei vastaa.";
  }
  function render(data) {
    if (!data.configured) { $section.hidden = true; return; }
    $section.hidden = false;
    AUTOMATION_ON = data.automation_on !== false;
    window.vkCottageData = data;
    var conn = CONNECTION[data.site.connection] || CONNECTION.offline;
    $conn.className = "badge " + conn[0];
    $conn.textContent = "Yhteys mökkiin: " + conn[1];
    $conn.hidden = data.site.connection === "ok";     // the header already says ok
    $conn.title = data.site.error || "Viimeisin yhteys: " + when(data.site.last_contact);
    var missing = data.site.missing_bindings || [];
    $note.textContent = (AUTOMATION_ON ? "" : "Automaatio on pois päältä: mökille ei lähetetä " +
      "tavoitteita, ja patterit siirtyvät turvalämpöön noin minuutin kuluttua. ") +
      (missing.length ? "Home Assistantista puuttuu liitettyjä laitteita: " +
      missing.join(", ") + ". " : "") + alarmNote(data);
    $rooms.textContent = "";
    data.rooms.forEach(function (room) { $rooms.appendChild(card(room)); });
    if (typeof setExtraHistUnits === "function")
      setExtraHistUnits(data.rooms.map(function (r) {
        return { id: "cottage:" + r.id, name: r.name }; }));     // the Mökki page only
  }
  // --- visit card (card F4.3): the form is built once so refreshes never reset it ---
  var $vState = document.getElementById("visit-state");
  var $vMsg = document.getElementById("visit-msg");
  var $vArrival = document.getElementById("visit-arrival");
  var $vDeparture = document.getElementById("visit-departure");
  var $vTarget = document.getElementById("visit-target");
  var $vStart = document.getElementById("visit-start");
  var $vEnd = document.getElementById("visit-end");
  var VISIT_ROOMS = ["large", "small"];
  var shownVisit = null;     // the visit values last put in the form
  var clock = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki",
    hour: "2-digit", minute: "2-digit" });
  var VISIT = null;
  var stamp = new Intl.DateTimeFormat("fi-FI", { timeZone: "Europe/Helsinki", weekday: "short",
    day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
  var HEAT_HELP = "Lämmitykseen oletetaan 12 tuntia, kunnes huoneiden lämpenemisnopeudet on opittu. " +
    "Aloitus valitaan edullisimmaksi muutaman tunnin ikkunasta tunnettujen hintojen mukaan.";
  var PLAN_HELP = { cheapest: "Edullisin aloitus hintojen mukaan.",
    fixed: "Aloitus 12 tuntia ennen saapumista (hintoja ei ollut koko ikkunalle).",
    started: "Lämmitys on jo aloitettu." };
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  // A "datetime-local" value is Helsinki wall-clock time; return an ISO instant.
  function helsinkiOffsetMs(ms) {
    var parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Helsinki", hourCycle: "h23",
      year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric"
    }).formatToParts(new Date(ms));
    var v = {};
    parts.forEach(function (p) { v[p.type] = parseInt(p.value, 10); });
    return Date.UTC(v.year, v.month - 1, v.day, v.hour, v.minute) - Math.floor(ms / 60000) * 60000;
  }
  function helsinkiToIso(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(text || "");
    if (!m) return null;
    var guess = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    var utc = guess - helsinkiOffsetMs(guess);
    utc = guess - helsinkiOffsetMs(utc);
    return new Date(utc).toISOString();
  }
  // An ISO instant as a "datetime-local" value in Helsinki time.
  function isoToHelsinki(iso) {
    if (!iso) return "";
    var v = {};
    new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Helsinki", hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
    }).formatToParts(new Date(iso)).forEach(function (p) { v[p.type] = p.value; });
    return v.year + "-" + v.month + "-" + v.day + "T" + v.hour + ":" + v.minute;
  }
  function checkedRooms() {
    return VISIT_ROOMS.filter(function (r) { return document.getElementById("visit-" + r).checked; });
  }
  // During a visit the form shows it; a field being edited is left alone.
  function fillForm(v) {
    var key = JSON.stringify([v.arrival, v.departure, v.target, v.rooms]);
    if (key === shownVisit) return;
    shownVisit = key;
    var focused = document.activeElement;
    if (focused !== $vArrival) $vArrival.value = isoToHelsinki(v.arrival);
    if (focused !== $vDeparture) $vDeparture.value = isoToHelsinki(v.departure);
    if (focused !== $vTarget) $vTarget.value = v.target;
    VISIT_ROOMS.forEach(function (r) {
      document.getElementById("visit-" + r).checked = v.rooms.indexOf(r) >= 0; });
  }
  function renderVisit() {
    $vState.textContent = "";
    var v = VISIT && VISIT.visit, plan = VISIT && VISIT.plan;
    $vStart.hidden = !!v;
    $vEnd.hidden = !v;
    if (!v) { shownVisit = null; return; }
    fillForm(v);
    if (plan) {
      var started = plan.reason === "started" || new Date(plan.start).getTime() <= Date.now();
      $vState.appendChild(el("p", "visit-line",
        (started ? "Lämmitys alkoi " : "Lämmitys alkaa ") + stamp.format(new Date(plan.start)),
        HEAT_HELP + " " + (PLAN_HELP[plan.reason] || "") + " Vierailun aloittanut: " + v.created_by + "."));
    }
    var progress = VISIT.progress || {};
    v.rooms.forEach(function (r) {
      var p = progress[r];
      if (!p) return;
      var text = ROOM_NAMES[r] + ": " + (p.fresh ? temp(p.value) : "lukema vanhentunut");
      if (p.reached) text += " · Tavoite saavutettu";
      else {
        if (p.rate !== null && p.rate !== undefined && p.rate > 0)
          text += " · lämpenee " + num.format(p.rate) + " °C/h";
        if (p.eta && !p.late) text += " · tavoitteessa n. klo " + clock.format(new Date(p.eta));
        if (p.late) text += " · ei ehdi ajoissa";
      }
      $vState.appendChild(el("p", "visit-line" + (p.late && !p.reached ? " late" : ""), text,
        "Tavoite katsotaan saavutetuksi, kun huoneanturin tuore lukema on enintään 0,5 °C alle tavoitteen. " +
        "Arvio perustuu lämpenemiseen lämmityksen alusta, enintään viimeiseen 75 minuuttiin."));
    });
  }
  var msgTimer = null;
  function visitCall(url, body, done) {
    $vMsg.textContent = "";
    clearTimeout(msgTimer);
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { return [r.ok, j]; }); })
      .then(function (res) {
        if (!res[0]) {
          $vMsg.textContent = res[1].detail || "Pyyntö epäonnistui.";
          shownVisit = null;           // show the visit as it still is
          renderVisit();
          return;
        }
        VISIT = window.vkVisit = res[1];
        renderVisit();
        if (done) {
          $vMsg.textContent = done;
          msgTimer = setTimeout(function () { $vMsg.textContent = ""; }, 3000);
        }
        load();                        // the room cards show the new targets at once
      }).catch(function () { $vMsg.textContent = "Yhteys palveluun katkesi."; });
  }
  $vStart.addEventListener("click", function () {
    var arrival = helsinkiToIso($vArrival.value);
    if (!arrival) { $vMsg.textContent = "Valitse saapumisaika."; return; }
    var departure = $vDeparture.value ? helsinkiToIso($vDeparture.value) : null;
    visitCall("api/visit", { arrival: arrival, target: parseFloat($vTarget.value),
      rooms: checkedRooms(), departure: departure });
  });
  $vEnd.addEventListener("click", function () { visitCall("api/visit/end", {}); });
  // During a visit a changed field saves itself, once the typing stops.
  var editTimer = null;
  function visitEdit(read) {
    return function () {
      if (!(VISIT && VISIT.visit)) return;
      clearTimeout(editTimer);
      editTimer = setTimeout(function () {
        var body = read();
        if (body) visitCall("api/visit/update", body, "Tallennettu.");
      }, 800);
    };
  }
  $vArrival.addEventListener("change", visitEdit(function () {
    var arrival = helsinkiToIso($vArrival.value);
    return arrival ? { arrival: arrival } : null; }));
  $vDeparture.addEventListener("change", visitEdit(function () {
    return { departure: $vDeparture.value ? helsinkiToIso($vDeparture.value) : null }; }));
  $vTarget.addEventListener("change", visitEdit(function () {
    var target = parseFloat($vTarget.value);
    return isNaN(target) ? null : { target: target }; }));
  VISIT_ROOMS.forEach(function (r) {
    document.getElementById("visit-" + r).addEventListener("change",
      visitEdit(function () { return { rooms: checkedRooms() }; }));
  });
  function loadVisit() {
    fetch("api/visit").then(function (r) { return r.json(); })
      .then(function (j) { VISIT = window.vkVisit = j; renderVisit(); }).catch(function () {});
  }
  function load() {
    fetch("api/cottage").then(function (r) { return r.json(); })
      .then(function (d) { render(d); if (d.configured) loadVisit(); }).catch(function () {});
  }
  window.loadCottage = load;
  load();
  vkEvery(load, 10000);
})();
"""
