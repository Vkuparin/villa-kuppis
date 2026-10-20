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
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .credential_file import read_token_file
from .ha_gateway import (DEFAULT_ENTITY_STALE_AFTER, Binding, BindingStore,
                         HomeAssistantGateway)
from .pump_service import (DB_FILENAME, HISTORY_INTERVAL_SECONDS, _iso,
                           _to_epoch)

SITE_ID = "cottage"
SITE_LABEL = "Mökki"
SITES_DB = "sites.db"
ROOMS = {"large": "Iso huone", "small": "Pieni huone"}
SENSOR_ROLES = ("temperature", "humidity", "co2", "pm25")
_ROLE_BY_CLASS = {"temperature": "temperature", "humidity": "humidity",
                  "carbon_dioxide": "co2", "pm25": "pm25"}
ACTOR_CONTROLLER = "Mökin ohjain"
ACTOR_MANUAL = "käsin"
DEFAULT_SAMPLE_INTERVAL = 15.0
# Worst first: the state shown for a room is its worst reading.
_STATUS_ORDER = ("missing", "unavailable", "stale", "unknown", "ok")
_CONNECTION = {"ok": "ok", "stale": "stale", "auth_failed": "auth_failed"}
MAX_ERROR_LENGTH = 200
MODES = ("follow", "thermostat", "fallback", "manual")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- site configuration ------------------------------------------------------------

@dataclass(frozen=True)
class SiteConfig:
    url: str
    token_file: Path


def parse_sites(raw) -> tuple:
    """Validate the ``sites`` config field -> ((site_id, SiteConfig), ...).

    Error messages never include the configured values.
    """
    if not isinstance(raw, dict) or set(raw) - {SITE_ID}:
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
                       sample_interval=sample_interval, on_close=store.close)


# --- roles ---------------------------------------------------------------------------

def status_entity(room: str) -> str:
    return f"sensor.villa_kuppis_{room}_target"


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

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts_utc, site_id, room, actor, action, new_value, detail "
                "FROM site_event ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": f"{r[2]}-{r[0]}", "ts": r[1], "site": r[2],
                 "unit": f"{r[2]}:{r[3]}",
                 "unit_name": f"{SITE_LABEL}: {ROOMS.get(r[3], r[3])}",
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


class CottageSite:
    """The cottage's gateway, readings, history and events for PumpService.

    ``gateway`` offers ``start()``, ``stop()``, ``snapshot()`` and ``health()``
    (``HomeAssistantGateway`` or ``FakeSiteGateway``); ``bindings`` returns the
    site's current ``Binding`` list.
    """

    def __init__(self, data_dir, gateway, bindings: Callable[[], list], *,
                 now=utc_now, sample_interval: float = DEFAULT_SAMPLE_INTERVAL,
                 on_close: Callable[[], None] | None = None):
        self.site_id = SITE_ID
        self._gateway = gateway
        self._bindings = bindings
        self._now = now
        self._sample_interval = sample_interval
        self._on_close = on_close
        self._store = CottageStore(Path(data_dir) / DB_FILENAME)
        self._known: frozenset = frozenset()
        self._roles: dict[str, str] = {}   # device id -> role seen with a reading
        self._sample_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle --

    def start(self) -> None:
        if self._thread is not None:
            return
        self._gateway.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cottage-sampler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        try:
            self._gateway.stop()
        finally:
            self._store.close()
            if self._on_close is not None:
                self._on_close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as error:
                logging.error("Cottage sampling failed: %s", type(error).__name__)
            self._stop.wait(self._sample_interval)

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

    def sample(self) -> int:
        """Record every fresh reading once; returns the rows written."""
        with self._sample_lock:
            now = int(self._now().timestamp())
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
        for room_id, name in ROOMS.items():
            room = {"id": room_id, "name": name, "heaters": [], "status": None,
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
                    room["heaters"].append({
                        **base, "available": bool(state.get("available")),
                        "setpoint": _num(state.get("setpoint")),
                        "mode": state.get("mode")})
                elif role == "status" and room["status"] is None:
                    attrs = state.get("attributes") or {}
                    room["status"] = {
                        **base, "target": _num(state.get("value")),
                        **{key: attrs.get(key) for key in (
                            "mode", "reason", "drive", "revision", "requested_target",
                            "valid_until", "manual_until", "manual_by")}}
            room["state"] = _worst(statuses) if statuses else "unbound"
            rooms.append(room)
        return {"configured": True, "site": self.health(), "rooms": rooms}

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
            "Iso huoneen tila", "large"),
    Binding(SITE_ID, "small-temperature", "sensor.small_alpstuga_temperature", "sensor",
            "ALPSTUGA", "small"),
    Binding(SITE_ID, "small-heater-1", "climate.small_mill_1", "climate",
            "Mill 1050 W", "small"),
    Binding(SITE_ID, "small-status", status_entity("small"), "sensor",
            "Pienen huoneen tila", "small"),
)


