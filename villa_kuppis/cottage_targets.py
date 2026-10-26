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
valid now and the plan from it on. With ``Automaatio`` off nothing is sent, so economic control
stops and the cottage's local fallback takes over when the last target
expires.

With ``Hintaohjaus`` set to ``Optimoitu`` a room whose learned model passed
its held-out day gets the heating optimizer's target instead (card F5.2,
``heat_optimizer``), planned once per period over all known prices; the
fixed thresholds stay its fallback and its baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from . import settings as app_settings
from .heat_optimizer import HeatLimits, optimize_heating
from .planning import Period, plan_day
from .prices import INTERVAL_SECONDS
from .storage import PriceStore
from .visit import VisitPlan, VisitStore, plan_start, visit_target

SITE = "cottage"
HELSINKI = ZoneInfo("Europe/Helsinki")
DOMAIN = "villa_kuppis_cottage"
VALID_MINUTES = 45          # three periods: a missed write or two is not a fallback
RETRY_SECONDS = 60.0        # resend an unacknowledged target after this
ACTOR = "Suunnitelma"


@dataclass(frozen=True)
class RoomTarget:
    room: str
    period_ts: int
    target_c: float
    price_c_kwh: Decimal | None
    reason: str                 # "base" | "threshold" | "noprice" | "optimized" | "visit"


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
                 room_temps: Callable[[], Mapping[str, float]] | None = None,
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
        # room_temps() -> {room: °C now}.
        self._models = models
        self._room_temps = room_temps
        self._optimized: dict[str, tuple] = {}   # room -> (key, HeatPlan or None)
        self._valid_minutes = valid_minutes
        self._retry = timedelta(seconds=retry_seconds)
        # room -> (target, revision, request_id, sent_at)
        self._sent: dict[str, tuple] = {}
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

    def plans(self) -> dict[str, list[RoomTarget]]:
        """Each room's targets from the current period to the end of the
        known prices: the economic plan, replaced by an active visit's
        target from its heat-up start on (card F4.3)."""
        now = self._now()
        overrides = self._overrides()
        factor = Decimal(str(app_settings.vat_factor(overrides, SITE)))
        today = now.astimezone(HELSINKI).date()
        periods = self._periods(today, factor) + self._periods(today + timedelta(days=1), factor)
        now_ts = int(now.timestamp())
        rooms = list(self._rooms)
        period_ts = now_ts - now_ts % INTERVAL_SECONDS
        ahead = [p for p in periods if p.ts_utc >= period_ts]
        if not ahead or ahead[0].ts_utc != period_ts:
            out = {room: [target] for room, target
                   in current_targets(overrides, [], rooms, now_ts).items()}
        else:
            fixed = plan_day(ahead, app_settings.plan_settings(overrides, SITE, rooms))
            out = {room: [RoomTarget(room, row.ts_utc, float(row.temperature_c),
                                     row.price_c_kwh, row.reason) for row in fixed[room]]
                   for room in rooms}
            if self._models is not None:
                self._optimize(out, overrides, ahead)
        visit = self._visits.active() if self._visits is not None else None
        if visit is None:
            self.visit_plan = None
            return out
        if visit.heat_start is not None:
            plan = VisitPlan(visit.heat_start, "started", None)
        else:
            plan = plan_start(visit.arrival, periods, now)
            if now >= plan.start:
                self._visits.mark_started(visit.visit_id, now)
                plan = VisitPlan(now, "started", None)
        self.visit_plan = plan
        learned = self._optimize_visit(out, visit, plan, overrides, periods, now)
        for room, steps in out.items():
            if room in learned:
                continue
            for i, target in enumerate(steps):
                # A later period heats when the heat-up starts during it.
                at = now if i == 0 else datetime.fromtimestamp(
                    target.period_ts + INTERVAL_SECONDS - 1, now.tzinfo)
                value = visit_target(visit, room, at, plan)
                if value is not None:
                    steps[i] = RoomTarget(room, target.period_ts, value, target.price_c_kwh, "visit")
        return out

    def _optimize(self, out: dict[str, list[RoomTarget]], overrides: Mapping,
                  ahead: Sequence[Period]) -> None:
        """Replace the economic plans of optimized rooms (card F5.2);
        ``ahead`` are the known periods from the current one on."""
        rooms = [room for room in out
                 if app_settings.resolve(overrides, SITE, room)["heat_price_control"]["value"]
                 == "optimized"]
        models = outdoor = temps = None
        for room in rooms:
            values = app_settings.resolve(overrides, SITE, room)
            fixed = [target.target_c for target in out[room]]
            key = (ahead[0].ts_utc, len(ahead), values["smart_min_c"]["value"],
                   values["smart_max_c"]["value"], tuple(fixed))
            cached = self._optimized.get(room)
            if cached is None or cached[0] != key:
                if models is None:
                    models, outdoor = self._models()
                    temps = self._room_temps() if self._room_temps is not None else {}
                model = models.get(room)
                plan = None if model is None else optimize_heating(
                    model, temps.get(room), [(p.ts_utc, float(p.price_c_kwh)) for p in ahead],
                    outdoor, HeatLimits(float(values["smart_min_c"]["value"]),
                                        float(values["smart_max_c"]["value"])),
                    fixed)
                cached = self._optimized[room] = (key, plan)
            plan = cached[1]
            if plan is not None and plan.optimized:
                out[room] = [RoomTarget(room, t.period_ts, step.target_c, t.price_c_kwh, "optimized")
                             for t, step in zip(out[room], plan.steps)]

    def _optimize_visit(self, out: dict[str, list[RoomTarget]], visit, plan: VisitPlan,
                        overrides: Mapping, periods: Sequence[Period], now: datetime) -> set:
        """Heat-ups of learned rooms planned to reach the target at arrival
        (card F5.3); returns the rooms it set. The baseline is the fixed-lead
        visit plan, so the result never costs more than it."""
        arrival = int(visit.arrival.timestamp())
        now_ts = int(now.timestamp())
        if self._models is None or visit.ended_at is not None or now_ts >= arrival:
            return set()
        rooms = [r for r in visit.rooms if r in out and out[r][0].reason != "noprice"]
        ahead = [p for p in periods if p.ts_utc >= now_ts - now_ts % INTERVAL_SECONDS]
        if not rooms or not ahead or ahead[-1].ts_utc < arrival:
            return set()   # the arrival is past the known prices
        fixed = plan_day(ahead, app_settings.plan_settings(overrides, SITE, rooms))
        start_ts = int(plan.start.timestamp())
        models = outdoor = temps = None
        done = set()
        for room in rooms:
            values = app_settings.resolve(overrides, SITE, room)
            base = [visit.target_c if p.ts_utc >= start_ts else float(r.temperature_c)
                    for p, r in zip(ahead, fixed[room])]
            key = ("visit", ahead[0].ts_utc, len(ahead), arrival, visit.target_c,
                   values["smart_min_c"]["value"], values["smart_max_c"]["value"], tuple(base))
            cached = self._optimized.get(("visit", room))
            if cached is None or cached[0] != key:
                if models is None:
                    models, outdoor = self._models()
                    temps = self._room_temps() if self._room_temps is not None else {}
                model = models.get(room)
                result = None if model is None else optimize_heating(
                    model, temps.get(room), [(p.ts_utc, float(p.price_c_kwh)) for p in ahead],
                    outdoor, HeatLimits(float(values["smart_min_c"]["value"]),
                                        float(values["smart_max_c"]["value"])),
                    base, visit=(arrival, visit.target_c))
                cached = self._optimized[("visit", room)] = (key, result)
            result = cached[1]
            if result is not None:
                out[room] = [RoomTarget(room, t.period_ts, step.target_c, t.price_c_kwh, "visit")
                             for t, step in zip(out[room], result.steps)]
                done.add(room)
        return done

    def preview(self) -> dict[str, RoomTarget]:
        """Refresh ``last`` and ``visit_plan`` without sending anything."""
        self.last = self.targets()
        return self.last

    def tick(self) -> list[dict]:
        """Send what is due; returns the commands submitted this tick."""
        if not self._automation_on():
            self.preview()
            return []
        if self._gateway.health()["state"] not in ("ok", "stale"):
            return []   # retried on the first tick after the reconnect
        now = self._now()
        out = []
        snapshot = self._gateway.snapshot()
        for room, target in self.targets().items():
            self.last[room] = target
            device_id = self._rooms[room]
            revision = target.period_ts
            previous = self._sent.get(room)
            if previous is not None and previous[:2] == (target.target_c, revision):
                state = self._gateway.command(previous[2]) or {}
                if state.get("state") in ("pending", "confirmed"):
                    continue
                if now - previous[3] < self._retry:
                    continue
            data = {"room": room, "target": target.target_c, "revision": revision,
                    "valid_minutes": self._valid_minutes}
            if takes_schedule(snapshot.get(device_id)):
                data["schedule"] = schedule(self.schedules.get(room) or [])
            cmd = self._gateway.submit_service(
                device_id, DOMAIN, "set_target", data,
                ACTOR, acknowledged(target.target_c, revision))
            self._sent[room] = (target.target_c, revision, cmd["request_id"], now)
            out.append(cmd)
        return out
