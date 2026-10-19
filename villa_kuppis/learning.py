"""Learned room warm-up and cool-down rates (card F5.1).

Pure functions: stored readings in, a small room model and its held-out
error out. No I/O. The pump service feeds it the home pumps' history; the
cottage readings from the Android app can use the same code later.

The model is a first-order room, per hour::

    dT/dt = a * (T_out - T) + b  - c * cooling + h * heating

* ``a`` (1/h) is how fast the room follows the outdoor temperature,
* ``b`` (°C/h) is the steady drift from sun, people and the building's
  own heat (the home radiators are outside our control),
* ``c`` and ``h`` (°C/h) are what the unit adds when it actively cools or
  heats.

The devices are their own thermostats, so the simulation holds the room at
the setpoint once it gets there. Fitting uses 15-minute steps of the stored
history and ordinary least squares, skipping steps where the unit is just
holding the setpoint (they say nothing about its power), steps whose
controls changed, and modes the model does not cover (dry, automatic).
Without outdoor data the ``a`` term is left out.

The held-out check trains on the days before the most recent complete day,
simulates that day from its first reading with the recorded controls and
outdoor temperature, and compares with the readings. The agreed error is
``MAX_MAE_C``: the day's mean absolute error must not exceed it.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")

STEP_SECONDS = 900          # fitting step
SIM_STEP_SECONDS = 900       # simulation and comparison step
MAX_READING_AGE = 1200       # a reading older than this at a grid point is a gap
MAX_OUTDOOR_GAP = 3 * 3600   # interpolate outdoor only across shorter gaps
DEADBAND_C = 0.5             # room within this of the setpoint: holding
MIN_PASSIVE_STEPS = 96       # one day of 15-minute steps without the unit
MIN_ACTIVE_STEPS = 8         # two hours of active cooling or heating
MIN_DAY_COVERAGE = 0.75      # held-out day needs this share of readings
LEARN_DAYS = 28
MAX_MAE_C = 1.0              # agreed held-out error (mean absolute, °C)


@dataclass(frozen=True)
class Reading:
    """One stored history row of a unit."""

    ts: int
    room_c: float | None
    setpoint_c: float | None
    power: bool | None
    mode: str | None


@dataclass(frozen=True)
class RoomModel:
    """A fitted room, or why there is none yet."""

    ready: bool
    reason: str = ""                  # "", "no_data", "too_little_data"
    loss_per_h: float = 0.0           # a
    drift_c_per_h: float = 0.0        # b
    cool_c_per_h: float | None = None  # c, None until learned
    heat_c_per_h: float | None = None  # h, None until learned
    uses_outdoor: bool = False
    passive_steps: int = 0
    cool_steps: int = 0
    heat_steps: int = 0

    def passive_rate(self, room_c: float, outdoor_c: float | None) -> float:
        """°C/h with the unit off (positive = warms up)."""
        rate = self.drift_c_per_h
        if self.uses_outdoor and outdoor_c is not None:
            rate += self.loss_per_h * (outdoor_c - room_c)
        return rate

    def public(self) -> dict:
        return {"ready": self.ready, "reason": self.reason,
                "loss_per_h": _r(self.loss_per_h, 4),
                "drift_c_per_h": _r(self.drift_c_per_h, 3),
                "cool_c_per_h": _r(self.cool_c_per_h, 3),
                "heat_c_per_h": _r(self.heat_c_per_h, 3),
                "uses_outdoor": self.uses_outdoor,
                "steps": {"passive": self.passive_steps,
                          "cool": self.cool_steps, "heat": self.heat_steps}}


@dataclass(frozen=True)
class Validation:
    """Held-out day result."""

    day: str
    mae_c: float
    max_error_c: float
    points: int
    passed: bool
    predicted: list = field(default_factory=list)   # [(ts, °C)]

    def public(self) -> dict:
        return {"day": self.day, "mae_c": _r(self.mae_c, 2),
                "max_error_c": _r(self.max_error_c, 2),
                "points": self.points, "passed": self.passed,
                "limit_c": MAX_MAE_C}


def _r(value, digits):
    return None if value is None else round(value, digits)


# --- inputs ---------------------------------------------------------------------

class Outdoor:
    """Linear interpolation of hourly outdoor temperatures."""

    def __init__(self, rows: Sequence[tuple[int, float]]):
        rows = sorted(rows)
        self._ts = [int(t) for t, _ in rows]
        self._v = [float(v) for _, v in rows]

    def __bool__(self) -> bool:
        return bool(self._ts)

    def at(self, ts: int) -> float | None:
        i = bisect.bisect_left(self._ts, ts)
        if i < len(self._ts) and self._ts[i] == ts:
            return self._v[i]
        if i == 0 or i == len(self._ts):
            return None
        t0, t1 = self._ts[i - 1], self._ts[i]
        if t1 - t0 > MAX_OUTDOOR_GAP:
            return None
        w = (ts - t0) / (t1 - t0)
        return self._v[i - 1] * (1 - w) + self._v[i] * w

    def last_before(self, ts: int) -> float | None:
        i = bisect.bisect_right(self._ts, ts)
        return self._v[i - 1] if i else None


class _History:
    """The latest reading at or before a time, if recent enough."""

    def __init__(self, readings: Sequence[Reading]):
        self._rows = sorted(readings, key=lambda r: r.ts)
        self._ts = [r.ts for r in self._rows]

    def at(self, ts: int) -> Reading | None:
        i = bisect.bisect_right(self._ts, ts)
        if not i:
            return None
        row = self._rows[i - 1]
        return row if ts - row.ts <= MAX_READING_AGE else None

    @property
    def span(self) -> tuple[int, int] | None:
        return (self._ts[0], self._ts[-1]) if self._ts else None


def _controls(r: Reading):
    return (r.power, r.mode, r.setpoint_c)


def _regime(start: Reading, end_room: float) -> str | None:
    """passive | cool | heat for a fitting step, None to skip it."""
    if start.power is False or start.mode == "Fan":
        return "passive"
    if not start.power or start.setpoint_c is None:
        return None
    t, sp = start.room_c, start.setpoint_c
    if start.mode == "Cool":
        if t >= sp + DEADBAND_C and end_room > sp + DEADBAND_C / 2:
            return "cool"
        if t <= sp - DEADBAND_C and end_room < sp:
            return "passive"   # room below the setpoint: the unit idles
        return None
    if start.mode == "Heat":
        if t <= sp - DEADBAND_C and end_room < sp - DEADBAND_C / 2:
            return "heat"
        if t >= sp + DEADBAND_C and end_room > sp:
            return "passive"
        return None
    return None


# --- fitting --------------------------------------------------------------------

def _solve(rows: list[list[float]], ys: list[float]) -> list[float] | None:
    """Least squares by the normal equations; None when singular."""
    n = len(rows[0])
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(n)] for i in range(n)]
    aty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(n)]
    # Gaussian elimination with partial pivoting.
    m = [ata[i] + [aty[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda k: abs(m[k][col]))
        if abs(m[piv][col]) < 1e-9:
            return None
        m[col], m[piv] = m[piv], m[col]
        for k in range(n):
            if k != col:
                f = m[k][col] / m[col][col]
                for j in range(col, n + 1):
                    m[k][j] -= f * m[col][j]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit(readings: Sequence[Reading], outdoor: Outdoor) -> RoomModel:
    """Fit the room model from a unit's readings and outdoor temperatures."""
    history = _History(readings)
    span = history.span
    if span is None:
        return RoomModel(False, "no_data")
    steps = []   # (regime, room, outdoor or None, rate °C/h)
    hours = STEP_SECONDS / 3600
    t = span[0] - span[0] % STEP_SECONDS + STEP_SECONDS
    while t + STEP_SECONDS <= span[1]:
        t0, t = t, t + STEP_SECONDS
        a, b = history.at(t0), history.at(t)
        if (a is None or b is None or a.room_c is None or b.room_c is None
                or _controls(a) != _controls(b)):
            continue
        regime = _regime(a, b.room_c)
        if regime is None:
            continue
        steps.append((regime, a.room_c, outdoor.at(t0),
                      (b.room_c - a.room_c) / hours))

    with_outdoor = [s for s in steps if s[2] is not None]
    uses_outdoor = sum(1 for s in with_outdoor if s[0] == "passive") >= MIN_PASSIVE_STEPS
    used = with_outdoor if uses_outdoor else steps
    counts = {k: sum(1 for s in used if s[0] == k) for k in ("passive", "cool", "heat")}
    if counts["passive"] < MIN_PASSIVE_STEPS:
        return RoomModel(False, "too_little_data", passive_steps=counts["passive"],
                         cool_steps=counts["cool"], heat_steps=counts["heat"])
    learn_cool = counts["cool"] >= MIN_ACTIVE_STEPS
    learn_heat = counts["heat"] >= MIN_ACTIVE_STEPS
    used = [s for s in used if s[0] == "passive"
            or (s[0] == "cool" and learn_cool) or (s[0] == "heat" and learn_heat)]

    def solve(with_a: bool):
        rows, ys = [], []
        for regime, room, out, rate in used:
            row = [out - room] if with_a else []
            row.append(1.0)
            if learn_cool:
                row.append(-1.0 if regime == "cool" else 0.0)
            if learn_heat:
                row.append(1.0 if regime == "heat" else 0.0)
            rows.append(row)
            ys.append(rate)
        coef = _solve(rows, ys)
        if coef is None:
            return None
        a = coef.pop(0) if with_a else 0.0
        b = coef.pop(0)
        c = coef.pop(0) if learn_cool else None
        h = coef.pop(0) if learn_heat else None
        return a, b, c, h

    result = solve(True) if uses_outdoor else None
    if result is None or result[0] < 0:
        # No outdoor data, or the outdoor term is not identifiable: a room
        # cannot run away from the outdoor temperature, so drop it.
        uses_outdoor = False
        result = solve(False)
    if result is None:
        return RoomModel(False, "too_little_data", passive_steps=counts["passive"])
    a, b, c, h = result
    return RoomModel(
        True, "", loss_per_h=a, drift_c_per_h=b,
        cool_c_per_h=c if c is not None and c > 0 else None,
        heat_c_per_h=h if h is not None and h > 0 else None,
        uses_outdoor=uses_outdoor, passive_steps=counts["passive"],
        cool_steps=counts["cool"], heat_steps=counts["heat"])


