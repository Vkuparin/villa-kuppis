"""Price-based room targets for the cottage, sent to its Home Assistant (card H2.4).

Every 15-minute price period has one target per cottage room from the fixed
heating thresholds (planning.plan_day with the cottage settings, VAT as
shown). The sender writes the current period's target to the cottage
integration's ``villa_kuppis_cottage.set_target`` service with the period
start as the revision and a validity window, and treats it as acknowledged
when the room's status sensor reports that revision and target. Each new
period is a new revision, which is also the heartbeat: if Villa Kuppis,
Tailscale or the link stops, the cottage target expires and the cottage
falls back to its safe setpoint on its own.

Each write also carries the rest of the room's plan over the known prices
as a schedule (only the periods where the target changes), so an outage of
Villa Kuppis, the home internet or Tailscale does not stop the plan or a
visit heat-up: the cottage keeps to it on its own until it runs out and
only then falls back (card H3.3). The schedule is sent only to an
integration that reports ``schedule_steps`` on its status sensor; an older
one gets the single target as before.

After a disconnect nothing is replayed: the next tick sends only the target
valid now and the plan from it on. Switching ``Automaatio`` off releases
each room once: a target below every safe setpoint, valid for a minute and
without a schedule, so the cottage drops to its fallback at once instead of
following the last plan for up to a day. A sender that starts with
``Automaatio`` off releases every room once too, since the cottage may
still hold a plan from before the restart. After that nothing is sent.

With ``Hintaohjaus`` set to ``Optimoitu`` (the default) a room has no price
thresholds. It is kept between ``heat_base_c`` and ``heat_max_c`` by the
heating optimizer (card F5.2, ``heat_optimizer``), planned again every
period over all known prices with the room's measured heat balance
(``thermal``): heat is stored before expensive hours when that is cheaper
than heating through them. The baseline and the fallback is holding the
base level, so a plan never costs more than that under the model, and a
room without a temperature or a forecast simply holds the base level. A
damp room's band starts ``HUMIDITY_RAISE_C`` higher. A visit heat-up uses
the optimizer only in ``Optimoitu`` rooms whose learned rates
(``learning``) passed their held-out day, and plans with
``VISIT_HEAT_MARGIN`` of the learned heating rate so the room is warm by
the arrival even if the heaters are slower than learned.

A hand-set room target ("Tavoite" on the room card) replaces the room's
whole plan, visit included, until "Palauta automaatio" removes it or a
visit with the room is started or its target or rooms change (the newest
choice wins). It is
sent with ``Automaatio`` off too: a person asked for it, so it is not
automation. So is an active visit: with ``Automaatio`` off its rooms get
the visit's steps (heat-up and hold until the departure) and the
fallback for every other step, so the visit heats on time while the rest
of the plan stays off.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from . import settings as app_settings
from .heat_optimizer import PERIOD_SECONDS, STORE_MARGIN, HeatLimits, optimize_heating, period_step
from .planning import Period, plan_day
from .prices import INTERVAL_SECONDS
from .storage import PriceStore
from .visit import (DEFAULT_NIGHT, VisitPlan, VisitStore, hold_factor, lead_hours, plan_start,
                    visit_target)

SITE = "cottage"
HELSINKI = ZoneInfo("Europe/Helsinki")
DOMAIN = "villa_kuppis_cottage"
VALID_MINUTES = 45          # three periods: a missed write or two is not a fallback
RETRY_SECONDS = 60.0        # resend an unacknowledged target after this
ACTOR = "Suunnitelma"
HUMIDITY_RAISE_C = 2.0      # a damp room is kept this much above the base level
HUMIDITY_CLEAR = 5.0        # until it is this many %-points below the limit
# Automaatio off: the integration clamps this to its safe setpoint and the
# short validity ends in fallback a minute later.
RELEASE_TARGET = 0.0
RELEASE_MINUTES = 1
# A visit heat-up is planned as if the heaters warmed the room this much
# slower than learned: an early warm room costs a little, a cold arrival more.
VISIT_HEAT_MARGIN = 0.7
# A heat-up by the room's heat balance is planned this much longer than it
# says, at most MAX_THERMAL_LEAD_H (a room that does not get there heats
# from that early) and at least MIN_THERMAL_LEAD_H.
THERMAL_LEAD_FACTOR = 1.15
MAX_THERMAL_LEAD_H = 36.0
MIN_THERMAL_LEAD_H = 1.0
# The visit lead is chosen by the forecast this long before the arrival.
VISIT_OUTDOOR_BEFORE = timedelta(hours=6)


def thermal_lead(model, start_c: float, goal_c: float, outdoor_c: float) -> float:
    """Hours a room needs at full power from ``start_c`` to ``goal_c`` by
    its heat balance, times ``THERMAL_LEAD_FACTOR``; ``MAX_THERMAL_LEAD_H``
    when it does not get there. The balance matched a real heat-up (Mökki,
    1050 W, 5.-6.11.2026: 6.3 -> 12.0 °C in 15 h, 12.2 predicted), where the
    fixed 8-hour lead in mild weather would have arrived at about 15 °C
    with all three heaters."""
    room, hours, limit = start_c, 0.0, MAX_THERMAL_LEAD_H / THERMAL_LEAD_FACTOR
    while room < goal_c - 1e-6:
        if hours >= limit:
            return MAX_THERMAL_LEAD_H
        room = period_step(model, room, outdoor_c, goal_c)[0]
        hours += PERIOD_SECONDS / 3600
    return max(MIN_THERMAL_LEAD_H, hours * THERMAL_LEAD_FACTOR)


def visit_night(overrides: Mapping) -> tuple[str, str]:
    """The visit's night hours (Helsinki time) from the cottage settings."""
    values = app_settings.resolve(overrides, SITE)
    try:
        return (values["visit_night_start"]["value"], values["visit_night_end"]["value"])
    except KeyError:
        return DEFAULT_NIGHT


