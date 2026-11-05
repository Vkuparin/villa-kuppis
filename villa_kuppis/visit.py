"""Visit heating for the cottage (card F4.3).

A visit has an arrival time, a target temperature and an optional night
target: from the arrival on, the rooms are kept at the night target during
the night hours of the settings (``visit_night_start``-``visit_night_end``,
Helsinki time) and at the target otherwise. Before the arrival the rooms
heat to the target in force at the arrival.

A room whose rates are learned is planned by ``heat_optimizer`` to reach
that target at the arrival (F5.3, in ``cottage_targets``). For the other
rooms the heat-up is assumed to take ``lead_hours(outdoor)`` at full heating:
8 h at +5 °C or warmer, 12 h at 0 °C and 24 h at -5 °C or colder, in
between linearly (``LEAD_HOURS`` without a forecast). Kammari's 1000 W Mill
took 4 h from 5 to 20.5 °C at +5 °C and 5-7 h at 0 °C in the model fitted
on 4.11.2026; in frost it may not get there at all. The start is chosen
from a few hours' window before ``arrival - lead`` so that the heat-up and
the hold until arrival cost the least at the known prices; holding a warm
room is assumed to take ``hold_factor(target, outdoor)`` of full heating.
Without prices for the whole window the start is simply ``arrival - lead``.

The plan is recomputed on every tick, so new prices move a start that has
not yet happened; a start that has happened stays. From the start until the
visit ends (the end-visit button, or the optional departure time) the visit
target replaces the economic plan for the visit's rooms.

A visit started on site has its arrival now or up to ``ARRIVED_GRACE`` ago:
it heats at once. The active visit's arrival, departure, target and rooms
can be changed (``update``); a new arrival plans the heat-up again.

Phone notices about a visit (warm, late, still on) are sent once each;
``notice_sent``/``mark_notice`` remember them per visit.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from zoneinfo import ZoneInfo

from .planning import Period
from .prices import INTERVAL_SECONDS

HELSINKI = ZoneInfo("Europe/Helsinki")
LEAD_HOURS = 12.0            # without an outdoor forecast
# (outdoor °C, heat-up hours): warmer than the first or colder than the last
# point keeps that point's lead.
LEAD_BY_OUTDOOR = ((5.0, 8.0), (0.0, 12.0), (-5.0, 24.0))
FLEX_HOURS = 4.0
HOLD_FACTOR = Decimal("0.3")    # without an outdoor forecast
# A room heater holds about this much over the outdoor temperature at full
# power (Kammari's 1000 W Mill: 20-40 °C, best fit 29 °C, 4.11.2026).
HEATER_DESIGN_DELTA_C = 30.0
MIN_TARGET = 5.0
MAX_TARGET = 25.0
DEFAULT_NIGHT = ("22:00", "06:00")


def lead_hours(outdoor_c: float | None) -> float:
    """Heat-up lead for a room whose rates are not learned yet."""
    if outdoor_c is None:
        return LEAD_HOURS
    points = LEAD_BY_OUTDOOR
    if outdoor_c >= points[0][0]:
        return points[0][1]
    for (warm, warm_h), (cold, cold_h) in zip(points, points[1:]):
        if outdoor_c >= cold:
            return warm_h + (cold_h - warm_h) * (warm - outdoor_c) / (warm - cold)
    return points[-1][1]


def hold_factor(target_c: float, outdoor_c: float | None) -> Decimal:
    """Share of full heating that holds a warm room at ``target_c``."""
    if outdoor_c is None:
        return HOLD_FACTOR
    share = min(1.0, max(0.1, (target_c - outdoor_c) / HEATER_DESIGN_DELTA_C))
    return Decimal(str(round(share, 2)))


def _minutes(hm: str) -> int:
    hours, minutes = hm.split(":")
    return int(hours) * 60 + int(minutes)


def in_night(at: datetime, night: tuple[str, str] | None) -> bool:
    """``at`` falls in the night window (Helsinki time, may pass midnight)."""
    if not night:
        return False
    local = at.astimezone(HELSINKI)
    now_m, start, end = local.hour * 60 + local.minute, _minutes(night[0]), _minutes(night[1])
    if start == end:
        return False
    return start <= now_m < end if start < end else (now_m >= start or now_m < end)


@dataclass(frozen=True)
class Visit:
    visit_id: int
    arrival: datetime
    target_c: float
    rooms: tuple
    created_by: str
    heat_start: datetime | None      # set once the heat-up has begun
    ended_at: datetime | None
    departure: datetime | None = None   # the visit ends by itself then
    night_c: float | None = None        # the night target; None: the target all night

    def target_at(self, at: datetime, night: tuple[str, str] | None = None) -> float:
        """The visit's target in force at ``at`` once the rooms are in use."""
        if self.night_c is not None and in_night(at, night):
            return self.night_c
        return self.target_c

    def arrival_target(self, night: tuple[str, str] | None = None) -> float:
        """What the heat-up aims at: the target in force at the arrival."""
        return self.target_at(self.arrival, night)


