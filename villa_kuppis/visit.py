"""Visit heating for the cottage (card F4.3).

A visit has an arrival time and a target temperature. A room whose rates are
learned is planned by ``heat_optimizer`` to reach the target at the arrival
(F5.3, in ``cottage_targets``). For the other rooms the heat-up is assumed to take ``LEAD_HOURS`` at
full heating. The start is chosen from a few hours' window before
``arrival - LEAD_HOURS`` so that the heat-up and the hold until arrival cost
the least at the known prices; holding a warm room is assumed to take
``HOLD_FACTOR`` of full heating. Without prices for the whole window the
start is simply ``arrival - LEAD_HOURS``.

The plan is recomputed on every tick, so new prices move a start that has
not yet happened; a start that has happened stays. From the start until the
visit ends (the end-visit button, or the optional departure time) the visit
target replaces the economic plan for the visit's rooms.

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

from .planning import Period
from .prices import INTERVAL_SECONDS

LEAD_HOURS = 12.0
FLEX_HOURS = 4.0
HOLD_FACTOR = Decimal("0.3")
MIN_TARGET = 5.0
MAX_TARGET = 25.0


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


@dataclass(frozen=True)
class VisitPlan:
    start: datetime
    reason: str                      # "cheapest" | "fixed" | "started"
    cost_index: Decimal | None       # relative, for display only


def _period_floor(ts: int) -> int:
    return ts - ts % INTERVAL_SECONDS


def plan_start(arrival: datetime, periods: Sequence[Period], now: datetime, *,
               lead_hours: float = LEAD_HOURS, flex_hours: float = FLEX_HOURS) -> VisitPlan:
    """The cheapest heat-up start for an arrival time.

    Candidates run in 15-minute steps from ``flex_hours`` before the
    fixed start up to the fixed start, never before the current period.
    """
    arrival_ts = _period_floor(int(arrival.timestamp()))
    lead = int(lead_hours * 3600)
    latest = _period_floor(arrival_ts - lead)
    now_ts = _period_floor(int(now.timestamp()))
    if latest <= now_ts:
        return VisitPlan(datetime.fromtimestamp(max(latest, now_ts), timezone.utc), "fixed", None)
    earliest = max(_period_floor(latest - int(flex_hours * 3600)), now_ts)
    prices = {p.ts_utc: p.price_c_kwh for p in periods}
    best = None
    for start in range(earliest, latest + 1, INTERVAL_SECONDS):
        heat_end = start + lead
        needed = range(start, arrival_ts, INTERVAL_SECONDS)
        if any(ts not in prices for ts in needed):
            continue
        cost = sum((prices[ts] if ts < heat_end else prices[ts] * HOLD_FACTOR)
                   for ts in needed)
        # Ties go to the later start: less time held warm.
        if best is None or cost <= best[0]:
            best = (cost, start)
    if best is None:
        return VisitPlan(datetime.fromtimestamp(latest, timezone.utc), "fixed", None)
    return VisitPlan(datetime.fromtimestamp(best[1], timezone.utc), "cheapest", best[0])


def visit_target(visit: Visit | None, room: str, now: datetime,
                 plan: VisitPlan | None) -> float | None:
    """The visit's target for a room now, or None (economic plan applies)."""
    if visit is None or visit.ended_at is not None or room not in visit.rooms:
        return None
    start = visit.heat_start or (plan.start if plan else None)
    if start is None or now < start:
        return None
    return visit.target_c


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
    departure_utc TEXT
);
CREATE TABLE IF NOT EXISTS visit_notice (
    visit_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    sent_at_utc TEXT NOT NULL,
    PRIMARY KEY (visit_id, kind)
);
"""
MAX_STAY = timedelta(days=30)


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
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row) -> Visit:
        parse = lambda v: datetime.fromisoformat(v) if v else None
        return Visit(row[0], parse(row[1]), row[2], tuple(json.loads(row[3])), row[4],
                     parse(row[6]), parse(row[7]), parse(row[8]))

    def active(self) -> Visit | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cottage_visit WHERE ended_at_utc IS NULL "
                "ORDER BY id DESC LIMIT 1").fetchone()
        return self._row(row) if row else None

    def start(self, arrival: datetime, target_c: float, rooms: Sequence[str],
              created_by: str, now: datetime, departure: datetime | None = None) -> Visit:
        if arrival.tzinfo is None or (departure is not None and departure.tzinfo is None):
            raise VisitError("arrival time needs a time zone")
        if departure is not None and not arrival < departure <= arrival + MAX_STAY:
            raise VisitError("departure must be after the arrival, within 30 days")
        if arrival <= now:
            raise VisitError("arrival time must be in the future")
        if arrival - now > timedelta(days=14):
            raise VisitError("arrival time must be within 14 days")
        if not MIN_TARGET <= target_c <= MAX_TARGET:
            raise VisitError(f"target must be between {MIN_TARGET:g} and {MAX_TARGET:g} °C")
        if not rooms:
            raise VisitError("choose at least one room")
        with self._lock:
            # A new visit replaces the active one.
            self._conn.execute("UPDATE cottage_visit SET ended_at_utc = ? "
                               "WHERE ended_at_utc IS NULL", (now.isoformat(),))
            self._conn.execute(
                "INSERT INTO cottage_visit (arrival_utc, target_c, rooms_json, created_by, "
                "created_at_utc, departure_utc) VALUES (?, ?, ?, ?, ?, ?)",
                (arrival.astimezone(timezone.utc).isoformat(), float(target_c),
                 json.dumps(list(rooms)), created_by, now.isoformat(),
                 departure.astimezone(timezone.utc).isoformat() if departure else None))
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
