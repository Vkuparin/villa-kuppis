"""PC pump service: state, commands and the event log for the home pumps.

Card F2.3. The service keeps the cloud-reported state of every air-to-air
unit in SQLite with a timestamp and a fresh/stale flag per reading, and a
separate pending slot per unit until the device confirms a requested
change. The device-reported state and the requested change are shown
separately, per the UI design (docs/ui-fi.md).

Commands go through the same supervised pipeline as the F2.1 probe: one
single-attempt HTTP write, then read-backs. A change is confirmed only
when a fresh read-back reports it; anything else is failed with a safe
detail message. Every command records who (or which plan) asked, the
unit, the requested value and its result in the SQLite event log.

The async MELCloud Home client runs in a background thread with its own
event loop; the synchronous API (view, request, events) is safe to call
from HTTP server threads. Live mode reads MELCloud Home credentials from
the environment (VILLA_KUPPIS_MELCLOUD_USERNAME /
VILLA_KUPPIS_MELCLOUD_PASSWORD) for development; the unattended runner
supplies them from Windows Credential Manager. Demo mode runs the same code against the
in-memory FakeClient, fully offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from aiomelcloudhome.models.ata import ATAFanSpeed, ATAOperationMode

from . import learning, weather
from .cooling import CoolingAutomation
from .service import SpotPriceService
from .settings import SettingsStore, vat_factor
from .pump_probe import (
    _default_client_factory,
    _operation_message,
    collect_units,
    find_unit_by_id,
)

DB_FILENAME = "pumps.db"
DEFAULT_POLL_INTERVAL = 60.0
# Through Home Assistant a read only looks at the gateway's live snapshot.
HA_POLL_INTERVAL = 10.0
DEFAULT_STALE_AFTER = 180.0
MAX_POLL_BACKOFF = 900.0     # longest wait between cloud reads after failures
PRICE_FETCH_MIN_SECONDS = 60.0   # between manual price fetches
DEFAULT_READBACK_POLLS = 3
DEFAULT_READBACK_DELAY = 5.0
DEFAULT_COMMAND_TIMEOUT = 30.0
MAX_ACTOR_LENGTH = 80
DEFAULT_ACTOR = "web"

MODES = ("Cool", "Heat", "Dry", "Fan", "Automatic")
FAN_NAMES = ("One", "Two", "Three", "Four", "Five")
ACTIONS = ("power", "setpoint", "mode", "fan")

# History (card F3.3): a history row is written every HISTORY_INTERVAL_SECONDS
# unless one of the five fields changed since the last row, and /api/history is
# downsampled to at most the per-range point budgets below (year stays ~1s).
HISTORY_INTERVAL_SECONDS = 300
COMMAND_MARK_LIMIT = 200
HELSINKI = ZoneInfo("Europe/Helsinki")
# How far past now the history window may reach (the FMI forecast is 48 h).
HISTORY_AHEAD = 48 * 3600
HISTORY_RANGES = {
    "day": (24 * 3600, 288),
    "week": (7 * 24 * 3600, 700),
    "month": (30 * 24 * 3600, 900),
    "year": (365 * 24 * 3600, 1000),
}
# Outdoor temperature (card F3.3): one hourly FMI fetch per configured site,
# a one-time backfill at start, then a 75-minute overlap window every hour.
WEATHER_UPDATE_INTERVAL = 3600.0
WEATHER_BACKFILL_HOURS = 24.0
WEATHER_OVERLAP_HOURS = 1.25
WEATHER_RETRY_SECONDS = 300.0
PRICE_DB_FILENAME = "prices.db"
# Forecast (card F5.1): fetched with each hourly weather pass, 48 hours ahead.
FORECAST_HOURS = 48.0
# Learned room rates (card F5.1) are recomputed at most this often.
ROOM_RATES_CACHE_SECONDS = 600.0
ROOM_RATES_PREDICT_HOURS = 6


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_epoch(value) -> int:
    """datetime or ISO-8601 (UTC) string -> unix seconds (naive = UTC)."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _iso(epoch: int) -> str:
    """unix seconds -> ISO-8601 UTC string (the API's timestamp form)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# --- validation ------------------------------------------------------------------

def _round(value, digits):
    return None if value is None else round(value, digits)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bounds_for_mode(caps, mode):
    """(lo, hi) for a mode from a stored caps dict; (None, None) if unknown.

    This is the dict-shape counterpart of pump_probe._bounds_for_mode, which
    operates on the library's capabilities object.
    """
    if caps is None or mode is None:
        return None, None
    if mode == "Heat":
        return caps.get("min_heat"), caps.get("max_heat")
    if mode in ("Cool", "Dry"):
        return caps.get("min_cool"), caps.get("max_cool")
    if mode == "Automatic":
        return caps.get("min_auto"), caps.get("max_auto")
    return None, None


def validate_request(state: dict, action: str, value):
    """Validate one command against the latest stored state of the unit.

    Returns (ok, normalized_value, error_code, detail). The state dict is
    the shape produced by unit_state() (caps from the unit's capabilities).
    """
    caps = state.get("caps")

    if action == "power":
        if not isinstance(value, bool):
            return False, None, "bad_power", "value must be true or false"
        return True, value, None, ""

    if action == "setpoint":
        if not _is_number(value) or not math.isfinite(value):
            return False, None, "not_number", "value must be a number"
        mode = state.get("mode")
        if mode is None:
            return False, None, "no_mode", "the unit has not reported an operation mode"
        if mode == "Fan":
            return False, None, "fan_mode", "a setpoint is not available in fan mode"
        lo, hi = _bounds_for_mode(caps, mode)
        if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)):
            return False, None, "no_bounds", "the unit has not reported temperature bounds for this mode"
        if not lo <= value <= hi:
            return False, None, "out_of_bounds", f"value must be between {lo:g} and {hi:g}"
        if caps is not None:
            half = caps.get("half_step")
            if half is True and abs(value * 2.0 - round(value * 2.0)) > 1e-6:
                return False, None, "step", "the value is not on the unit's half-degree steps"
            if half is False and abs(value - round(value)) > 1e-6:
                return False, None, "step", "the value is not on the unit's one-degree steps"
        return True, float(value), None, ""

    if action == "mode":
        allowed = [
            m for m in MODES
            if not (caps is not None and caps.get("modes", {}).get(m) is False)
        ]
        if not isinstance(value, str) or value not in allowed:
            return False, None, "bad_mode", "the mode is not supported by this unit"
        return True, value, None, ""

    if action == "fan":
        if not isinstance(value, str):
            return False, None, "bad_fan", "value must be a fan speed name"
        if value == "Auto":
            if caps is not None and caps.get("auto_fan") is False:
                return False, None, "bad_fan", "this unit has no automatic fan speed"
            return True, value, None, ""
        if value in FAN_NAMES:
            index = FAN_NAMES.index(value) + 1
            speeds = caps.get("fan_speeds") if caps is not None else None
            if speeds is not None and index > speeds:
                return False, None, "bad_fan", "this unit has no fan speed that high"
            return True, value, None, ""
        return False, None, "bad_fan", "the fan speed is not supported by this unit"

    return False, None, "unknown_action", f"action must be one of {', '.join(ACTIONS)}"


def _control_kwargs(action: str, value) -> dict:
    if action == "power":
        return {"power": value}
    if action == "setpoint":
        return {"set_temperature": value}
    if action == "mode":
        return {"operation_mode": ATAOperationMode(value)}
    if action == "fan":
        return {"set_fan_speed": ATAFanSpeed(value)}
    raise ValueError(action)


def _matches(unit, action: str, value) -> bool:
    if action == "power":
        return unit.power is not None and unit.power == value
    if action == "setpoint":
        st = unit.set_temperature
        return st is not None and math.isfinite(st) and st == value
    if action == "mode":
        return unit.operation_mode is not None and unit.operation_mode.value == value
    if action == "fan":
        return unit.set_fan_speed is not None and unit.set_fan_speed.value == value
    return False


def unit_state(unit, now_iso: str) -> dict:
    """Normalize one library unit into the state row shape."""
    caps = None
    if unit.capabilities is not None:
        c = unit.capabilities
        caps = {
            "min_heat": c.min_temp_heat,
            "max_heat": c.max_temp_heat,
            "min_cool": c.min_temp_cool,
            "max_cool": c.max_temp_cool,
            "min_auto": c.min_temp_auto,
            "max_auto": c.max_temp_auto,
            "half_step": c.has_half_degree_increments,
            "fan_speeds": c.number_of_fan_speeds,
            "auto_fan": c.has_automatic_fan_speed,
            "modes": {
                "Cool": c.has_cool_operation_mode,
                "Heat": c.has_heat_operation_mode,
                "Dry": c.has_dry_operation_mode,
                "Fan": c.has_fan_operation_mode,
                "Automatic": c.has_auto_operation_mode,
            },
        }

    def finite(v):
        return v if (isinstance(v, float) and math.isfinite(v)) else None

    mode = unit.operation_mode.value if unit.operation_mode is not None else None
    fan = unit.set_fan_speed.value if unit.set_fan_speed is not None else None
    return {
        "id": unit.id,
        "name": unit.name or "(unnamed)",
        "power": unit.power,
        "mode": mode,
        "setpoint": finite(unit.set_temperature),
        "fan": fan,
        "room_temp": finite(unit.room_temperature),
        "in_error": unit.is_in_error,
        "caps": caps,
        "updated_at": now_iso,
    }


# --- store -----------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS unit_state (
    unit_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    power INTEGER,
    mode TEXT,
    setpoint REAL,
    fan TEXT,
    room_temp REAL,
    in_error INTEGER,
    caps_json TEXT,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    unit_name TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('power','setpoint','fan','mode')),
    requested TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('pending','confirmed','failed')),
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_event_id_desc ON event (id DESC);
CREATE TABLE IF NOT EXISTS pump_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc INTEGER NOT NULL,
    unit_id TEXT NOT NULL,
    power INTEGER,
    mode TEXT,
    setpoint REAL,
    fan TEXT,
    room_temp REAL
);
CREATE INDEX IF NOT EXISTS idx_pump_history_unit
    ON pump_history (unit_id, ts_utc);
CREATE TABLE IF NOT EXISTS outdoor_temperature (
    site TEXT NOT NULL,
    ts_utc INTEGER NOT NULL,
    temp_c REAL NOT NULL,
    PRIMARY KEY (site, ts_utc)
);
CREATE TABLE IF NOT EXISTS plan_target (
    unit_id TEXT NOT NULL,
    ts_utc INTEGER NOT NULL,
    target_c REAL NOT NULL,
    PRIMARY KEY (unit_id, ts_utc)
);
CREATE TABLE IF NOT EXISTS outdoor_forecast (
    site TEXT NOT NULL,
    ts_utc INTEGER NOT NULL,
    temp_c REAL NOT NULL,
    PRIMARY KEY (site, ts_utc)
);
CREATE TABLE IF NOT EXISTS pump_energy (
    unit_id TEXT NOT NULL,
    ts_utc INTEGER NOT NULL,
    kwh REAL NOT NULL,
    PRIMARY KEY (unit_id, ts_utc)
);
"""


class PumpStore:
    """SQLite state and event log. All methods are thread-safe (one lock)."""

    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    def upsert_units(self, states: list[dict]) -> None:
        with self._lock:
            for s in states:
                self._conn.execute(
                    """INSERT INTO unit_state (unit_id, name, power, mode, setpoint,
                                               fan, room_temp, in_error, caps_json,
                                               updated_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(unit_id) DO UPDATE SET
                         name=excluded.name,
                         power=excluded.power,
                         mode=excluded.mode,
                         setpoint=excluded.setpoint,
                         fan=excluded.fan,
                         room_temp=excluded.room_temp,
                         in_error=excluded.in_error,
                         caps_json=excluded.caps_json,
                         updated_at_utc=excluded.updated_at_utc""",
                    (
                        s["id"], s["name"], s["power"], s["mode"], s["setpoint"],
                        s["fan"], s["room_temp"], s["in_error"],
                        json.dumps(s["caps"]) if s["caps"] is not None else None,
                        s["updated_at"],
                    ),
                )
            self._conn.commit()

    def unit_state(self, unit_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM unit_state WHERE unit_id = ?", (unit_id,)
            ).fetchone()
        return self._row_to_state(row) if row is not None else None

    def unit_states(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM unit_state ORDER BY rowid"
            ).fetchall()
        return [self._row_to_state(r) for r in rows]

    @staticmethod
    def _row_to_state(row) -> dict:
        # SQLite stores booleans as 0/1; coerce them back on the way out.
        power = row["power"]
        in_error = row["in_error"]
        return {
            "id": row["unit_id"],
            "name": row["name"],
            "power": bool(power) if power is not None else None,
            "mode": row["mode"],
            "setpoint": row["setpoint"],
            "fan": row["fan"],
            "room_temp": row["room_temp"],
            "in_error": bool(in_error) if in_error is not None else None,
            "caps": json.loads(row["caps_json"]) if row["caps_json"] else None,
            "updated_at": row["updated_at_utc"],
        }

    def add_event(self, now_iso: str, unit_id: str, unit_name: str, actor: str,
                  action: str, requested) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO event (ts_utc, unit_id, unit_name, actor, action,
                                      requested, result, detail)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', '')""",
                (now_iso, unit_id, unit_name, actor, action, json.dumps(requested)),
            )
            self._conn.commit()
            rowid = cur.lastrowid
            return int(rowid) if rowid is not None else 0

    def finish_event(self, event_id: int, result: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE event SET result = ?, detail = ? WHERE id = ?",
                (result, detail, event_id),
            )
            self._conn.commit()

    def fail_orphaned_pending(self) -> int:
        """Fail every pending event.

        The command queue is in memory, so any event still pending when a
        service starts belongs to a previous process and can never be
        confirmed from here — even if the restart happened seconds after
        the crash.
        """
        with self._lock:
            cur = self._conn.execute(
                """UPDATE event SET result = 'failed',
                          detail = 'the service restarted before the change was confirmed'
                   WHERE result = 'pending'"""
            )
            self._conn.commit()
            return cur.rowcount

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM event ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": r["id"],
                "ts": r["ts_utc"],
                "unit": r["unit_id"],
                "unit_name": r["unit_name"],
                "actor": r["actor"],
                "action": r["action"],
                "requested": json.loads(r["requested"]),
                "result": r["result"],
                "detail": r["detail"],
            }
            for r in rows
        ]

    def set_away(self, away: bool, actor: str) -> dict:
        """Home-away button (card F6.1)."""
        self.automation.set_away(away, actor)
        return self.plan_view()

    # -- history (card F3.3) --------------------------------------------------

    def record_history(self, states: list[dict]) -> int:
        """Persist the on-change / 5-minute history; return rows written.

        A row is written when any of the five fields changed since the last
        row for that unit, or when the last row is at least
        HISTORY_INTERVAL_SECONDS old. One transaction per call.
        """
        written = 0
        with self._lock, self._conn:
            for s in states:
                last = self._conn.execute(
                    "SELECT ts_utc, power, mode, setpoint, fan, room_temp "
                    "FROM pump_history WHERE unit_id = ? ORDER BY ts_utc DESC, id DESC "
                    "LIMIT 1", (s["id"],)).fetchone()
                if last is not None:
                    changed = (
                        s["power"] != last[1] or s["mode"] != last[2]
                        or s["setpoint"] != last[3] or s["fan"] != last[4]
                        or s["room_temp"] != last[5]
                    )
                    if not changed:
                        try:
                            gap = _to_epoch(s["updated_at"]) - last[0]
                            if gap < HISTORY_INTERVAL_SECONDS:
                                continue
                        except (KeyError, ValueError):
                            pass  # unparseable timestamp: record it
                self._conn.execute(
                    "INSERT INTO pump_history (ts_utc, unit_id, power, mode, "
                    "setpoint, fan, room_temp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (_to_epoch(s["updated_at"]), s["id"], s["power"],
                     s["mode"], s["setpoint"], s["fan"], s["room_temp"]))
                written += 1
            self._conn.commit()
        return written

    @staticmethod
    def _bucket_seconds(start: int, end: int, max_points: int) -> float:
        return max(1, end - start) / max(1, max_points)

    def pump_history(self, unit_id: str, start, end,
                     max_points: int) -> list[tuple]:
        """Downsampled (ts, room_temp, setpoint) rows, last sample per bucket."""
        s, e = _to_epoch(start), _to_epoch(end)
        b = self._bucket_seconds(s, e, max_points)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, room_temp, setpoint FROM ("
                "  SELECT ts_utc, room_temp, setpoint, "
                "         ROW_NUMBER() OVER (PARTITION BY CAST((ts_utc - ?) / ? AS INTEGER) "
                "                            ORDER BY ts_utc DESC, id DESC) AS rn "
                "  FROM pump_history WHERE unit_id = ? AND ts_utc >= ? "
                "    AND ts_utc < ?"
                ") WHERE rn = 1 ORDER BY ts_utc", (s, b, unit_id, s, e)).fetchall()
        return [tuple(r) for r in rows]

    def outdoor_upsert(self, site: str, values: Sequence[tuple]) -> int:
        """Insert/refresh outdoor temperatures; return the row count."""
        rows = [(site, _to_epoch(ts), float(temp_c)) for ts, temp_c in values]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO outdoor_temperature (site, ts_utc, temp_c) "
                "VALUES (?, ?, ?) ON CONFLICT(site, ts_utc) DO UPDATE SET "
                "temp_c = excluded.temp_c", rows)
            self._conn.commit()
        return len(rows)

    def outdoor_series(self, site: str, start, end,
                       max_points: int) -> list[tuple]:
        """Downsampled (ts, temp_c) outdoor rows, last sample per bucket."""
        s, e = _to_epoch(start), _to_epoch(end)
        b = self._bucket_seconds(s, e, max_points)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, temp_c FROM ("
                "  SELECT ts_utc, temp_c, "
                "         ROW_NUMBER() OVER (PARTITION BY CAST((ts_utc - ?) / ? AS INTEGER) "
                "                            ORDER BY ts_utc DESC) AS rn "
                "  FROM outdoor_temperature WHERE site = ? AND ts_utc >= ? "
                "    AND ts_utc < ?"
                ") WHERE rn = 1 ORDER BY ts_utc", (s, b, site, s, e)).fetchall()
        return [tuple(r) for r in rows]

    def outdoor_rows(self, site: str, start, end) -> list[tuple]:
        """All (ts, temp_c) outdoor rows in the range, oldest first."""
        s, e = _to_epoch(start), _to_epoch(end)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, temp_c FROM outdoor_temperature WHERE site = ? "
                "AND ts_utc >= ? AND ts_utc < ? ORDER BY ts_utc",
                (site, s, e)).fetchall()
        return [tuple(r) for r in rows]

    # -- learning inputs and forecast (card F5.1) -----------------------------

    def forecast_upsert(self, site: str, values: Sequence[tuple]) -> int:
        """Store the latest forecast; a newer fetch replaces older values."""
        rows = [(site, _to_epoch(ts), float(temp_c)) for ts, temp_c in values]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO outdoor_forecast (site, ts_utc, temp_c) "
                "VALUES (?, ?, ?) ON CONFLICT(site, ts_utc) DO UPDATE SET "
                "temp_c = excluded.temp_c", rows)
            self._conn.commit()
        return len(rows)

    def forecast_rows(self, site: str, start, end) -> list[tuple]:
        """(ts, temp_c) forecast rows in the range, oldest first."""
        s, e = _to_epoch(start), _to_epoch(end)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, temp_c FROM outdoor_forecast WHERE site = ? "
                "AND ts_utc >= ? AND ts_utc < ? ORDER BY ts_utc",
                (site, s, e)).fetchall()
        return [tuple(r) for r in rows]

    def learning_readings(self, unit_id: str, start, end) -> list:
        """Every stored history row of a unit as learning.Reading."""
        s, e = _to_epoch(start), _to_epoch(end)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, room_temp, setpoint, power, mode "
                "FROM pump_history WHERE unit_id = ? AND ts_utc >= ? "
                "AND ts_utc < ? ORDER BY ts_utc, id", (unit_id, s, e)).fetchall()
        return [learning.Reading(r[0], r[1], r[2],
                                 None if r[3] is None else bool(r[3]), r[4])
                for r in rows]

    def price_series(self, prices_db, start, end,
                     max_points: int, factor: float = 1.0) -> list[tuple] | None:
        """Downsampled (ts, c/kWh) prices from prices.db (opened read-only).

        Returns None when prices.db does not exist (the API reports price
        null); otherwise a list, possibly empty.
        """
        path = Path(prices_db)
        if not path.exists():
            return None
        s, e = _to_epoch(start), _to_epoch(end)
        b = self._bucket_seconds(s, e, max_points)
        uri = path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute(
                "SELECT ts_utc, price_raw_eur_mwh FROM ("
                "  SELECT ts_utc, price_raw_eur_mwh, "
                "         ROW_NUMBER() OVER (PARTITION BY CAST((ts_utc - ?) / ? AS INTEGER) "
                "                            ORDER BY ts_utc DESC) AS rn "
                "  FROM price_interval WHERE ts_utc >= ? AND ts_utc < ?"
                ") WHERE rn = 1 ORDER BY ts_utc", (s, b, s, e)).fetchall()
        finally:
            conn.close()
        return [(ts, float(Decimal(raw) / Decimal(10)) * factor) for ts, raw in rows]

    # -- plan targets, commands, energy and cost (card F3.3) ------------------

    def plan_upsert(self, unit_id: str, values: Sequence[tuple]) -> int:
        """Insert/refresh plan targets as (ts, target_c) pairs; return rows."""
        rows = [(unit_id, _to_epoch(ts), float(t)) for ts, t in values]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO plan_target (unit_id, ts_utc, target_c) "
                "VALUES (?, ?, ?) ON CONFLICT(unit_id, ts_utc) DO UPDATE SET "
                "target_c = excluded.target_c", rows)
            self._conn.commit()
        return len(rows)

    def plan_series(self, unit_id: str, start, end,
                    max_points: int) -> list[tuple]:
        """Downsampled (ts, target_c) plan rows, last sample per bucket."""
        s, e = _to_epoch(start), _to_epoch(end)
        b = self._bucket_seconds(s, e, max_points)
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, target_c FROM ("
                "  SELECT ts_utc, target_c, "
                "         ROW_NUMBER() OVER (PARTITION BY CAST((ts_utc - ?) / ? AS INTEGER) "
                "                            ORDER BY ts_utc DESC) AS rn "
                "  FROM plan_target WHERE unit_id = ? AND ts_utc >= ? "
                "    AND ts_utc < ?"
                ") WHERE rn = 1 ORDER BY ts_utc", (s, b, unit_id, s, e)).fetchall()
        return [tuple(r) for r in rows]

    def command_marks(self, unit_id: str, start, end,
                      limit: int = COMMAND_MARK_LIMIT) -> list[dict]:
        """Commands of one unit in the range, oldest first (newest `limit`)."""
        s, e = _iso(_to_epoch(start)), _iso(_to_epoch(end))
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts_utc, actor, action, requested, result FROM event "
                "WHERE unit_id = ? AND ts_utc >= ? AND ts_utc < ? "
                "AND result != 'pending' ORDER BY ts_utc DESC, id DESC LIMIT ?",
                (unit_id, s, e, limit)).fetchall()
        return [{"ts": r["ts_utc"], "actor": r["actor"],
                 "action": r["action"], "requested": json.loads(r["requested"]),
                 "result": r["result"]} for r in reversed(rows)]

    def energy_upsert(self, unit_id: str, values: Sequence[tuple]) -> int:
        """Insert/refresh energy as (hour_start, kwh) pairs; return rows."""
        rows = [(unit_id, _to_epoch(ts), float(k)) for ts, k in values]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO pump_energy (unit_id, ts_utc, kwh) VALUES (?, ?, ?) "
                "ON CONFLICT(unit_id, ts_utc) DO UPDATE SET kwh = excluded.kwh",
                rows)
            self._conn.commit()
        return len(rows)

    def energy_daily(self, unit_id: str, prices_db, start, end,
                     factor: float = 1.0) -> list[dict]:
        """Energy and cost per Helsinki day; [] when no energy is stored.

        Each energy row covers the hour starting at its timestamp and is
        priced with the mean of the 15-minute spot prices in that hour.
        cost_eur is None when no hour of the day has a price; cost_complete
        is False when some hours lack one (cost then covers priced hours).
        """
        s, e = _to_epoch(start), _to_epoch(end)
        with self._lock:
            energy = self._conn.execute(
                "SELECT ts_utc, kwh FROM pump_energy WHERE unit_id = ? "
                "AND ts_utc >= ? AND ts_utc < ? ORDER BY ts_utc",
                (unit_id, s, e)).fetchall()
        if not energy:
            return []
        prices: dict[int, float] = {}
        path = Path(prices_db)
        if path.exists():
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                   uri=True)
            try:
                for ts, raw in conn.execute(
                        "SELECT ts_utc, price_raw_eur_mwh FROM price_interval "
                        "WHERE ts_utc >= ? AND ts_utc < ?", (s, e + 3600)):
                    prices[ts] = float(Decimal(raw) / Decimal(10)) * factor
            finally:
                conn.close()
        days: dict[str, dict] = {}
        for ts, kwh in energy:
            day = datetime.fromtimestamp(ts, tz=HELSINKI).date().isoformat()
            d = days.setdefault(day, {"date": day, "kwh": 0.0,
                                      "cost_eur": None, "cost_complete": True})
            d["kwh"] += kwh
            hour = [v for t, v in prices.items() if ts <= t < ts + 3600]
            if hour:
                d["cost_eur"] = (d["cost_eur"] or 0.0) + (
                    kwh * (sum(hour) / len(hour)) / 100)
            else:
                d["cost_complete"] = False
        for d in days.values():
            d["kwh"] = round(d["kwh"], 3)
            if d["cost_eur"] is not None:
                d["cost_eur"] = round(d["cost_eur"], 4)
        return list(days.values())

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- gateway (async client on its own loop) ---------------------------------------

