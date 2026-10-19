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
* Away mode (card F6.1, ``set_away``): every cooling unit targets its
  ``away_max_c`` with no precooling; the expensive-price switch-off still
  applies, and a unit comes back when the room reaches the away maximum.
  Switching away off restores the normal plan on the next pass.
* A unit brought back on because its room reached the maximum holds the
  maximum until the expensive stretch ends, instead of being switched off
  again on the next pass.
* It acts on changes of the plan, not on every pass, so a manual setpoint
  stays until the plan moves to another target.

* The optimizer (card F5.4, optimizer.py) replaces the fixed plan from the
  current period to the end of the known prices (today and, once fetched,
  tomorrow) for rooms with ``cool_optimizer`` on, once the room's cooling
  rate is learned (card F5.1). It is not used in away mode. Its plan is
  applied with the same rules as above, and it is planned again at each
  new period from the room's current temperature.

* Night mode (card F6.2) is separate from the prices: in rooms with
  ``night_enabled`` the fan is set to its quietest speed between
  ``night_start`` and ``night_end`` (Helsinki time, may cross midnight) and
  the earlier fan setting is put back afterwards, unless the household
  changed it meanwhile.

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

from . import optimizer
from . import settings as app_settings
from .planning import Period, plan_day
from .prices import HELSINKI, INTERVAL_SECONDS, helsinki_day_bounds
from .storage import PriceStore

ACTOR = "Suunnitelma"
SITE = "home"
DEFAULT_INTERVAL = 60.0
QUIET_FAN = "One"


def in_night(values: dict, now: datetime) -> bool:
    """True when the room's night mode is on and `now` is inside its hours."""
    if not values["night_enabled"]:
        return False
    local = now.astimezone(HELSINKI)
    minutes = local.hour * 60 + local.minute
    start, end = (int(values[k][:2]) * 60 + int(values[k][3:5])
                  for k in ("night_start", "night_end"))
    if start == end:
        return False
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


@dataclass(frozen=True)
class CoolPeriod:
    ts_utc: int
    price_c_kwh: Decimal
    target_c: float
    off: bool
    reason: str                    # "off" | "precool" | "max" | "away" | "optimizer"


def _setting_values(overrides, room: str) -> dict:
    resolved = app_settings.resolve(overrides, SITE, room)
    return {k: v["value"] for k, v in resolved.items()}