# --- simulation -----------------------------------------------------------------

def step(model: RoomModel, room: float, outdoor_c: float | None,
         power: bool | None, mode: str | None, setpoint: float | None,
         seconds: int) -> float:
    """Room temperature after ``seconds`` with the given controls.

    The unit acts as its own thermostat: it cools or heats only towards
    the setpoint and holds the room there.
    """
    hours = seconds / 3600
    free = room + model.passive_rate(room, outdoor_c) * hours
    if not power or setpoint is None:
        return free
    if mode == "Cool" and model.cool_c_per_h is not None and free > setpoint:
        return max(setpoint, free - model.cool_c_per_h * hours)
    if mode == "Heat" and model.heat_c_per_h is not None and free < setpoint:
        return min(setpoint, free + model.heat_c_per_h * hours)
    return free


def simulate(model: RoomModel, start_room: float, start_ts: int, end_ts: int,
             controls, outdoor: Outdoor) -> list[tuple[int, float]]:
    """Predicted room temperature every SIM_STEP_SECONDS from start_ts.

    ``controls(ts)`` returns (power, mode, setpoint) in force at ts.
    Missing outdoor readings reuse the last known value.
    """
    out = [(start_ts, start_room)]
    room, ts = start_room, start_ts
    last_out = outdoor.last_before(ts)
    while ts + SIM_STEP_SECONDS <= end_ts:
        o = outdoor.at(ts)
        last_out = o if o is not None else last_out
        power, mode, sp = controls(ts)
        room = step(model, room, last_out, power, mode, sp, SIM_STEP_SECONDS)
        ts += SIM_STEP_SECONDS
        out.append((ts, room))
    return out


