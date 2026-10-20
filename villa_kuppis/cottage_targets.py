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

After a disconnect nothing is replayed: the next tick sends only the target
valid now. With ``Automaatio`` off nothing is sent, so economic control
stops and the cottage's local fallback takes over when the last target
expires.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from . import settings as app_settings
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
    reason: str                 # "base" | "threshold" | "noprice" | "visit"


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
        self._valid_minutes = valid_minutes
        self._retry = timedelta(seconds=retry_seconds)
        # room -> (target, revision, request_id, sent_at)
        self._sent: dict[str, tuple] = {}
        self.last: dict[str, RoomTarget] = {}

    def _periods(self, day: date, factor: Decimal) -> list[Period]:
        if not self._prices_db.exists():
            return []
        with PriceStore(self._prices_db) as store:
            rows = store.stored_day(day.isoformat()) or []
        return [Period(r.ts_utc, r.price_c_kwh * factor) for r in rows]

    def targets(self) -> dict[str, RoomTarget]:
        """The economic plan per room, replaced by an active visit's target
        from its heat-up start on (card F4.3)."""
        now = self._now()
        overrides = self._overrides()
        factor = Decimal(str(app_settings.vat_factor(overrides, SITE)))
        today = now.astimezone(HELSINKI).date()
        periods = self._periods(today, factor)
        out = current_targets(overrides, periods, list(self._rooms), int(now.timestamp()))
        visit = self._visits.active() if self._visits is not None else None
        if visit is None:
            self.visit_plan = None
            return out
        if visit.heat_start is not None:
            plan = VisitPlan(visit.heat_start, "started", None)
        else:
            periods += self._periods(today + timedelta(days=1), factor)
            plan = plan_start(visit.arrival, periods, now)
            if now >= plan.start:
                self._visits.mark_started(visit.visit_id, now)
                plan = VisitPlan(now, "started", None)
        self.visit_plan = plan
        for room, target in out.items():
            value = visit_target(visit, room, now, plan)
            if value is not None:
                out[room] = RoomTarget(room, target.period_ts, value, target.price_c_kwh, "visit")
        return out

    def tick(self) -> list[dict]:
        """Send what is due; returns the commands submitted this tick."""
        if not self._automation_on():
            return []
        if self._gateway.health()["state"] not in ("ok", "stale"):
            return []   # retried on the first tick after the reconnect
        now = self._now()
        out = []
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
            cmd = self._gateway.submit_service(
                device_id, DOMAIN, "set_target",
                {"room": room, "target": target.target_c, "revision": revision,
                 "valid_minutes": self._valid_minutes},
                ACTOR, acknowledged(target.target_c, revision))
            self._sent[room] = (target.target_c, revision, cmd["request_id"], now)
            out.append(cmd)
        return out
