"""Cooling control for the home pumps (cards F4.1, F5.4, F6.1, F6.2).

``plan_day`` (planning.py) turns the stored settings into a target per room
and 15-minute period; ``build_plan`` adds the cooling rules, and the
optimizer (card F5.4, optimizer.py) replaces the plan from the current
period on for rooms with ``price_control`` "optimized" once the room's
cooling rate is learned (card F5.1). With ``price_control`` "off" the
target is always the maximum and the price is not used.

One controller per unit then applies the current period (audit 2.1):

1. Nothing is touched while the automation is off, the unit is not in
   cooling mode, a command is pending, or the manual pause is on. A unit's
   mode is never changed.
2. The target is the plan's: the maximum, the precooling temperature, the
   optimizer's target or, in away mode (card F6.1), the away maximum. In an
   expensive period (``off`` in the plan) the target is the maximum.
3. Power, only with ``cool_power``: a unit that is on is switched off once
   the room is ``STOP_BAND_C`` below the target; a unit that is off is
   switched on when the room is above the target. Every power change waits
   ``min_off_minutes`` from the unit's previous power change.
4. A unit that is on gets the target as its setpoint when they differ.

Manual pause: when the household changes a unit's power, setpoint or mode
(in this app, in MELCloud Home or by remote), the controller leaves the
unit alone for ``manual_hold_hours``. A change counts as manual when an
event by someone other than ``ACTOR`` was logged for the unit, or the unit
changed without any logged command. ``resume`` ends the pause at once, and
switching the automation on starts afresh.

Without a price for the current period the target is the maximum (the away
maximum in away mode), so the temperature control keeps working; nothing
is written to the stored plan for such periods.

Night mode (card F6.2) is separate from the prices: in rooms with
``night_enabled`` the fan is set to its quietest speed between
``night_start`` and ``night_end`` (Helsinki time, may cross midnight) and
the earlier fan setting is put back afterwards, unless the household
changed it meanwhile.

The state is one ``control_state`` row per unit. Every command goes
through ``PumpService.request`` with the actor ``Suunnitelma``, so it is
validated, confirmed and logged like any other.
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

STOP_BAND_C = 0.5    # a unit stops this far below its target
SETPOINT_TOLERANCE_C = 0.05
WATCHED_ACTIONS = ("power", "setpoint", "mode")
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
    reason: str                    # "off" | "precool" | "max" | "away" | "optimizer" | "noprice"


def _setting_values(overrides, room: str) -> dict:
    resolved = app_settings.resolve(overrides, SITE, room)
    return {k: v["value"] for k, v in resolved.items()}


def build_plan(overrides, unit_ids, periods, away: bool = False) -> dict[str, list[CoolPeriod]]:
    """Cooling plan per unit.

    An expensive period is ``off`` (the unit may be switched off) when the
    automation may switch the unit's power; its target is the maximum.
    """
    if not periods:
        return {}
    plan = plan_day(periods, app_settings.plan_settings(overrides, SITE, unit_ids))
    out: dict[str, list[CoolPeriod]] = {}
    for unit_id in unit_ids:
        values = _setting_values(overrides, unit_id)
        high = Decimal(str(values["high_price_c_kwh"]))
        off_allowed = (bool(values["cool_power"])
                       and values["price_control"] != "off")
        limit = float(values["away_max_c" if away else "cool_max_c"])
        rows = []
        for room_plan in plan[unit_id]:
            off = off_allowed and room_plan.price_c_kwh >= high
            if off:
                target, reason = limit, "off"
            elif away:
                target, reason = limit, "away"
            else:
                target = float(room_plan.temperature_c)
                reason = "precool" if room_plan.reason == "threshold" else "max"
            rows.append(CoolPeriod(room_plan.ts_utc, room_plan.price_c_kwh,
                                   target, off, reason))
        out[unit_id] = rows
    return out


def merge_windows(rows: list[CoolPeriod]) -> list[dict]:
    """Consecutive periods with the same decision as [start, end) windows."""
    windows: list[dict] = []
    for row in rows:
        key = (row.target_c, row.off)
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
    """Plans the cooling and runs one controller per home pump."""

    def __init__(self, service, settings_store, prices_db, *, now,
                 interval: float | None = None, default_on: bool = True):
        self._service = service
        self._settings = settings_store
        self._prices_db = Path(prices_db)
        self._now = now
        self.interval = interval
        self._default_on = default_on
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._path = service.store.path
        self._optimized: dict[str, tuple] = {}
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS away_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), away INTEGER NOT NULL, "
                "changed_utc TEXT, actor TEXT)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS automation_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), on_ INTEGER NOT NULL, "
                "changed_utc TEXT, actor TEXT)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS control_state ("
                "unit_id TEXT PRIMARY KEY, "
                "seen_utc TEXT, seen_power INTEGER, seen_setpoint REAL, "
                "seen_mode TEXT, "             # the unit as last observed
                "seen_event INTEGER, "         # the newest event by then
                "power_changed_utc TEXT, "     # its last observed power change
                "hold_until_utc TEXT, "        # manual pause
                "rejected_target REAL, "       # a setpoint the unit refused
                "night INTEGER NOT NULL DEFAULT 0, saved_fan TEXT)")
            self._migrate(conn)
        finally:
            conn.close()

    @staticmethod
    def _migrate(conn) -> None:
        """Carry a quiet-fan night over from the old tables, then drop them."""
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "night_state" in tables:
            conn.execute(
                "INSERT OR IGNORE INTO control_state (unit_id, night, saved_fan) "
                "SELECT unit_id, 1, saved_fan FROM night_state")
        for table in ("plan_state", "power_state", "night_state"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    # -- lifecycle --

    @property
    def available(self) -> bool:
        """The plan loop exists (it may still be switched off)."""
        return self.interval is not None

    @property
    def enabled(self) -> bool:
        """The automation is switched on and so acts on the pumps."""
        return self.available and self.automation_on()

    def automation_on(self) -> bool:
        """The master switch; until it is first used it takes the default."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT on_ FROM automation_state WHERE id = 1").fetchone()
        finally:
            conn.close()
        return bool(row[0]) if row else self._default_on

    def set_automation(self, on: bool, actor: str = ACTOR) -> bool:
        """Switch the whole automation on or off; on applies the plan at once.

        Switching off only stops further commands: pumps stay as they are.
        Switching on starts afresh: what the household did meanwhile is not
        a manual change, and every manual pause ends.
        """
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO automation_state (id, on_, changed_utc, actor) "
                "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "on_ = excluded.on_, changed_utc = excluded.changed_utc, "
                "actor = excluded.actor", (int(on), self._now().isoformat(), actor))
            if on:
                conn.execute("UPDATE control_state SET seen_utc = NULL, "
                             "hold_until_utc = NULL")
        finally:
            conn.close()
        if on and self.available:
            try:
                self.run_once()
            except Exception:
                logging.exception("Cooling plan pass after switching on failed")
        return bool(on)

    def start(self) -> None:
        if not self.available or self._thread is not None:
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
                if self.automation_on():
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

    def _load(self, unit_id: str) -> dict:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM control_state WHERE unit_id = ?",
                               (unit_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"unit_id": unit_id, "seen_utc": None, "seen_power": None,
                    "seen_setpoint": None, "seen_mode": None, "seen_event": None,
                    "power_changed_utc": None, "hold_until_utc": None,
                    "rejected_target": None, "night": 0, "saved_fan": None}
        return dict(row)

    def _save(self, row: dict) -> None:
        keys = list(row)
        conn = self._connect()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO control_state ({', '.join(keys)}) "
                f"VALUES ({', '.join('?' * len(keys))})",
                [row[k] for k in keys])
        finally:
            conn.close()

    # -- manual pause --

    def holds(self) -> dict[str, str]:
        """Units on a manual pause now: unit id -> end of the pause (UTC ISO)."""
        now = self._now()
        conn = self._connect()
        try:
            rows = conn.execute("SELECT unit_id, hold_until_utc FROM control_state "
                                "WHERE hold_until_utc IS NOT NULL").fetchall()
        finally:
            conn.close()
        return {unit: until for unit, until in rows
                if _after(until, now)}

    def resume(self, unit_id: str) -> None:
        """End a unit's manual pause; the next pass controls it again."""
        row = self._load(unit_id)
        row.update(hold_until_utc=None, seen_utc=None)
        self._save(row)
        if self.enabled:
            try:
                self.run_once()
            except Exception:
                logging.exception("Cooling plan pass after resume failed")

    def _observe(self, state, row: dict, values, events, now: datetime) -> bool:
        """Compare the unit with how it was last seen; True while paused.

        A change is manual when someone other than the automation logged a
        command for the unit since then, or a power, setpoint or mode change
        has no command of the automation's to explain it (MELCloud Home, the
        remote). ``events`` is the event log, newest first.
        """
        power = None if state["power"] is None else int(state["power"])
        newest = events[0]["id"] if events else 0
        if row["seen_utc"] is not None:
            new = [e for e in events
                   if e["id"] > (row["seen_event"] or 0)
                   and e["unit"] == state["id"]
                   and e["action"] in WATCHED_ACTIONS]
            ours = {e["action"] for e in new if e["actor"] == ACTOR}
            setpoint_moved = (
                (state["setpoint"] is None) != (row["seen_setpoint"] is None)
                or (state["setpoint"] is not None
                    and row["seen_setpoint"] is not None
                    and abs(state["setpoint"] - row["seen_setpoint"])
                    > SETPOINT_TOLERANCE_C))
            changed = {action for action, moved in (
                ("power", power != row["seen_power"]),
                ("setpoint", setpoint_moved),
                ("mode", state["mode"] != row["seen_mode"])) if moved}
            manual = (any(e["actor"] != ACTOR for e in new)
                      or bool(changed - ours))
            hours = float(values["manual_hold_hours"])
            if manual and hours > 0:
                row["hold_until_utc"] = (now + timedelta(hours=hours)).isoformat()
                logging.info("Manual change on %s: automation paused until %s",
                             state["name"], row["hold_until_utc"])
            if "power" in changed and "power" not in ours:
                row["power_changed_utc"] = now.isoformat()
            if setpoint_moved:
                row["rejected_target"] = None
        if row["hold_until_utc"] is not None and not _after(row["hold_until_utc"], now):
            row["hold_until_utc"] = None
        row.update(seen_utc=now.isoformat(), seen_power=power,
                   seen_setpoint=state["setpoint"], seen_mode=state["mode"],
                   seen_event=newest)
        return row["hold_until_utc"] is not None

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
        if values["price_control"] != "optimized" or state["room_temp"] is None:
            return None
        rows = plan[unit_id]
        now_ts = int(now.timestamp())
        i = next((k for k, r in enumerate(rows)
                  if r.ts_utc <= now_ts < r.ts_utc + INTERVAL_SECONDS), None)
        if i is None:
            return None
        changed = self._load(unit_id)["power_changed_utc"]
        need = max(1, -(-int(values["min_off_minutes"]) * 60 // INTERVAL_SECONDS))
        start_off = 0
        if not state["power"] and changed is not None:
            since = _parse(changed)
            start_off = (need if since is None else
                         1 + int((now - since).total_seconds()) // INTERVAL_SECONDS)
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
                bool(values["cool_power"]), need)
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
        holds = self.holds()
        overrides = self._settings.overrides()
        shown = []
        for u in units:
            if u["id"] not in plan:
                continue
            result = results.get(u["id"])
            shown.append({
                "id": u["id"], "name": u["name"],
                "active": u["mode"] == "Cool",
                "hold_until": holds.get(u["id"]),
                "price_control": _setting_values(overrides, u["id"])["price_control"],
                "windows": merge_windows([r for r in plan[u["id"]]
                                          if r.ts_utc < end]),
                "optimizer": None if result is None else {
                    "optimized": result.optimized,
                    "cost_c": round(result.cost_c, 2),
                    "fixed_cost_c": round(result.fixed_cost_c, 2),
                    "over_max_c_h": round(result.over_max_c_h, 2),
                    "fixed_over_max_c_h": round(result.fixed_over_max_c_h, 2)},
            })
        return {"enabled": self.enabled, "available": self.available,
                "actor": ACTOR,
                "day": now.astimezone(HELSINKI).date().isoformat(),
                "has_prices": bool(plan), "away": self.away(),
                "cool_kw": optimizer.COOL_KW, "units": shown}

    # -- apply --

    def run_once(self) -> list[dict]:
        """Write today's plan targets and run each unit's controller.

        Returns the commands it tried, as dicts with unit, action, value
        and the HTTP-style status of the request.
        """
        if not self.automation_on():
            return []
        now = self._now()
        units, plan, _results = self._plan(now)
        view = {u["id"]: u for u in self._service.view()["units"]}
        overrides = self._settings.overrides()
        away = self.away()
        events = self._service.store.events(200)
        sent: list[dict] = []
        now_ts = int(now.timestamp())
        for state in units:
            unit_id = state["id"]
            rows = plan.get(unit_id)
            if rows:
                self._service.store.plan_upsert(
                    unit_id, [(_iso(r.ts_utc), r.target_c)
                              for r in rows if not r.off])
            shown = view.get(unit_id)
            if (shown is None or not shown["fresh"] or shown["pending"]
                    or state["in_error"]):
                continue
            values = _setting_values(overrides, unit_id)
            row = self._load(unit_id)
            paused = self._observe(state, row, values, events, now)
            try:
                if paused:
                    continue
                sent.extend(self._night(state, row, values, now))
                if state["mode"] != "Cool" or any(
                        s["unit"] == unit_id and s["status"] == 202 for s in sent):
                    continue
                current = next((r for r in rows or []
                                if r.ts_utc <= now_ts < r.ts_utc + INTERVAL_SECONDS),
                               None)
                if current is None:
                    # No price for this period: cool to the maximum (or the
                    # away maximum). Nothing is written to the stored plan.
                    current = CoolPeriod(
                        now_ts - now_ts % INTERVAL_SECONDS, Decimal(0),
                        float(values["away_max_c" if away else "cool_max_c"]),
                        False, "away" if away else "noprice")
                sent.extend(self._control(state, row, current, values, now))
            finally:
                self._save(row)
        return sent

    def _night(self, state, row: dict, values, now) -> list[dict]:
        """Quiet fan during the night hours; restore it when they end."""
        active = in_night(values, now)
        if active and not row["night"] and state["fan"] != QUIET_FAN:
            result = self._command(state, "fan", QUIET_FAN)
            if result["status"] == 202:
                row.update(night=1, saved_fan=state["fan"])
            return [result]
        if not active and row["night"]:
            sent = []
            # Restore only what we set; a fan the household changed stays.
            if state["fan"] == QUIET_FAN and row["saved_fan"] is not None:
                sent.append(self._command(state, "fan", row["saved_fan"]))
                if sent[0]["status"] != 202:
                    return sent
            row.update(night=0, saved_fan=None)
            return sent
        return []

    def _command(self, state, action, value) -> dict:
        status, body = self._service.request(state["id"], action, value, ACTOR)
        entry = {"unit": state["id"], "action": action, "value": value,
                 "status": status}
        if status != 202:
            logging.info("Plan command %s=%r for %s not sent: %s", action,
                         value, state["name"], body.get("detail", status))
        return entry

    def _control(self, state, row: dict, current: CoolPeriod, values,
                 now) -> list[dict]:
        """One unit's controller: power by the room, then the setpoint."""
        target = float(current.target_c)
        room = state["room_temp"]
        if values["cool_power"] and room is not None:
            changed = _parse(row["power_changed_utc"])
            rested = changed is None or now - changed >= timedelta(
                minutes=float(values["min_off_minutes"]))
            switch = None
            if state["power"] and room <= target - STOP_BAND_C:
                switch = False
            elif not state["power"] and room > target:
                switch = True
            if switch is not None and rested:
                # The setpoint follows on the next pass, once the unit is on.
                result = self._command(state, "power", switch)
                if result["status"] == 202:
                    row["power_changed_utc"] = now.isoformat()
                return [result]
        if not state["power"]:
            return []
        if (state["setpoint"] is not None
                and abs(state["setpoint"] - target) <= SETPOINT_TOLERANCE_C):
            return []
        if row["rejected_target"] == target:
            return []                       # refused before; wait for a change
        result = self._command(state, "setpoint", target)
        if result["status"] == 400:
            row["rejected_target"] = target
        return [result]


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _after(value: str | None, now: datetime | None) -> bool:
    """True when the ISO time ``value`` is later than ``now``."""
    when = _parse(value)
    return when is not None and now is not None and when > now