@dataclass(frozen=True)
class VisitPlan:
    start: datetime
    reason: str                      # "cheapest" | "fixed" | "started"
    cost_index: Decimal | None       # relative, for display only
    lead_hours: float | None = None  # the heat-up lead used (unlearned rooms)


def _period_floor(ts: int) -> int:
    return ts - ts % INTERVAL_SECONDS


def plan_start(arrival: datetime, periods: Sequence[Period], now: datetime, *,
               lead_hours: float = LEAD_HOURS, flex_hours: float = FLEX_HOURS,
               hold: Decimal = HOLD_FACTOR) -> VisitPlan:
    """The cheapest heat-up start for an arrival time.

    Candidates run in 15-minute steps from ``flex_hours`` before the
    fixed start up to the fixed start, never before the current period.
    """
    arrival_ts = _period_floor(int(arrival.timestamp()))
    lead = int(lead_hours * 3600)
    latest = _period_floor(arrival_ts - lead)
    now_ts = _period_floor(int(now.timestamp()))
    if latest <= now_ts:
        return VisitPlan(datetime.fromtimestamp(max(latest, now_ts), timezone.utc), "fixed", None,
                         lead_hours)
    earliest = max(_period_floor(latest - int(flex_hours * 3600)), now_ts)
    prices = {p.ts_utc: p.price_c_kwh for p in periods}
    best = None
    for start in range(earliest, latest + 1, INTERVAL_SECONDS):
        heat_end = start + lead
        needed = range(start, arrival_ts, INTERVAL_SECONDS)
        if any(ts not in prices for ts in needed):
            continue
        cost = sum((prices[ts] if ts < heat_end else prices[ts] * hold)
                   for ts in needed)
        # Ties go to the later start: less time held warm.
        if best is None or cost <= best[0]:
            best = (cost, start)
    if best is None:
        return VisitPlan(datetime.fromtimestamp(latest, timezone.utc), "fixed", None, lead_hours)
    return VisitPlan(datetime.fromtimestamp(best[1], timezone.utc), "cheapest", best[0],
                     lead_hours)


