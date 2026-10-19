"""Fixed price thresholds for pump cooling (card F4.1).

``plan_day`` (planning.py) turns the stored settings into a target per room
and 15-minute period. This module adds the cooling rules around it and
applies them to the pumps:

* Only units in cooling mode are touched, and a unit's mode is never
  changed.
* A period is "off" when the price is at or above the high price and the
  room's ``cool_off_when_expensive`` is on; otherwise the unit cools to the
  planned target (the maximum, or the precooling temperature when the price
  is below the low price).
* The automation switches off only what it can switch back on: it records
  when it switched a unit off and brings only those units back, never
  before ``min_off_minutes`` have passed (compressor protection), when the
  price is no longer high or the room has reached the maximum. A unit the
  household switched off is left alone.
* It acts on changes of the plan, not on every pass, so a manual setpoint
  stays until the plan moves to another target.

Every command goes through ``PumpService.request`` with the actor
``Suunnitelma``, so it is validated, confirmed and logged like any other.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import settings as app_settings
from .planning import Period, plan_day
from .prices import HELSINKI, INTERVAL_SECONDS
from .storage import PriceStore

ACTOR = "Suunnitelma"
SITE = "home"
DEFAULT_INTERVAL = 60.0


@dataclass(frozen=True)
class CoolPeriod:
    ts_utc: int
    price_c_kwh: Decimal
    target_c: float
    off: bool
    reason: str                    # "off" | "precool" | "max"


def _setting_values(overrides, room: str) -> dict:
    resolved = app_settings.resolve(overrides, SITE, room)
    return {k: v["value"] for k, v in resolved.items()}


def build_plan(overrides, unit_ids, periods) -> dict[str, list[CoolPeriod]]:
    """Cooling plan per unit; units whose room is set to heating are left out."""
    if not periods:
        return {}
    plan = plan_day(periods, app_settings.plan_settings(overrides, SITE, unit_ids))
    out: dict[str, list[CoolPeriod]] = {}
    for unit_id in unit_ids:
        values = _setting_values(overrides, unit_id)
        if values["kind"] != "cool":
            continue
        high = Decimal(str(values["high_price_c_kwh"]))
        off_allowed = bool(values["cool_off_when_expensive"])
        rows = []
        for room_plan in plan[unit_id]:
            off = off_allowed and room_plan.price_c_kwh >= high
            reason = ("off" if off else
                      "precool" if room_plan.reason == "threshold" else "max")
            rows.append(CoolPeriod(room_plan.ts_utc, room_plan.price_c_kwh,
                                   float(room_plan.temperature_c), off, reason))
        out[unit_id] = rows
    return out


def merge_windows(rows: list[CoolPeriod]) -> list[dict]:
    """Consecutive periods with the same decision as [start, end) windows."""
    windows: list[dict] = []
    for row in rows:
        key = (None if row.off else row.target_c, row.off)
        end = row.ts_utc + INTERVAL_SECONDS
        if windows and windows[-1]["_key"] == key and windows[-1]["_end"] == row.ts_utc:
            windows[-1]["_end"] = end
            continue
        windows.append({"_key": key, "_start": row.ts_utc, "_end": end,
                        "target_c": key[0], "off": row.off})
    return [{"start": _iso(w["_start"]), "end": _iso(w["_end"]),
             "target_c": w["target_c"], "off": w["off"]} for w in windows]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


class CoolingAutomation:
    """Plans and applies the fixed-threshold cooling for the home pumps."""

    def __init__(self, service, settings_store, prices_db, *, now,
                 interval: float | None = None):
        self._service = service
        self._settings = settings_store
        self._prices_db = Path(prices_db)
        self._now = now
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._path = service.store.path
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS plan_state ("
                "unit_id TEXT PRIMARY KEY, off_since_utc TEXT, "
                "last_target REAL)")
        finally:
            conn.close()

    # -- lifecycle --

    @property
    def enabled(self) -> bool:
        return self.interval is not None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pump-plan",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logging.exception("Cooling plan pass failed")
            self._stop.wait(self.interval)

    # -- state --

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0,
                               isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _load(self, unit_id: str) -> tuple[str | None, float | None]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT off_since_utc, last_target FROM plan_state "
                "WHERE unit_id = ?", (unit_id,)).fetchone()
        finally:
            conn.close()
        return (row[0], row[1]) if row else (None, None)

    def _save(self, unit_id: str, off_since: str | None,
              last_target: float | None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO plan_state (unit_id, off_since_utc, last_target) "
                "VALUES (?, ?, ?) ON CONFLICT(unit_id) DO UPDATE SET "
                "off_since_utc = excluded.off_since_utc, "
                "last_target = excluded.last_target",
                (unit_id, off_since, last_target))
        finally:
            conn.close()

    # -- plan --

    def _periods(self, day: date) -> list[Period]:
        if not self._prices_db.exists():
            return []
        store = PriceStore(self._prices_db)
        try:
            rows = store.stored_day(day.isoformat())
        finally:
            store.close()
        return [Period(r.ts_utc, r.price_c_kwh) for r in rows or []]

    def _plan(self, now: datetime):
        units = self._service.store.unit_states()
        periods = self._periods(now.astimezone(HELSINKI).date())
        plan = build_plan(self._settings.overrides(),
                          [u["id"] for u in units], periods)
        return units, plan

    def day_plan(self) -> dict:
        """Today's plan per unit as merged windows, for the app."""
        now = self._now()
        units, plan = self._plan(now)
        shown = [{"id": u["id"], "name": u["name"],
                  "active": u["mode"] == "Cool",
                  "windows": merge_windows(plan[u["id"]])}
                 for u in units if u["id"] in plan]
        return {"enabled": self.enabled, "actor": ACTOR,
                "day": now.astimezone(HELSINKI).date().isoformat(),
                "has_prices": bool(plan), "units": shown}

    # -- apply --

    def run_once(self) -> list[dict]:
        """Write today's plan targets and apply the current period.

        Returns the commands it tried, as dicts with unit, action, value
        and the HTTP-style status of the request.
        """
        now = self._now()
        units, plan = self._plan(now)
        fresh = {u["id"]: u["fresh"] for u in self._service.view()["units"]}
        overrides = self._settings.overrides()
        sent: list[dict] = []
        now_ts = int(now.timestamp())
        for state in units:
            unit_id = state["id"]
            rows = plan.get(unit_id)
            if not rows:
                continue
            self._service.store.plan_upsert(
                unit_id, [(_iso(r.ts_utc), r.target_c) for r in rows if not r.off])
            if state["mode"] != "Cool" or not fresh.get(unit_id) \
                    or state["in_error"]:
                continue
            current = next((r for r in rows
                            if r.ts_utc <= now_ts < r.ts_utc + INTERVAL_SECONDS),
                           None)
            if current is None:
                continue
            values = _setting_values(overrides, unit_id)
            sent.extend(self._apply(state, current, values, now))
        return sent

    def _command(self, state, action, value) -> dict:
        status, body = self._service.request(state["id"], action, value, ACTOR)
        entry = {"unit": state["id"], "action": action, "value": value,
                 "status": status}
        if status != 202:
            logging.info("Plan command %s=%r for %s not sent: %s", action,
                         value, state["name"], body.get("detail", status))
        return entry

    def _apply(self, state, current: CoolPeriod, values, now) -> list[dict]:
        unit_id = state["id"]
        off_since, last_target = self._load(unit_id)
        sent: list[dict] = []
        if state["power"] and off_since is not None:
            # Switched on again (by us or by the household).
            off_since = None
            self._save(unit_id, None, last_target)

        if current.off:
            if state["power"]:
                result = self._command(state, "power", False)
                sent.append(result)
                if result["status"] == 202:
                    self._save(unit_id, now.isoformat(), None)
            elif off_since is not None:
                # Off by us during an expensive period: back on only when the
                # room has reached the maximum and the minimum off time is up.
                room = state["room_temp"]
                if (self._off_time_over(off_since, values, now)
                        and room is not None
                        and room >= float(values["cool_max_c"])):
                    sent.append(self._power_on(state, last_target))
            return sent

        if not state["power"]:
            if off_since is not None and self._off_time_over(off_since, values, now):
                sent.append(self._power_on(state, last_target))
            return sent

        if last_target != current.target_c:
            result = self._command(state, "setpoint", current.target_c)
            sent.append(result)
            if result["status"] == 202:
                self._save(unit_id, None, current.target_c)
        return sent

    def _power_on(self, state, last_target) -> dict:
        result = self._command(state, "power", True)
        if result["status"] == 202:
            # The setpoint is set again on the next pass, once on.
            self._save(state["id"], None, None)
        return result

    @staticmethod
    def _off_time_over(off_since: str, values, now: datetime) -> bool:
        try:
            since = datetime.fromisoformat(off_since)
        except ValueError:
            return True
        return now - since >= timedelta(minutes=float(values["min_off_minutes"]))