class FakeSiteGateway:
    """Offline stand-in for the cottage gateway: same read interface, no network.

    Large room follows the sensor, the small room is in fallback; values drift
    slowly with the clock.
    """

    def __init__(self, *, now=utc_now):
        self._now = now
        self._started = False

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
        out = {}
        for binding in DEMO_BINDINGS:
            state = {"device_id": binding.device_id, "site_id": SITE_ID,
                     "entity_id": binding.entity_id, "kind": binding.kind,
                     "label": binding.label, "available": True, "source_ts": ts,
                     "caps": {}, "status": "ok", "pending": None}
            if binding.kind == "climate":
                boost = binding.room == "large" and large_temp < 19.0
                state.update({
                    "power": True, "mode": "heat", "fan": None, "room_temp": None,
                    "setpoint": 22.0 if boost else (19.0 if binding.room == "large" else 7.0)})
            elif binding.device_id in status:
                target, attrs = status[binding.device_id]
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
    <h2>Mökki <span id="cottage-conn" class="badge info"></span></h2>
    <p id="cottage-note" class="detail"></p>
    <div id="cottage-rooms" class="cottage-rooms"></div>
  </section>
"""

STYLE = r"""
section.cottage { background: var(--surface); border: 1px solid var(--line);
                  border-radius: .75rem; padding: 1rem 1.1rem; margin-bottom: 1rem; }
section.cottage[hidden] { display: none; }
section.cottage h2 { margin: 0 0 .6rem; font-size: 1.15rem; }
.cottage-rooms { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
                 gap: 1rem; }
.cottage-rooms .card.offline { border-color: var(--err); }
.cottage-rooms .heater { display: flex; justify-content: space-between; gap: .6rem;
                         padding: .15rem 0; font-size: .92rem; color: var(--text2); }
.cottage-rooms .value.old { color: var(--text3); text-decoration: line-through; }
"""

SCRIPT = r"""
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
  function sensorRow(label, s, fmt, help) {
    if (!s) return null;
    var ok = s.status === "ok";
    return row(label, s.value === null ? "–" : fmt(s.value), ok,
      "Päivitetty " + when(s.source_ts) + (ok ? "" : " (" + (STATE_TEXT[s.status] || ["", s.status])[1] + ")"),
      help);
  }
  function card(room) {
    var st = room.status;
    var offline = room.state === "missing" || room.state === "unavailable";
    var c = el("article", "card" + (room.state === "ok" ? "" : (offline ? " offline" : " stale")));
    var head = el("header", "card-head");
    head.appendChild(el("h3", "", room.name));
    var badge = STATE_TEXT[room.state];
    if (badge && badge[1]) head.appendChild(el("span", "badge " + badge[0], badge[1], STATE_HELP[room.state]));
    c.appendChild(head);
    var t = sensorRow("Huonelämpötila", room.temperature, temp,
      "ALPSTUGA-anturin mittaama lämpötila. Se ei ole turvatermostaatti.");
    if (t) c.appendChild(t);
    var h = sensorRow("Ilmankosteus", room.humidity, function (v) { return num.format(v) + " %"; });
    if (h) c.appendChild(h);
    var co2 = sensorRow("CO₂", room.co2, function (v) { return Math.round(v) + " ppm"; });
    if (co2) c.appendChild(co2);
    var pm = sensorRow("Hiukkaset PM2,5", room.pm25, function (v) { return num.format(v) + " µg/m³"; });
    if (pm) c.appendChild(pm);
    if (st) {
      var ok = st.status === "ok";
      var mode = st.mode ? COTTAGE_MODES[st.mode] || st.mode : "–";
      c.appendChild(row("Voimassa oleva tavoite", st.target === null ? "–" : temp(st.target), ok,
        st.reason || "", "Lämpötila, johon mökin ohjain pyrkii juuri nyt."));
      c.appendChild(row("Tila", mode, ok, (st.reason || "") + (st.mode && MODE_HELP[st.mode] ?
        (st.reason ? " – " : "") + MODE_HELP[st.mode] : ""), "Mökin ohjaimen toimintatila."));
      if (st.mode === "manual" && st.manual_until)
        c.appendChild(el("p", "detail", "Käsiohjaus päättyy klo " + clock.format(new Date(st.manual_until)) + "."));
      else if (st.valid_until && st.mode !== "fallback")
        c.appendChild(el("p", "detail", "Villa Kuppiksen tavoite voimassa klo " + clock.format(new Date(st.valid_until)) + " asti."));
    } else {
      c.appendChild(el("p", "detail", "Huoneen tilatietoa ei ole liitetty."));
    }
    room.heaters.forEach(function (hh) {
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
  function render(data) {
    if (!data.configured) { $section.hidden = true; return; }
    $section.hidden = false;
    var conn = CONNECTION[data.site.connection] || CONNECTION.offline;
    $conn.className = "badge " + conn[0];
    $conn.textContent = "Yhteys mökkiin: " + conn[1];
    $conn.title = data.site.error || "Viimeisin yhteys: " + when(data.site.last_contact);
    var missing = data.site.missing_bindings || [];
    $note.textContent = missing.length ? "Home Assistantista puuttuu liitettyjä laitteita: " +
      missing.join(", ") + "." : "";
    $rooms.textContent = "";
    data.rooms.forEach(function (room) { $rooms.appendChild(card(room)); });
    if (typeof setExtraHistUnits === "function")
      setExtraHistUnits(data.rooms.map(function (r) {
        return { id: "cottage:" + r.id, name: "Mökki: " + r.name }; }));
  }
  function load() {
    fetch("/api/cottage").then(function (r) { return r.json(); })
      .then(render).catch(function () {});
  }
  load();
  setInterval(load, 10000);
})();
"""