class PumpGateway:
    """Runs the async MELCloud Home client in a background thread.

    A poll loop reads the context and upserts unit states; a worker consumes
    submitted commands. A pending slot is reserved at submit time so two
    concurrent commands for the same unit can never both enter the pipeline.
    """

    def __init__(self, client, store: PumpStore, *, now=utc_now,
                 poll_interval: float = DEFAULT_POLL_INTERVAL,
                 stale_after: float = DEFAULT_STALE_AFTER,
                 readback_polls: int = DEFAULT_READBACK_POLLS,
                 readback_delay: float = DEFAULT_READBACK_DELAY,
                 command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
                 teardown=None, client_factory=None):
        self._client = client
        self._client_factory = client_factory
        self._store = store
        self._now = now
        self._poll_interval = poll_interval
        self._stale_after = stale_after
        self._readback_polls = readback_polls
        self._readback_delay = readback_delay
        self._command_timeout = command_timeout
        self._teardown = teardown

        self._lock = threading.Lock()
        self._last_ok: datetime | None = None
        self._last_error: str | None = None
        self._pending: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Safe to create outside the loop (Python 3.10+); bound to the
        # gateway loop on first use.
        self._queue: asyncio.Queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._started = False

    # -- lifecycle --

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run_thread, name="pump-gateway", daemon=True
        )
        self._thread.start()

    def _run_thread(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        finally:
            self._loop.close()

    async def _run(self) -> None:
        if self._client_factory is not None:
            try:
                # aiohttp sessions must be created on their running owner loop.
                self._client, self._teardown = self._client_factory()
            except Exception as err:
                with self._lock:
                    self._last_error = _operation_message("cloud startup", err)
                await self._stop_event.wait()
                return
        poll_task = asyncio.create_task(self._poll_loop())
        worker_task = asyncio.create_task(self._worker())
        await self._stop_event.wait()
        poll_task.cancel()
        worker_task.cancel()
        await asyncio.gather(poll_task, worker_task, return_exceptions=True)
        if self._teardown is not None:
            try:
                await asyncio.wait_for(self._teardown(), timeout=self._command_timeout)
            except Exception:
                pass

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._request_stop(), loop
            ).result(timeout=30.0)
        else:
            # The loop has not started (or has ended) yet; the flag is
            # checked when _run begins.
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=30.0)
            self._thread = None

    async def _request_stop(self) -> None:
        assert self._stop_event is not None
        self._stop_event.set()

    # -- status --

    @property
    def running(self) -> bool:
        return self._started and self._client is not None

    def has_pending(self, unit_id: str) -> bool:
        with self._lock:
            return unit_id in self._pending

    def status(self) -> dict:
        now = self._now()
        with self._lock:
            last_ok = self._last_ok
            last_error = self._last_error
        if last_ok is None:
            state = "initializing" if last_error is None else "error"
        elif now - last_ok <= timedelta(seconds=self._stale_after):
            state = "ok"
        else:
            state = "stale"
        return {
            "state": state,
            "error": last_error,
            "last_ok": last_ok.isoformat() if last_ok is not None else None,
            "stale_after": self._stale_after,
        }

    def _pending_snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._pending.items()}

    # -- polling --

    def _next_poll_delay(self, failures: int) -> float:
        """Wait before the next cloud read: the normal interval after a
        success, doubled for each consecutive failure up to the cap."""
        if failures <= 0:
            return self._poll_interval
        return min(self._poll_interval * 2 ** failures,
                   max(MAX_POLL_BACKOFF, self._poll_interval))

    async def _poll_loop(self) -> None:
        failures = 0
        while True:
            try:
                context = await asyncio.wait_for(
                    self._client.get_context(), timeout=self._command_timeout
                )
                now_iso = self._now().isoformat()
                states = [unit_state(u, now_iso) for u in collect_units(context)]
                self._store.upsert_units(states)
                try:
                    self._store.record_history(states)
                except sqlite3.Error:
                    # A history write failure must not take polling down.
                    pass
                with self._lock:
                    self._last_ok = self._now()
                    self._last_error = None
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as err:
                failures += 1
                with self._lock:
                    self._last_error = _operation_message("cloud read", err)
            await asyncio.sleep(self._next_poll_delay(failures))

    # -- commands --

    def submit(self, event_id: int, unit_id: str, action: str, value,
               actor: str) -> str | None:
        """Reserve the pending slot and queue the command.

        Returns None when the command is queued, or the reason it was
        refused — "pending" (the unit already has a command in flight) or
        "stopped" (the gateway is not running). The reason is decided
        under the same lock as the reservation, so it stays accurate even
        if the slot clears right afterwards; callers must act on the
        reason instead of re-guessing with has_pending().
        """
        if not self.running or self._loop is None or self._loop.is_closed():
            return "stopped"
        with self._lock:
            if unit_id in self._pending:
                return "pending"
            self._pending[unit_id] = {
                "action": action,
                "requested": value,
                "actor": actor,
                "started_at": self._now().isoformat(),
            }
        try:
            # The queue's getters wait on futures bound to the gateway loop,
            # so the put must be executed on that loop. A bare put_nowait
            # from this thread would set_result on the loop's futures from
            # the wrong thread and the wake-up can be silently lost until
            # the next timer fires. call_soon_threadsafe is the designed
            # cross-thread wakeup.
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait, (event_id, unit_id, action, value, actor)
            )
        except RuntimeError:
            with self._lock:
                self._pending.pop(unit_id, None)
            return "stopped"
        return None

    async def _worker(self) -> None:
        while True:
            event_id, unit_id, action, value, actor = await self._queue.get()
            await self._execute(event_id, unit_id, action, value, actor)

    async def _read_unit(self, unit_id: str):
        context = await asyncio.wait_for(
            self._client.get_context(), timeout=self._command_timeout
        )
        return find_unit_by_id(context, unit_id)

    async def _execute(self, event_id: int, unit_id: str, action: str, value,
                       actor: str) -> None:
        result, detail = "failed", ""
        cancelled = False
        started = asyncio.get_running_loop().time()
        try:
            try:
                unit = await self._read_unit(unit_id)
            except Exception as err:
                detail = _operation_message("state read", err)
            else:
                if unit is None:
                    detail = "the unit was not found in MELCloud Home"
                else:
                    state = unit_state(unit, self._now().isoformat())
                    ok, value, code, msg = validate_request(state, action, value)
                    if not ok:
                        detail = msg
                    else:
                        result, detail = await self._write_and_verify(
                            unit_id, action, value
                        )
        except asyncio.CancelledError:
            # A command interrupted at shutdown stays pending; the next
            # start marks it as an orphaned failure.
            cancelled = True
            raise
        except Exception as err:
            # A per-command failure (a bad read, a validation or
            # normalization error) fails this one command and never the
            # worker: the next command must still be processed.
            detail = _operation_message("command", err)
        finally:
            with self._lock:
                self._pending.pop(unit_id, None)
            if not cancelled:
                logging.getLogger(__name__).info(
                    "Command %s on %s: %s %r %s in %.1f s%s", event_id, unit_id, action,
                    value, result, asyncio.get_running_loop().time() - started,
                    f" ({detail})" if detail else "")
                try:
                    self._store.finish_event(event_id, result, detail)
                except sqlite3.Error:
                    pass

    async def _write_and_verify(self, unit_id: str, action: str, value) -> tuple[str, str]:
        kwargs = _control_kwargs(action, value)
        write_failed = False
        try:
            await asyncio.wait_for(
                self._client.control_ata_unit(unit_id, **kwargs),
                timeout=self._command_timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            write_failed = True
        last_read_error = ""
        for attempt in range(1, self._readback_polls + 1):
            try:
                unit = await self._read_unit(unit_id)
                if unit is not None and _matches(unit, action, value):
                    # The read-back is a fresh cloud report: persist it so
                    # the view shows the confirmed value at once, not on
                    # the next poll.
                    try:
                        states = [unit_state(unit, self._now().isoformat())]
                        self._store.upsert_units(states)
                        self._store.record_history(states)
                    except sqlite3.Error:
                        pass
                    detail = "the write response failed but the change was confirmed" if write_failed else ""
                    return "confirmed", detail
            except asyncio.CancelledError:
                raise
            except Exception as err:
                last_read_error = _operation_message(
                    f"read-back {attempt} of {self._readback_polls}", err
                )
            if attempt < self._readback_polls:
                await asyncio.sleep(self._readback_delay)
        if write_failed:
            return "failed", "the write response failed and the change was not confirmed"
        if last_read_error:
            return "failed", f"the change was not confirmed: {last_read_error}"
        return "failed", (
            f"the change was not confirmed after {self._readback_polls} read-backs"
        )


# --- service ---------------------------------------------------------------------

class PumpService:
    """Synchronous facade over the gateway and store, for the HTTP layer."""

    def __init__(self, data_dir, *, client, teardown=None,
                 client_factory=None, now=utc_now,
                 poll_interval: float = DEFAULT_POLL_INTERVAL,
                 stale_after: float = DEFAULT_STALE_AFTER,
                 readback_polls: int = DEFAULT_READBACK_POLLS,
                 readback_delay: float = DEFAULT_READBACK_DELAY,
                 command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
                 weather_sites: dict | None = None,
                 weather_fetcher=weather.fetch_hourly_temperature,
                 weather_interval: float = WEATHER_UPDATE_INTERVAL,
                 weather_backfill_hours: float = WEATHER_BACKFILL_HOURS,
                 forecast_fetcher=None,
                 prices_db=None,
                 automation_interval: float | None = None,
                 automation_default_on: bool = True,
                 price_transport=None,
                 price_fetch_enabled: bool = True,
                 cottage_factory=None,
                 notifier=None, reloader=None):
        self._data_dir = Path(data_dir)
        self._now = now
        self._stale_after = stale_after
        self.store = PumpStore(self._data_dir / DB_FILENAME)
        self.gateway = PumpGateway(
            client,
            self.store,
            now=now,
            poll_interval=poll_interval,
            stale_after=stale_after,
            readback_polls=readback_polls,
            readback_delay=readback_delay,
            command_timeout=command_timeout,
            teardown=teardown,
            client_factory=client_factory,
        )
        self._weather_sites = {str(k): (float(v[0]), float(v[1]))
                               for k, v in (weather_sites or {}).items()}
        self._weather_fetcher = weather_fetcher
        self._weather_interval = float(weather_interval)
        self._weather_backfill_hours = float(weather_backfill_hours)
        self._forecast_fetcher = forecast_fetcher
        self._room_rates_cache: tuple[datetime, dict] | None = None
        self._room_rates_lock = threading.Lock()
        self._models_cache: dict[str, tuple[datetime, tuple]] = {}
        self._models_lock = threading.Lock()
        self._prices_db = (Path(prices_db) if prices_db is not None
                           else self._data_dir / PRICE_DB_FILENAME)
        self._price_transport = price_transport
        self._price_fetch_enabled = price_fetch_enabled
        self._price_fetch_lock = threading.Lock()
        self._price_fetch_last: datetime | None = None
        self.automation = CoolingAutomation(
            self, SettingsStore(self.store.path), self._prices_db, now=now,
            interval=automation_interval, default_on=automation_default_on)
        self._weather_stop = threading.Event()
        self._weather_write_lock = threading.Lock()
        self._weather_thread: threading.Thread | None = None
        # The cottage site (card H2.3): built from the data directory.
        self.cottage = cottage_factory(self._data_dir) if cottage_factory else None
        if self.cottage is not None:
            settings_store = SettingsStore(self.store.path)
            self.cottage.configure_targets(
                prices_db=self._prices_db, overrides=settings_store.overrides,
                automation_on=self.automation.automation_on)
        # Phone alerts (alerts.py) when a notifier is given.
        self.alerts = None
        if notifier is not None:
            from .alerts import AlertMonitor
            self.alerts = AlertMonitor(self, notifier, SettingsStore(self.store.path),
                                       reloader=reloader, now=now)
        self._closed = False

    # -- lifecycle --

    def start(self) -> None:
        if self.gateway.running:
            return
        # A command interrupted by the previous run's shutdown or by a
        # crash was never confirmed; it cannot be confirmed now, so it is
        # failed. The pending queue lives in the previous process's
        # memory, so every pending event at start time is an orphan, no
        # matter how recently the last process died.
        self.store.fail_orphaned_pending()
        if self._weather_sites:
            self._weather_stop.clear()
            self._weather_thread = threading.Thread(
                target=self._weather_loop, name="pump-weather", daemon=True)
            self._weather_thread.start()
        self.gateway.start()
        self.automation.start()
        if self.cottage is not None:
            self.cottage.start()
        if self.alerts is not None:
            self.alerts.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._weather_write_lock:
            self._weather_stop.set()
        if self._weather_thread is not None:
            self._weather_thread.join(timeout=10.0)
            self._weather_thread = None
        if self.alerts is not None:
            self.alerts.stop()
        self.automation.stop()
        self.gateway.stop()
        if self.cottage is not None:
            self.cottage.stop()
        self.store.close()

    # -- reads --

    def view(self) -> dict:
        now = self._now()
        window = timedelta(seconds=self._stale_after)
        units = []
        for state in self.store.unit_states():
            fresh = False
            if state["updated_at"]:
                try:
                    updated = datetime.fromisoformat(state["updated_at"])
                    fresh = now - updated <= window
                except ValueError:
                    pass
            units.append({
                **{k: state[k] for k in (
                    "id", "name", "power", "mode", "setpoint", "fan",
                    "room_temp", "in_error", "caps",
                )},
                "updated_at": state["updated_at"],
                "fresh": fresh,
                "pending": None,
            })
        # Overlay pending slots on the matching units.
        pending_by_unit = self.gateway._pending_snapshot()
        holds = self.automation.holds() if self.automation.enabled else {}
        for unit in units:
            if unit["id"] in pending_by_unit:
                unit["pending"] = pending_by_unit[unit["id"]]
            unit["hold_until"] = holds.get(unit["id"])
        return {"service": self.gateway.status(), "units": units}

    def events(self, limit: int = 100) -> list[dict]:
        """Pump commands, plus cottage mode changes when a cottage is set up."""
        events = self.store.events(limit)
        if self.cottage is None:
            return events
        merged = [{**e, "site": "home"} for e in events] + self.cottage.events(limit)
        merged.sort(key=lambda e: _to_epoch(e["ts"]), reverse=True)
        return merged[:limit]

    def cottage_view(self) -> dict:
        if self.cottage is None:
            return {"configured": False, "site": None, "rooms": []}
        return self.cottage.view()

    def sites_health(self) -> list[dict]:
        return [] if self.cottage is None else [self.cottage.health()]

    def plan_view(self) -> dict:
        """Today's cooling plan (card F4.1)."""
        return self.automation.day_plan()

    def set_away(self, away: bool, actor: str) -> dict:
        """Home-away button (card F6.1)."""
        self.automation.set_away(away, actor)
        return self.plan_view()

    def refresh_prices(self) -> tuple[int, dict]:
        """`Hae hinnat` button: fetch today's and tomorrow's prices if missing.

        Writes to the prices.db the plan reads. At most one fetch per
        PRICE_FETCH_MIN_SECONDS, whatever its outcome.
        """
        if not self._price_fetch_enabled:
            return 503, {"error": "unavailable",
                         "detail": "Hintoja ei haeta verkosta demotilassa."}
        with self._price_fetch_lock:
            now = self._now()
            last = self._price_fetch_last
            if last is not None and (now - last).total_seconds() < PRICE_FETCH_MIN_SECONDS:
                return 429, {"error": "too_many_requests",
                             "detail": "Hinnat haettiin juuri äsken. "
                                       "Odota hetki ennen uutta hakua."}
            self._price_fetch_last = now
            # prices.db lives in a data directory of its own name.
            with SpotPriceService(self._prices_db.parent,
                                  transport=self._price_transport,
                                  now=self._now) as prices:
                days = prices.fetch_missing()
        return 200, {"days": [{"day": d.day.isoformat(), "status": d.status,
                               "periods": d.interval_count,
                               "detail": d.detail} for d in days]}

    def automation_state(self) -> dict:
        return {"on": self.automation.automation_on(),
                "available": self.automation.available}

    def resume_automation(self, unit_id: str) -> tuple[int, dict]:
        """End a unit's manual pause (Palauta automaatio)."""
        if self.store.unit_state(unit_id) is None:
            return 404, {"error": "unknown_unit",
                         "detail": "the unit is unknown to the service"}
        self.automation.resume(unit_id)
        return 200, self.view()

    def set_automation(self, on: bool, actor: str) -> dict:
        """Master switch of the price and temperature automation."""
        self.automation.set_automation(on, actor)
        return self.automation_state()

    # -- history (card F3.3) ---------------------------------------------------

    def history(self, unit: str, range_name: str,
                site: str | None = None, end=None) -> dict:
        """One history payload: downsampled room/setpoint/outdoor/price.

        range_name is one of day | week | month | year; site selects the
        outdoor-temperature site (default "home"). end moves the window
        (default now) and is capped at HISTORY_AHEAD seconds past now, so
        stored prices and the weather forecast can be browsed too.
        """
        if range_name not in HISTORY_RANGES:
            raise ValueError(
                "range must be one of: " + ", ".join(sorted(HISTORY_RANGES)))
        span_seconds, max_points = HISTORY_RANGES[range_name]
        cottage_room = unit.partition("cottage:")[2] if unit.startswith("cottage:") else None
        if cottage_room is not None and self.cottage is None:
            raise ValueError("no cottage is set up")
        site = site or ("cottage" if cottage_room is not None else "home")
        now = self._now()
        if end is None:
            end = now
        else:
            end = min(end, now + timedelta(seconds=HISTORY_AHEAD))
        start = end - timedelta(seconds=span_seconds)
        cottage = (self.cottage.history(cottage_room, start, end, max_points)
                   if cottage_room is not None else None)
        unit_rows = ([] if cottage is not None
                     else self.store.pump_history(unit, start, end, max_points))
        outdoor = self.store.outdoor_series(site, start, end, max_points)
        factor = vat_factor(SettingsStore(self.store.path).overrides(), site)
        price = self.store.price_series(self._prices_db, start, end,
                                        max_points, factor)
        plan = self.store.plan_series(unit, start, end, max_points)
        commands = self.store.command_marks(unit, start, end)
        energy = self.store.energy_daily(unit, self._prices_db, start, end,
                                         factor)
        forecast = self.store.forecast_rows(
            site, max(start, now - timedelta(hours=1)), end)
        payload = {
            "now": _iso(_to_epoch(now)),
            "vat_percent": round((factor - 1) * 100, 2) if factor != 1 else None,
            "max_end": _iso(_to_epoch(now + timedelta(seconds=HISTORY_AHEAD))),
            "forecast": [[_iso(ts), v] for ts, v in forecast],
            "unit": unit,
            "range": range_name,
            "start": _iso(_to_epoch(start)),
            "end": _iso(_to_epoch(end)),
            "room_temp": [[_iso(ts), v] for ts, v, _ in unit_rows
                          if v is not None],
            "setpoint": [[_iso(ts), v] for ts, _, v in unit_rows
                         if v is not None],
            "outdoor": [[_iso(ts), v] for ts, v in outdoor],
            "price": None if price is None
                     else [[_iso(ts), v] for ts, v in price],
            "plan": [[_iso(ts), v] for ts, v in plan],
            "commands": commands,
            "energy": energy,
        }
        if cottage is not None:
            payload.update(cottage)
        return payload

    # -- outdoor temperature (card F3.3) ---------------------------------------

    def _weather_loop(self) -> None:
        """Backfill each site, retry failed fetches, then update hourly."""
        backfilled = set()
        while not self._weather_stop.is_set():
            failed = False
            for name, (lat, lon) in self._weather_sites.items():
                if self._weather_stop.is_set():
                    return
                try:
                    self._fetch_outdoor(
                        name, lat, lon,
                        hours_back=(WEATHER_OVERLAP_HOURS if name in backfilled
                                    else self._weather_backfill_hours))
                    backfilled.add(name)
                except Exception:
                    failed = True
                if self._forecast_fetcher is not None:
                    try:
                        self._fetch_forecast(name, lat, lon)
                    except Exception:
                        failed = True
            delay = (min(self._weather_interval, WEATHER_RETRY_SECONDS)
                     if failed else self._weather_interval)
            self._weather_stop.wait(delay)

    def _fetch_outdoor(self, site: str, lat: float, lon: float,
                       hours_back: float) -> None:
        end = self._now()
        series = self._weather_fetcher(lat, lon,
                                       end - timedelta(hours=hours_back), end)
        with self._weather_write_lock:
            if not self._weather_stop.is_set():
                self.store.outdoor_upsert(site, list(series.values))

    def _fetch_forecast(self, site: str, lat: float, lon: float) -> None:
        start = self._now()
        series = self._forecast_fetcher(
            lat, lon, start, start + timedelta(hours=FORECAST_HOURS))
        with self._weather_write_lock:
            if not self._weather_stop.is_set():
                self.store.forecast_upsert(site, list(series.values))

    # -- learned room rates (card F5.1) ----------------------------------------

    def room_rates(self) -> dict:
        """Learned rates, held-out error and a short prediction per home
        unit and, with a cottage, per cottage room."""
        now = self._now()
        with self._room_rates_lock:
            cached = self._room_rates_cache
            if cached is not None and 0 <= (now - cached[0]).total_seconds() \
                    < ROOM_RATES_CACHE_SECONDS:
                return cached[1]
            payload = self._compute_room_rates("home", now)
            if self.cottage is not None:
                cottage = self._compute_room_rates("cottage", now)
                payload["units"] += cottage["units"]
                payload["cottage_forecast"] = cottage["forecast"]
            self._room_rates_cache = (now, payload)
            return payload

    def _learning_subjects(self, site: str) -> list[tuple[str, str]]:
        """(id, name) of what is learned at a site: home units, cottage rooms."""
        if site == "cottage":
            if self.cottage is None:
                return []
            from .cottage import ROOMS
            return list(ROOMS.items())
        return [(state["id"], state["name"]) for state in self.store.unit_states()]

    def _learning_readings(self, site: str, subject: str, start, end) -> list:
        if site == "cottage":
            return self.cottage.learning_readings(subject, start, end)
        return self.store.learning_readings(subject, start, end)

    def learned_models(self, site: str = "home") -> tuple[dict, tuple, list]:
        """Fitted room model per home unit or cottage room, (observed,
        forecast-ahead) outdoor and the forecast rows; cached like the rates
        (cards F5.1, F5.2 and F5.4)."""
        now = self._now()
        with self._models_lock:
            cached = self._models_cache.get(site)
            if cached is not None and 0 <= (now - cached[0]).total_seconds() \
                    < ROOM_RATES_CACHE_SECONDS:
                return cached[1]
            start = now - timedelta(days=learning.LEARN_DAYS)
            observed = self.store.outdoor_rows(site, start,
                                               now + timedelta(hours=1))
            ahead = self.store.forecast_rows(
                site, now - timedelta(hours=1),
                now + timedelta(hours=FORECAST_HOURS))
            outdoor = learning.Outdoor(observed)
            # Predictions run on the forecast, falling back to observations.
            future = learning.Outdoor(
                [r for r in observed if r[0] < _to_epoch(now)]
                + [r for r in ahead if r[0] >= _to_epoch(now)])
            models = {
                subject: learning.fit(self._learning_readings(site, subject, start, now),
                                      outdoor)
                for subject, _name in self._learning_subjects(site)}
            result = (models, (outdoor, future), ahead)
            self._models_cache[site] = (now, result)
            return result

    def _compute_room_rates(self, site: str, now: datetime) -> dict:
        start = now - timedelta(days=learning.LEARN_DAYS)
        models, (outdoor, future), ahead = self.learned_models(site)
        now_ts = _to_epoch(now)
        units = []
        rooms_now = ({} if site == "cottage" else
                     {state["id"]: state["room_temp"] for state in self.store.unit_states()})
        for subject, name in self._learning_subjects(site):
            readings = self._learning_readings(site, subject, start, now)
            model = models.get(subject) or learning.fit(readings, outdoor)
            _trained, check = learning.validate(readings, outdoor, now)
            if site == "cottage":
                last = readings[-1] if readings else None
                room = (last.room_c if last is not None and now_ts - last.ts
                        <= learning.MAX_READING_AGE else None)
            else:
                room = rooms_now.get(subject)
            out_now = future.at(now_ts)
            if out_now is None:
                out_now = future.last_before(now_ts)
            current = predicted = None
            if model.ready and room is not None:
                passive = model.passive_rate(room, out_now)
                current = {
                    "room_c": room, "outdoor_c": _round(out_now, 1),
                    "off_c_per_h": round(passive, 2),
                    "cooling_c_per_h": (None if model.cool_c_per_h is None
                                        else round(passive - model.cool_c_per_h, 2)),
                    "heating_c_per_h": (None if model.heat_c_per_h is None
                                        else round(passive + model.heat_c_per_h, 2)),
                }
                end_ts = now_ts + ROOM_RATES_PREDICT_HOURS * 3600
                path = learning.simulate(model, room, now_ts, end_ts,
                                         lambda _ts: (False, None, None), future)
                predicted = {"hours": ROOM_RATES_PREDICT_HOURS,
                             "off_c": round(path[-1][1], 1),
                             "forecast": bool(ahead)}
            units.append({
                "id": subject if site == "home" else f"{site}:{subject}",
                "site": site,
                "name": name if site == "home" else f"Mökki: {name}",
                "hours": (round((readings[-1].ts - readings[0].ts) / 3600)
                          if readings else 0),
                "model": model.public(),
                "validation": None if check is None else check.public(),
                "now": current, "predicted": predicted,
            })
        return {
            "site": site, "updated": _iso(now_ts),
            "learn_days": learning.LEARN_DAYS, "limit_c": learning.MAX_MAE_C,
            "forecast": [[_iso(ts), v] for ts, v in ahead
                         if now_ts - 3600 < ts < now_ts + 24 * 3600],
            "units": units,
        }

    # -- writes --

    def request(self, unit_id: str, action: str, value, actor: str | None) -> tuple[int, dict]:
        if action not in ACTIONS:
            return 400, {"error": "unknown_action",
                         "detail": f"action must be one of {', '.join(ACTIONS)}"}
        if not self.gateway.running:
            return 503, {"error": "not_running",
                         "detail": "the pump service is not running"}
        state = self.store.unit_state(unit_id)
        if state is None:
            return 404, {"error": "unknown_unit",
                         "detail": "the unit is unknown to the service"}
        actor = (actor or "").strip()[:MAX_ACTOR_LENGTH] or DEFAULT_ACTOR
        if state["power"] is None and state["mode"] is None:
            # Home Assistant reports the unit unavailable: refuse at once
            # instead of failing after the read-backs.
            return 409, {"error": "unavailable",
                         "detail": "the unit reports no state"}
        if self.gateway.has_pending(unit_id):
            return 409, {"error": "pending",
                         "detail": "a previous change for this unit is still being confirmed"}
        try:
            ok, value, code, detail = validate_request(state, action, value)
        except Exception:
            # A validation bug must not drop the connection with a raw
            # traceback; report it and let the next command try again.
            return 500, {"error": "internal_error",
                         "detail": "the command could not be validated"}
        if not ok:
            return 400, {"error": code, "detail": detail}
        event_id = self.store.add_event(
            self._now().isoformat(), unit_id, state["name"], actor, action, value)
        reason = self.gateway.submit(event_id, unit_id, action, value, actor)
        if reason is not None:
            if reason == "pending":
                # Lost the reservation race to a concurrent command: the
                # service is fine, this unit is simply busy. The reason
                # comes from submit() itself, decided under the
                # reservation lock, so a slot that clears afterwards
                # cannot turn the answer into a fake shutdown.
                self.store.finish_event(
                    event_id, "failed",
                    "another change for this unit was still being confirmed")
                return 409, {"error": "pending",
                             "detail": "a previous change for this unit "
                                       "is still being confirmed"}
            self.store.finish_event(event_id, "failed",
                                    "the service is shutting down")
            return 503, {"error": "not_running",
                         "detail": "the pump service is shutting down"}
        return 202, {"status": "pending", "unit": unit_id, "action": action,
                     "requested": value}


# --- client factories -------------------------------------------------------------

def make_live_service(data_dir, *, credentials=None, weather_sites=None,
                      poll_interval=None,
                      stale_after=DEFAULT_STALE_AFTER,
                      readback_polls=DEFAULT_READBACK_POLLS,
                      readback_delay=DEFAULT_READBACK_DELAY,
                      command_timeout=DEFAULT_COMMAND_TIMEOUT,
                      automation_interval=None,
                      automation_default_on=True,
                      sites=None, pump_site=None) -> PumpService:
    """Live service with explicit credentials, or development environment values.

    The runtime supplies MELCloud credentials from its credential file.
    Values are never printed or logged. The pumps are read every
    DEFAULT_POLL_INTERVAL seconds from the cloud, or every HA_POLL_INTERVAL
    seconds through Home Assistant, unless ``poll_interval`` says otherwise.
    With the pumps through Home Assistant, phone alerts go through the same
    home Home Assistant, which also reloads the pumps' integration when every
    pump drops out (an expired MELCloud Home session).
    """
    import os

    if poll_interval is None:
        poll_interval = HA_POLL_INTERVAL if pump_site is not None else DEFAULT_POLL_INTERVAL

    cottage_factory = None
    if sites and "cottage" in sites:
        from .cottage import make_cottage_site
        cottage_factory = lambda data: make_cottage_site(data, sites["cottage"])
    common = dict(
        poll_interval=poll_interval, stale_after=stale_after,
        readback_polls=readback_polls, readback_delay=readback_delay,
        command_timeout=command_timeout,
        weather_sites=(weather.weather_sites_from_env(os.environ)
                       if weather_sites is None else weather_sites),
        forecast_fetcher=weather.fetch_forecast_temperature,
        automation_interval=automation_interval,
        automation_default_on=automation_default_on,
        cottage_factory=cottage_factory,
    )
    if pump_site is not None:
        # Card H1.5: the pumps through the home Home Assistant's climate
        # entities, bound in sites.db with their MELCloud unit ids.
        from .credential_file import read_token_file
        from .ha_gateway import BindingStore, HomeAssistantGateway
        from .ha_pumps import client_factory as ha_client_factory
        bindings = BindingStore(Path(data_dir) / "sites.db")
        token_file = pump_site.token_file
        gateway = HomeAssistantGateway("home", pump_site.url,
                                       lambda: read_token_file(token_file), bindings)
        from .alerts import HomeAssistantNotifier
        from .ha_pumps import SessionReloader
        service = PumpService(data_dir, client=None, client_factory=ha_client_factory(
            gateway, lambda: service.store.unit_states()),
            notifier=HomeAssistantNotifier(gateway), reloader=SessionReloader(gateway), **common)
        return service

    username = (credentials.username if credentials is not None else
                os.environ.get("VILLA_KUPPIS_MELCLOUD_USERNAME"))
    password = (credentials.password if credentials is not None else
                os.environ.get("VILLA_KUPPIS_MELCLOUD_PASSWORD"))
    if not username or not password:
        raise RuntimeError(
            "live mode requires the VILLA_KUPPIS_MELCLOUD_USERNAME and "
            "VILLA_KUPPIS_MELCLOUD_PASSWORD environment variables"
        )
    def client_factory():
        client, close_session = _default_client_factory(username, password)

        async def teardown():
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
            finally:
                await close_session()

        return client, teardown

    return PumpService(data_dir, client=None, client_factory=client_factory, **common)