@dataclass(frozen=True)
class RoomTarget:
    room: str
    period_ts: int
    target_c: float
    price_c_kwh: Decimal | None
    reason: str                 # "base" | "threshold" | "noprice" | "optimized" | "visit" | "hand" | "off"


def current_targets(overrides: Mapping, periods: Sequence[Period], rooms: Sequence[str],
                    now_ts: int) -> dict[str, RoomTarget]:
    """The target of each room for the period containing ``now_ts``.

    Without a price for the period the room gets its base level, so a
    missing price day never stops the cottage plan.
    """
    period_ts = now_ts - now_ts % INTERVAL_SECONDS
    current = [p for p in periods if p.ts_utc == period_ts]
    plan = plan_day(current, app_settings.plan_settings(overrides, SITE, list(rooms)))
    out = {}
    for room in rooms:
        rows = plan.get(room) or []
        if rows:
            row = rows[0]
            out[room] = RoomTarget(room, period_ts, float(row.temperature_c),
                                   row.price_c_kwh, row.reason)
        else:
            base = app_settings.resolve(overrides, SITE, room)["heat_base_c"]["value"]
            out[room] = RoomTarget(room, period_ts, float(base), None, "noprice")
    return out


def schedule(steps: Sequence[RoomTarget]) -> list[dict]:
    """The plan after the current period for ``set_target``: the periods
    where the target changes, and the last period, which ends the plan."""
    out, value = [], steps[0].target_c if steps else None
    for i, step in enumerate(steps[1:], 1):
        if step.target_c != value or i == len(steps) - 1:
            start = datetime.fromtimestamp(step.period_ts, timezone.utc)
            out.append({"start": start.isoformat(), "target": step.target_c})
            value = step.target_c
    return out