def visit_target(visit: Visit | None, room: str, now: datetime,
                 plan: VisitPlan | None, night: tuple[str, str] | None = None) -> float | None:
    """The visit's target for a room now, or None (economic plan applies):
    the arrival's target while heating up, then the day or night target."""
    if visit is None or visit.ended_at is not None or room not in visit.rooms:
        return None
    start = visit.heat_start or (plan.start if plan else None)
    if start is None or now < start:
        return None
    if now < visit.arrival:
        return visit.arrival_target(night)
    return visit.target_at(now, night)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cottage_visit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arrival_utc TEXT NOT NULL,
    target_c REAL NOT NULL,
    rooms_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    heat_start_utc TEXT,
    ended_at_utc TEXT,
    departure_utc TEXT,
    night_c REAL
);
CREATE TABLE IF NOT EXISTS visit_notice (
    visit_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    sent_at_utc TEXT NOT NULL,
    PRIMARY KEY (visit_id, kind)
);
"""
MAX_STAY = timedelta(days=30)
MAX_AHEAD = timedelta(days=14)
ARRIVED_GRACE = timedelta(days=1)    # a visit started on site may have arrived already
# Notices about reaching the target; a changed arrival, target or rooms resets them.
TARGET_NOTICES = ("warm", "late")
_KEEP = object()


def _check(arrival: datetime, departure: datetime | None, target_c: float,
           rooms: Sequence[str], now: datetime, night_c: float | None = None) -> None:
    if night_c is not None and not MIN_TARGET <= night_c <= MAX_TARGET:
        raise VisitError(f"night target must be between {MIN_TARGET:g} and {MAX_TARGET:g} °C")
    if arrival.tzinfo is None or (departure is not None and departure.tzinfo is None):
        raise VisitError("arrival time needs a time zone")
    if departure is not None and departure <= now:
        raise VisitError("departure must be in the future")
    if departure is not None and not arrival < departure <= arrival + MAX_STAY:
        raise VisitError("departure must be after the arrival, within 30 days")
    if arrival <= now - ARRIVED_GRACE:
        raise VisitError("arrival time must be at most a day ago")
    if arrival - now > MAX_AHEAD:
        raise VisitError("arrival time must be within 14 days")
    if not MIN_TARGET <= target_c <= MAX_TARGET:
        raise VisitError(f"target must be between {MIN_TARGET:g} and {MAX_TARGET:g} °C")
    if not rooms:
        raise VisitError("choose at least one room")


class VisitError(ValueError):
    pass


class VisitStore:
    """At most one active visit; ended visits are kept as history."""

    def __init__(self, path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(Path(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        columns = {r[1] for r in self._conn.execute("PRAGMA table_info(cottage_visit)")}
        if "departure_utc" not in columns:     # visits stored before 0.4.36
            self._conn.execute("ALTER TABLE cottage_visit ADD COLUMN departure_utc TEXT")
        if "night_c" not in columns:           # visits stored before 0.4.50
            self._conn.execute("ALTER TABLE cottage_visit ADD COLUMN night_c REAL")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row) -> Visit:
        parse = lambda v: datetime.fromisoformat(v) if v else None
        return Visit(row[0], parse(row[1]), row[2], tuple(json.loads(row[3])), row[4],
                     parse(row[6]), parse(row[7]), parse(row[8]), row[9])

    def active(self) -> Visit | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, arrival_utc, target_c, rooms_json, created_by, created_at_utc, "
                "heat_start_utc, ended_at_utc, departure_utc, night_c "
                "FROM cottage_visit WHERE ended_at_utc IS NULL "
                "ORDER BY id DESC LIMIT 1").fetchone()
        return self._row(row) if row else None

    def start(self, arrival: datetime, target_c: float, rooms: Sequence[str],
              created_by: str, now: datetime, departure: datetime | None = None,
              night_c: float | None = None) -> Visit:
        _check(arrival, departure, target_c, rooms, now, night_c)
        with self._lock:
            # A new visit replaces the active one.
            self._conn.execute("UPDATE cottage_visit SET ended_at_utc = ? "
                               "WHERE ended_at_utc IS NULL", (now.isoformat(),))
            self._conn.execute(
                "INSERT INTO cottage_visit (arrival_utc, target_c, rooms_json, created_by, "
                "created_at_utc, departure_utc, night_c) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (arrival.astimezone(timezone.utc).isoformat(), float(target_c),
                 json.dumps(list(rooms)), created_by, now.isoformat(),
                 departure.astimezone(timezone.utc).isoformat() if departure else None,
                 None if night_c is None else float(night_c)))
            self._conn.commit()
        return self.active()

    def update(self, now: datetime, *, arrival: datetime | None = None, departure=_KEEP,
               target_c: float | None = None, rooms: Sequence[str] | None = None,
               night_c=_KEEP) -> Visit:
        """Change the active visit; None (departure, night_c: _KEEP) keeps a
        value, departure or night_c None clears it. A new arrival plans the
        heat-up again."""
        visit = self.active()
        if visit is None:
            raise VisitError("no active visit")
        new_arrival = visit.arrival if arrival is None else arrival
        new_departure = visit.departure if departure is _KEEP else departure
        new_target = visit.target_c if target_c is None else float(target_c)
        new_rooms = visit.rooms if rooms is None else tuple(rooms)
        new_night = visit.night_c if night_c is _KEEP else (
            None if night_c is None else float(night_c))
        if arrival is not None and arrival.tzinfo is None:
            raise VisitError("arrival time needs a time zone")
        moved = new_arrival != visit.arrival
        if moved:
            _check(new_arrival, new_departure, new_target, new_rooms, now, new_night)
        else:      # an arrival in the past stays valid while the visit is on
            _check(max(new_arrival, now), new_departure, new_target, new_rooms, now, new_night)
            if new_departure is not None and new_departure <= new_arrival:
                raise VisitError("departure must be after the arrival, within 30 days")
        with self._lock:
            self._conn.execute(
                "UPDATE cottage_visit SET arrival_utc = ?, departure_utc = ?, target_c = ?, "
                "rooms_json = ?, night_c = ?, "
                "heat_start_utc = CASE WHEN ? THEN NULL ELSE heat_start_utc END WHERE id = ?",
                (new_arrival.astimezone(timezone.utc).isoformat(),
                 new_departure.astimezone(timezone.utc).isoformat() if new_departure else None,
                 new_target, json.dumps(list(new_rooms)), new_night, moved, visit.visit_id))
            if moved or new_target != visit.target_c or set(new_rooms) != set(visit.rooms):
                self._conn.execute(
                    f"DELETE FROM visit_notice WHERE visit_id = ? AND kind IN "
                    f"({', '.join('?' * len(TARGET_NOTICES))})",
                    (visit.visit_id, *TARGET_NOTICES))
            self._conn.commit()
        return self.active()

    def mark_started(self, visit_id: int, at: datetime) -> None:
        with self._lock:
            self._conn.execute("UPDATE cottage_visit SET heat_start_utc = ? "
                               "WHERE id = ? AND heat_start_utc IS NULL",
                               (at.isoformat(), visit_id))
            self._conn.commit()

    def end(self, now: datetime) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE cottage_visit SET ended_at_utc = ? "
                                     "WHERE ended_at_utc IS NULL", (now.isoformat(),))
            self._conn.commit()
            return cur.rowcount > 0

    def notice_sent(self, visit_id: int, kind: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM visit_notice WHERE visit_id = ? AND kind = ?",
                (visit_id, kind)).fetchone() is not None

    def mark_notice(self, visit_id: int, kind: str, at: datetime) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO visit_notice (visit_id, kind, sent_at_utc) VALUES (?, ?, ?)",
                (visit_id, kind, at.isoformat()))
            self._conn.commit()