# --- held-out day ---------------------------------------------------------------

def _day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=HELSINKI)
    end = start + timedelta(days=1)
    return (int(start.astimezone(timezone.utc).timestamp()),
            int(end.astimezone(timezone.utc).timestamp()))


def held_out_day(readings: Sequence[Reading], now: datetime) -> date | None:
    """The most recent complete Helsinki day with enough readings."""
    history = _History(readings)
    span = history.span
    if span is None:
        return None
    day = now.astimezone(HELSINKI).date() - timedelta(days=1)
    first = datetime.fromtimestamp(span[0], HELSINKI).date()
    while day >= first:
        s, e = _day_bounds(day)
        points = range(s, e, SIM_STEP_SECONDS)
        seen = sum(1 for t in points
                   if (r := history.at(t)) is not None and r.room_c is not None)
        if seen >= MIN_DAY_COVERAGE * len(points):
            return day
        day -= timedelta(days=1)
    return None


def validate(readings: Sequence[Reading], outdoor: Outdoor,
             now: datetime) -> tuple[RoomModel, Validation | None]:
    """Train before the held-out day and predict it.

    Returns the training model and the result, or (model, None) when no
    day can be held out or the earlier days are not enough to learn from.
    """
    day = held_out_day(readings, now)
    if day is None:
        return RoomModel(False, "too_little_data"), None
    s, e = _day_bounds(day)
    model = fit([r for r in readings if r.ts < s], outdoor)
    if not model.ready:
        return model, None
    history = _History([r for r in readings if r.ts < e])
    first = next((r for r in sorted(readings, key=lambda r: r.ts)
                  if s <= r.ts < e and r.room_c is not None), None)
    if first is None:
        return model, None
    held = {"c": _controls(first)}

    def controls(ts):
        r = history.at(ts)
        if r is not None:
            held["c"] = _controls(r)
        return held["c"]

    predicted = simulate(model, first.room_c, first.ts, e, controls, outdoor)
    errors = []
    for ts, value in predicted[1:]:
        actual = history.at(ts)
        if actual is not None and actual.room_c is not None:
            errors.append(abs(value - actual.room_c))
    if not errors:
        return model, None
    mae = sum(errors) / len(errors)
    return model, Validation(day.isoformat(), mae, max(errors), len(errors),
                             mae <= MAX_MAE_C, predicted)