def heat_limits(values: Mapping, damp: bool = False) -> HeatLimits:
    """The optimizer's band for a room: never below its base level, and
    ``HUMIDITY_RAISE_C`` higher at the bottom while the room is damp."""
    floor = float(values["heat_base_c"]["value"]) + (HUMIDITY_RAISE_C if damp else 0.0)
    return HeatLimits(floor, max(floor, float(values["heat_max_c"]["value"])))


def optimized_room(overrides: Mapping, room: str) -> bool:
    return (app_settings.resolve(overrides, SITE, room)["heat_price_control"]["value"]
            == "optimized")


def takes_schedule(state: dict | None) -> bool:
    """True when the room's status sensor is from an integration that
    keeps a schedule (version 0.3.0 on)."""
    return "schedule_steps" in ((state or {}).get("attributes") or {})


def acknowledged(target: float, revision: int) -> Callable[[dict], bool]:
    """Read-back check on the room status sensor's attributes."""
    def check(state: dict) -> bool:
        attrs = state.get("attributes") or {}
        requested = attrs.get("requested_target")
        return (attrs.get("revision") == revision and isinstance(requested, (int, float))
                and abs(requested - target) < 1e-6)
    return check


class CottageTargetSender:
    """Keeps each cottage room's current target written to Home Assistant.

    ``rooms`` maps a settings room name to the gateway device bound to that
    room's ``sensor.villa_kuppis_<room>_target``. Call ``tick`` about once
    a minute from a service thread.
    """

    def __init__(self, gateway, rooms: Mapping[str, str], *, prices_db: Path,
                 overrides: Callable[[], Mapping], automation_on: Callable[[], bool],
                 now: Callable[[], datetime], visits: VisitStore | None = None,
                 models: Callable[[], tuple] | None = None,
                 thermal: Callable[[], tuple] | None = None,
                 room_temps: Callable[[], Mapping[str, float]] | None = None,
                 room_humidity: Callable[[], Mapping[str, float]] | None = None,
                 hand_targets: Callable[[], Mapping[str, float]] | None = None,
                 valid_minutes: float = VALID_MINUTES,
                 retry_seconds: float = RETRY_SECONDS):
        self._gateway = gateway
        self._rooms = dict(rooms)
        self._prices_db = Path(prices_db)
        self._overrides = overrides
        self._automation_on = automation_on
        self._now = now
        self._visits = visits
        self.visit_plan: VisitPlan | None = None
        # models() -> ({room: validated RoomModel}, outdoor forecast);
        # room_temps() -> {room: °C now}; room_humidity() -> {room: % now}.
        self._models = models
        # thermal() -> ({room: (RoomModel from the heat balance, heater kW)},
        # outdoor forecast): the maintenance plan of Optimoitu rooms.
        self._thermal = thermal
        self._room_temps = room_temps
        self._room_humidity = room_humidity
        self._hand_targets = hand_targets        # () -> {room: hand-set °C}
        self.damp: set[str] = set()              # rooms the humidity guard heats
        self._optimized: dict[str, tuple] = {}   # room -> (key, HeatPlan or None)
        self.heat_plans: dict[str, object] = {}  # room -> its latest HeatPlan or None
        self._valid_minutes = valid_minutes
        self._retry = timedelta(seconds=retry_seconds)
        # room -> (target, revision, request_id, sent_at)
        self._sent: dict[str, tuple] = {}
        self._sent_data: dict[str, dict] = {}  # includes the future schedule and validity
        self.last: dict[str, RoomTarget] = {}
        self.schedules: dict[str, list[RoomTarget]] = {}

    def _periods(self, day: date, factor: Decimal) -> list[Period]:
        if not self._prices_db.exists():
            return []
        with PriceStore(self._prices_db) as store:
            rows = store.stored_day(day.isoformat()) or []
        return [Period(r.ts_utc, r.price_c_kwh * factor) for r in rows]

    def targets(self) -> dict[str, RoomTarget]:
        """The target of each room now; ``schedules`` gets the plans."""
        self.schedules = self.plans()
        return {room: steps[0] for room, steps in self.schedules.items()}

    def _hand(self) -> dict[str, float]:
        hand = self._hand_targets() if self._hand_targets is not None else {}
        return {room: float(value) for room, value in hand.items() if room in self._rooms}

    def plans(self) -> dict[str, list[RoomTarget]]:
        """Each room's targets from the current period to the end of the
        known prices: the economic plan, replaced by an active visit's
        target from its heat-up start on (card F4.3), and a hand-set
        target over both."""
        out = self._plans()
        for room, value in self._hand().items():
            if room in out:
                out[room] = [RoomTarget(room, t.period_ts, value, t.price_c_kwh, "hand")
                             for t in out[room]]
        return out

    def _plans(self) -> dict[str, list[RoomTarget]]:
        now = self._now()
        overrides = self._overrides()
        factor = Decimal(str(app_settings.vat_factor(overrides, SITE)))
        # The optimizer weighs the whole price per kWh; the thresholds stay on spot.
        extra = app_settings.price_extra(overrides, SITE)
        today = now.astimezone(HELSINKI).date()
        periods = self._periods(today, factor) + self._periods(today + timedelta(days=1), factor)
        now_ts = int(now.timestamp())
        rooms = list(self._rooms)
        period_ts = now_ts - now_ts % INTERVAL_SECONDS
        ahead = [p for p in periods if p.ts_utc >= period_ts]
        priced = bool(ahead) and ahead[0].ts_utc == period_ts
        if not priced:
            out = {room: [target] for room, target
                   in current_targets(overrides, [], rooms, now_ts).items()}
        else:
            fixed = plan_day(ahead, app_settings.plan_settings(overrides, SITE, rooms))
            out = {room: [RoomTarget(room, row.ts_utc, float(row.temperature_c),
                                     row.price_c_kwh, row.reason) for row in fixed[room]]
                   for room in rooms}
        self._mark_damp(out, overrides)
        self.heat_plans = {}
        if priced and self._thermal is not None:
            self._optimize(out, overrides, ahead, extra)
        self._humidity_guard(out, overrides)
        visit = self._visits.active() if self._visits is not None else None
        if visit is not None and visit.departure is not None and now >= visit.departure:
            self._visits.end(visit.departure)      # the departure time has come
            visit = None
        if visit is None:
            self.visit_plan = None
            return out
        night = visit_night(overrides)
        if visit.heat_start is not None:
            plan = VisitPlan(visit.heat_start, "started", None)
        else:
            outdoor = self._outdoor_at(visit.arrival - VISIT_OUTDOOR_BEFORE)
            plan = plan_start(visit.arrival, periods, now,
                              lead_hours=self._visit_lead(visit, overrides, night, outdoor),
                              hold=hold_factor(visit.arrival_target(night), outdoor))
            if now >= plan.start:
                self._visits.mark_started(visit.visit_id, now)
                plan = VisitPlan(now, "started", None)
        self.visit_plan = plan
        economic = {room: list(steps) for room, steps in out.items()}
        learned = self._optimize_visit(out, visit, plan, overrides, periods, now, extra, night)
        for room, steps in out.items():
            if room in learned:
                continue
            for i, target in enumerate(steps):
                # A later period heats when the heat-up starts during it.
                value = visit_target(visit, room, self._period_at(now, i, target), plan, night)
                if value is not None:
                    steps[i] = RoomTarget(room, target.period_ts, value, target.price_c_kwh, "visit")
        if visit.departure is not None:
            # The schedule the cottage keeps goes back to the economic plan at departure.
            departure = visit.departure.timestamp()
            for room, steps in out.items():
                out[room] = [economic[room][i] if t.period_ts >= departure and t.reason == "visit"
                             else t for i, t in enumerate(steps)]
        return out

    @staticmethod
    def _period_at(now: datetime, i: int, target: RoomTarget) -> datetime:
        """The moment a plan step stands for: now for the current period,
        the last second of a later one."""
        if i == 0:
            return now
        return datetime.fromtimestamp(target.period_ts + INTERVAL_SECONDS - 1, now.tzinfo)

    def _visit_lead(self, visit, overrides: Mapping, night, outdoor: float | None) -> float:
        """Hours the visit's heat-up takes: the longest of its rooms by their
        heat balance (``thermal_lead``), from the base level or the room's
        reading if it is colder; the fixed ``lead_hours`` without a balance
        or a forecast."""
        fixed = lead_hours(outdoor)
        if self._thermal is None or outdoor is None:
            return fixed
        try:
            models, _outdoor = self._thermal()
            temps = self._room_temps() if self._room_temps is not None else {}
        except Exception as error:      # no balance: the fixed lead
            logging.warning("Cottage visit lead: %s", type(error).__name__)
            return fixed
        hand = self._hand()
        goal = visit.arrival_target(night)
        leads = []
        for room in visit.rooms:
            if room in hand:
                continue
            model, heat_kw = models.get(room) or (None, 0.0)
            if model is None or heat_kw <= 0 or not model.heat_c_per_h:
                leads.append(fixed)
                continue
            base = float(app_settings.resolve(overrides, SITE, room)["heat_base_c"]["value"])
            now_c = temps.get(room)
            start = min(base, now_c) if now_c is not None else base
            leads.append(thermal_lead(model, start, goal, outdoor))
        return max(leads) if leads else fixed

    def _outdoor_at(self, at: datetime) -> float | None:
        """The outdoor forecast at ``at``, or None when there is none."""
        source = self._models or self._thermal
        if source is None:
            return None
        try:
            _models, outdoor = source()
            ts = int(at.timestamp())
            value = outdoor.at(ts)
            return value if value is not None else outdoor.last_before(ts)
        except Exception:     # no forecast: the fixed lead without it
            return None

    def _humidity_guard(self, out: dict[str, list[RoomTarget]], overrides: Mapping) -> None:
        """Setting humidity_heat: a damp room (``_mark_damp``) is kept at
        least HUMIDITY_RAISE_C above its base level."""
        for room in self.damp & set(out):
            floor = app_settings.resolve(overrides, SITE, room)["heat_base_c"]["value"] \
                + HUMIDITY_RAISE_C
            out[room] = [t if t.target_c >= floor
                         else RoomTarget(room, t.period_ts, floor, t.price_c_kwh, "humidity")
                         for t in out[room]]

    def _mark_damp(self, out: Mapping[str, list], overrides: Mapping) -> None:
        """Keep ``damp``: the rooms over the humidity limit, until they are
        HUMIDITY_CLEAR %-points below it. A missing reading keeps the room
        as it was."""
        values = app_settings.resolve(overrides, SITE)
        if self._room_humidity is None or not values["humidity_heat"]["value"]:
            self.damp.clear()
            return
        limit = values["humidity_limit_pct"]["value"]
        humidity = self._room_humidity()
        for room in out:
            value = humidity.get(room)
            if value is not None and value > limit:
                self.damp.add(room)
            elif value is not None and value < limit - HUMIDITY_CLEAR:
                self.damp.discard(room)

    def _optimize(self, out: dict[str, list[RoomTarget]], overrides: Mapping,
                  ahead: Sequence[Period], extra: float = 0.0) -> None:
        """Replace the plans of optimized rooms (card F5.2); ``ahead`` are
        the known periods from the current one on. The baseline is holding
        the floor, which ``out`` already has for these rooms."""
        rooms = [room for room in out if optimized_room(overrides, room)]
        models = outdoor = temps = None
        for room in rooms:
            limits = heat_limits(app_settings.resolve(overrides, SITE, room),
                                 room in self.damp)
            key = (ahead[0].ts_utc, len(ahead), limits, extra)
            cached = self._optimized.get(room)
            if cached is None or cached[0] != key:
                plan = None
                try:
                    if models is None:
                        models, outdoor = self._thermal()
                        temps = self._room_temps() if self._room_temps is not None else {}
                    model, heat_kw = models.get(room) or (None, 0.0)
                    if model is not None and heat_kw > 0:
                        plan = optimize_heating(
                            model, temps.get(room),
                            [(p.ts_utc, float(p.price_c_kwh) + extra) for p in ahead],
                            outdoor, limits, [limits.min_c] * len(ahead),
                            heat_kw=heat_kw, store_margin=STORE_MARGIN)
                except Exception as error:      # no plan: the room holds its floor
                    logging.warning("Cottage heating plan for %s: %s", room,
                                    type(error).__name__)
                cached = self._optimized[room] = (key, plan)
            plan = cached[1]
            self.heat_plans[room] = plan
            if plan is not None and plan.optimized:
                out[room] = [t if step.target_c <= limits.min_c + 1e-9
                             else RoomTarget(room, t.period_ts, step.target_c,
                                             t.price_c_kwh, "optimized")
                             for t, step in zip(out[room], plan.steps)]

    def _optimize_visit(self, out: dict[str, list[RoomTarget]], visit, plan: VisitPlan,
                        overrides: Mapping, periods: Sequence[Period], now: datetime,
                        extra: float = 0.0, night: tuple[str, str] | None = None) -> set:
        """Heat-ups of learned rooms planned to reach the target at arrival
        (card F5.3); returns the rooms it set. The baseline is the fixed-lead
        visit plan, so the result never costs more than it. From the
        arrival on the room follows the visit's day and night targets."""
        arrival = int(visit.arrival.timestamp())
        now_ts = int(now.timestamp())
        if self._models is None or visit.ended_at is not None or now_ts >= arrival:
            return set()
        rooms = [r for r in visit.rooms if r in out and out[r][0].reason != "noprice"
                 and optimized_room(overrides, r)]
        ahead = [p for p in periods if p.ts_utc >= now_ts - now_ts % INTERVAL_SECONDS]
        if not rooms or not ahead or ahead[-1].ts_utc < arrival:
            return set()   # the arrival is past the known prices
        fixed = plan_day(ahead, app_settings.plan_settings(overrides, SITE, rooms))
        start_ts = int(plan.start.timestamp())
        goal = visit.arrival_target(night)
        models = outdoor = temps = None
        done = set()
        for room in rooms:
            values = app_settings.resolve(overrides, SITE, room)
            base = [goal if p.ts_utc >= start_ts else float(r.temperature_c)
                    for p, r in zip(ahead, fixed[room])]
            limits = heat_limits(values)
            key = ("visit", ahead[0].ts_utc, len(ahead), arrival, goal,
                   limits, tuple(base), extra)
            cached = self._optimized.get(("visit", room))
            if cached is None or cached[0] != key:
                if models is None:
                    models, outdoor = self._models()
                    temps = self._room_temps() if self._room_temps is not None else {}
                model = models.get(room)
                if model is not None and model.heat_c_per_h:
                    model = replace(model, heat_c_per_h=model.heat_c_per_h * VISIT_HEAT_MARGIN)
                result = None if model is None else optimize_heating(
                    model, temps.get(room), [(p.ts_utc, float(p.price_c_kwh) + extra) for p in ahead],
                    outdoor, limits, base, visit=(arrival, goal))
                cached = self._optimized[("visit", room)] = (key, result)
            result = cached[1]
            if result is not None:
                out[room] = [RoomTarget(
                    room, t.period_ts,
                    step.target_c if t.period_ts < arrival
                    else visit.target_at(self._period_at(now, i, t), night),
                    t.price_c_kwh, "visit")
                    for i, (t, step) in enumerate(zip(out[room], result.steps))]
                done.add(room)
        return done

    def _release(self, keep=()) -> list[dict]:
        """Automaatio off: hand each room back to its fallback, once.

        A room this sender has not written yet (Villa Kuppis restarted) is
        released too, with the current period as the revision, since the
        cottage may still follow a plan from before the restart.
        """
        if self._gateway.health()["state"] not in ("ok", "stale"):
            return []
        now = self._now()
        now_ts = int(now.timestamp())
        out = []
        for room, device_id in self._rooms.items():
            if room in keep:
                continue
            previous = self._sent.get(room)
            if previous is None:
                previous = (RELEASE_TARGET, now_ts - now_ts % INTERVAL_SECONDS, None, now)
            revision = previous[1]
            if previous[0] is None:   # released already: retry only a lost write
                state = (self._gateway.command(previous[2]) or {}).get("state")
                # A refused release (an older revision) would be refused again.
                if (state in ("pending", "confirmed", "failed")
                        or now - previous[3] < self._retry):
                    continue
            cmd = self._gateway.submit_service(
                device_id, DOMAIN, "set_target",
                {"room": room, "target": RELEASE_TARGET, "revision": revision,
                 "valid_minutes": RELEASE_MINUTES},
                ACTOR, acknowledged(RELEASE_TARGET, revision))
            self._sent[room] = (None, revision, cmd["request_id"], now)
            self._sent_data.pop(room, None)
            out.append(cmd)
        return out

    def preview(self) -> dict[str, RoomTarget]:
        """Refresh ``last`` and ``visit_plan`` without sending anything."""
        self.last = self.targets()
        return self.last

    def tick(self) -> list[dict]:
        """Send what is due; returns the commands submitted this tick.

        With ``Automaatio`` off only hand-set targets and an active visit
        are sent; the other rooms are released."""
        if not self._automation_on():
            targets = self.preview()
            hand = self._hand()
            visit = self._visits.active() if self._visits is not None else None
            rooms = set(hand) | {r for r in (visit.rooms if visit is not None else ())
                                 if r in targets}
            for room in rooms - set(hand):
                # Only the visit's own steps; the rest of the plan stays off.
                steps = [t if t.reason == "visit" else
                         RoomTarget(room, t.period_ts, RELEASE_TARGET, None, "off")
                         for t in self.schedules[room]]
                self.schedules[room] = steps
                self.last[room] = targets[room] = steps[0]
            out = self._release(keep=rooms)
            if rooms and self._gateway.health()["state"] in ("ok", "stale"):
                out += self._send({room: targets[room] for room in rooms})
            return out
        if self._gateway.health()["state"] not in ("ok", "stale"):
            return []   # retried on the first tick after the reconnect
        targets = self.targets()
        self.last.update(targets)
        return self._send(targets)

    def _send(self, targets: Mapping[str, RoomTarget]) -> list[dict]:
        now = self._now()
        out = []
        snapshot = self._gateway.snapshot()
        for room, target in targets.items():
            device_id = self._rooms[room]
            revision = target.period_ts
            data = {"room": room, "target": target.target_c, "revision": revision,
                    "valid_minutes": self._valid_minutes}
            if takes_schedule(snapshot.get(device_id)):
                data["schedule"] = schedule(self.schedules.get(room) or [])
            previous = self._sent.get(room)
            if previous is not None:
                state = self._gateway.command(previous[2]) or {}
                if state.get("state") == "pending":
                    continue
                if self._sent_data.get(room) == data and (
                        state.get("state") == "confirmed" or now - previous[3] < self._retry):
                    continue
            cmd = self._gateway.submit_service(
                device_id, DOMAIN, "set_target", data,
                ACTOR, acknowledged(target.target_c, revision))
            self._sent[room] = (target.target_c, revision, cmd["request_id"], now)
            self._sent_data[room] = data
            out.append(cmd)
        return out