def build_plan(overrides, unit_ids, periods, away: bool = False) -> dict[str, list[CoolPeriod]]:
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
            if away:
                target, reason = float(values["away_max_c"]), "away"
            else:
                target = float(room_plan.temperature_c)
                reason = "precool" if room_plan.reason == "threshold" else "max"
            if off:
                reason = "off"
            rows.append(CoolPeriod(room_plan.ts_utc, room_plan.price_c_kwh,
                                   target, off, reason))
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
        self._optimized: dict[str, tuple] = {}
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS plan_state ("
                "unit_id TEXT PRIMARY KEY, off_since_utc TEXT, "
                "last_target REAL)")
            try:
                conn.execute("ALTER TABLE plan_state ADD COLUMN "
                             "hold_on INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass                       # column exists already
            conn.execute(
                "CREATE TABLE IF NOT EXISTS away_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), away INTEGER NOT NULL, "
                "changed_utc TEXT, actor TEXT)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS night_state ("
                "unit_id TEXT PRIMARY KEY, saved_fan TEXT)")
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

    def _load(self, unit_id: str) -> tuple[str | None, float | None, bool]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT off_since_utc, last_target, hold_on FROM plan_state "
                "WHERE unit_id = ?", (unit_id,)).fetchone()
        finally:
            conn.close()
        return (row[0], row[1], bool(row[2])) if row else (None, None, False)

    def _save(self, unit_id: str, off_since: str | None,
              last_target: float | None, hold: bool = False) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO plan_state (unit_id, off_since_utc, last_target, "
                "hold_on) VALUES (?, ?, ?, ?) ON CONFLICT(unit_id) DO UPDATE "
                "SET off_since_utc = excluded.off_since_utc, "
                "last_target = excluded.last_target, "
                "hold_on = excluded.hold_on",
                (unit_id, off_since, last_target, int(hold)))
        finally:
            conn.close()

    # -- away mode (card F6.1) --

    def away(self) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT away FROM away_state WHERE id = 1").fetchone()
        finally:
            conn.close()
        return bool(row[0]) if row else False

    def set_away(self, away: bool, actor: str = ACTOR) -> bool:
        """Turn away mode on or off and apply it at once when automation runs."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO away_state (id, away, changed_utc, actor) "
                "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "away = excluded.away, changed_utc = excluded.changed_utc, "
                "actor = excluded.actor",
                (int(away), self._now().isoformat(), actor))
        finally:
            conn.close()
        if self.enabled:
            try:
                self.run_once()
            except Exception:
                logging.exception("Cooling plan pass after away change failed")
        return bool(away)

    # -- plan --

    def _periods(self, day: date) -> list[Period]:
        if not self._prices_db.exists():
            return []
        store = PriceStore(self._prices_db)
        try:
            rows = store.stored_day(day.isoformat())
        finally:
            store.close()
        # The thresholds are compared with the price as shown, VAT included.
        factor = Decimal(str(app_settings.vat_factor(
            self._settings.overrides(), "home")))
        return [Period(r.ts_utc, r.price_c_kwh * factor) for r in rows or []]

    def _plan(self, now: datetime):
        """Units, the plan per unit (today, then tomorrow when stored) and
        the optimizer's result per unit (None: fixed thresholds)."""
        units = self._service.store.unit_states()
        today = now.astimezone(HELSINKI).date()
        periods = self._periods(today)
        if periods:
            periods += self._periods(today + timedelta(days=1))
        overrides = self._settings.overrides()
        away = self.away()
        plan = build_plan(overrides, [u["id"] for u in units], periods, away)
        results: dict[str, optimizer.Plan | None] = {}
        if plan and not away:
            for state in units:
                if state["id"] in plan:
                    results[state["id"]] = self._optimize(
                        state, plan, overrides, now)
        return units, plan, results

    def _optimize(self, state, plan, overrides, now) -> optimizer.Plan | None:
        """Replace the plan from the current period on with the optimizer's
        (card F5.4); planned once per period and cached."""
        unit_id = state["id"]
        values = _setting_values(overrides, unit_id)
        if not values["cool_optimizer"] or state["room_temp"] is None:
            return None
        rows = plan[unit_id]
        now_ts = int(now.timestamp())
        i = next((k for k, r in enumerate(rows)
                  if r.ts_utc <= now_ts < r.ts_utc + INTERVAL_SECONDS), None)
        if i is None:
            return None
        off_since, _target, _hold = self._load(unit_id)
        need = max(1, -(-int(values["min_off_minutes"]) * 60 // INTERVAL_SECONDS))
        start_off = 0
        if not state["power"] and off_since is not None:
            try:
                since = datetime.fromisoformat(off_since)
                start_off = 1 + int((now - since).total_seconds()) // INTERVAL_SECONDS
            except ValueError:
                start_off = need
        key = (rows[i].ts_utc, len(rows), rows[-1].ts_utc,
               tuple(sorted(values.items())), min(start_off, need))
        cached = self._optimized.get(unit_id)
        if cached is not None and cached[0] == key:
            result = cached[1]
        else:
            models, (_observed, future), _ahead = self._service.learned_models(SITE)
            model = models.get(unit_id)
            limits = optimizer.Limits(
                float(values["cool_max_c"]), float(values["precool_c"]),
                float(values["low_price_c_kwh"]), float(values["high_price_c_kwh"]),
                bool(values["cool_off_when_expensive"]), need)
            result = None if model is None else optimizer.optimize(
                model, float(state["room_temp"]), start_off,
                [(r.ts_utc, float(r.price_c_kwh)) for r in rows[i:]],
                future, limits, [(r.off, r.target_c) for r in rows[i:]])
            self._optimized[unit_id] = (key, result)
        if result is not None and result.optimized:
            rows[i:] = [CoolPeriod(r.ts_utc, rows[i + k].price_c_kwh, r.target_c,
                                   r.off, "off" if r.off else "optimizer")
                        for k, r in enumerate(result.steps)]
        return result

    def day_plan(self) -> dict:
        """Today's plan per unit as merged windows, for the app."""
        now = self._now()
        units, plan, results = self._plan(now)
        _start, end = helsinki_day_bounds(now.astimezone(HELSINKI).date())
        shown = []
        for u in units:
            if u["id"] not in plan:
                continue
            result = results.get(u["id"])
            shown.append({
                "id": u["id"], "name": u["name"],
                "active": u["mode"] == "Cool",
                "windows": merge_windows([r for r in plan[u["id"]]
                                          if r.ts_utc < end]),
                "optimizer": None if result is None else {
                    "optimized": result.optimized,
                    "cost_c": round(result.cost_c, 2),
                    "fixed_cost_c": round(result.fixed_cost_c, 2),
                    "over_max_c_h": round(result.over_max_c_h, 2),
                    "fixed_over_max_c_h": round(result.fixed_over_max_c_h, 2)},
            })
        return {"enabled": self.enabled, "actor": ACTOR,
                "day": now.astimezone(HELSINKI).date().isoformat(),
                "has_prices": bool(plan), "away": self.away(),
                "cool_kw": optimizer.COOL_KW, "units": shown}

    # -- apply --

    def run_once(self) -> list[dict]:
        """Write today's plan targets and apply the current period.

        Returns the commands it tried, as dicts with unit, action, value
        and the HTTP-style status of the request.
        """
        now = self._now()
        units, plan, _results = self._plan(now)
        fresh = {u["id"]: u["fresh"] for u in self._service.view()["units"]}
        overrides = self._settings.overrides()
        sent: list[dict] = []
        now_ts = int(now.timestamp())
        for state in units:
            if fresh.get(state["id"]) and not state["in_error"]:
                sent.extend(self._night(state, overrides, now))
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
            if current.reason in ("away", "off") and self.away():
                values["cool_max_c"] = values["away_max_c"]
            sent.extend(self._apply(state, current, values, now))
        return sent

    def _night(self, state, overrides, now) -> list[dict]:
        """Quiet fan during the night hours; restore it when they end."""
        unit_id = state["id"]
        active = in_night(_setting_values(overrides, unit_id), now)
        conn = self._connect()
        try:
            row = conn.execute("SELECT saved_fan FROM night_state "
                               "WHERE unit_id = ?", (unit_id,)).fetchone()
        finally:
            conn.close()
        if active and row is None and state["fan"] != QUIET_FAN:
            result = self._command(state, "fan", QUIET_FAN)
            if result["status"] == 202:
                self._set_night(unit_id, state["fan"])
            return [result]
        if not active and row is not None:
            sent = []
            # Restore only what we set; a fan the household changed stays.
            if state["fan"] == QUIET_FAN and row[0] is not None:
                sent.append(self._command(state, "fan", row[0]))
                if sent[0]["status"] != 202:
                    return sent
            self._set_night(unit_id, None, clear=True)
            return sent
        return []

    def _set_night(self, unit_id, fan, clear=False) -> None:
        conn = self._connect()
        try:
            if clear:
                conn.execute("DELETE FROM night_state WHERE unit_id = ?",
                             (unit_id,))
            else:
                conn.execute("INSERT OR REPLACE INTO night_state "
                             "(unit_id, saved_fan) VALUES (?, ?)",
                             (unit_id, fan))
        finally:
            conn.close()

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
        off_since, last_target, hold = self._load(unit_id)
        sent: list[dict] = []
        if state["power"] and off_since is not None:
            # Switched on again (by us or by the household).
            off_since = None
            self._save(unit_id, None, last_target, hold)

        if current.off and not (hold and state["power"]):
            if state["power"]:
                result = self._command(state, "power", False)
                sent.append(result)
                if result["status"] == 202:
                    self._save(unit_id, now.isoformat(), None)
            elif off_since is not None:
                # Off by us during an expensive period: back on only when the
                # room has reached the maximum and the minimum off time is up;
                # it then holds the maximum until the stretch ends.
                room = state["room_temp"]
                if (self._off_time_over(off_since, values, now)
                        and room is not None
                        and room >= float(values["cool_max_c"])):
                    sent.append(self._power_on(state, last_target, hold=True))
            return sent
        if not current.off and hold:
            hold = False
            self._save(unit_id, off_since, last_target)

        if not state["power"]:
            if off_since is not None and self._off_time_over(off_since, values, now):
                sent.append(self._power_on(state, last_target))
            return sent

        if last_target != current.target_c:
            result = self._command(state, "setpoint", current.target_c)
            sent.append(result)
            if result["status"] == 202:
                self._save(unit_id, None, current.target_c, hold)
        return sent

    def _power_on(self, state, last_target, hold: bool = False) -> dict:
        result = self._command(state, "power", True)
        if result["status"] == 202:
            # The setpoint is set again on the next pass, once on.
            self._save(state["id"], None, None, hold)
        return result

    @staticmethod
    def _off_time_over(off_since: str, values, now: datetime) -> bool:
        try:
            since = datetime.fromisoformat(off_since)
        except ValueError:
            return True
        return now - since >= timedelta(minutes=float(values["min_off_minutes"]))
