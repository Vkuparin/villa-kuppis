"""Cooling optimizer for the home pumps (card F5.4).

Pure functions: prices, a learned room (learning.RoomModel), the outdoor
forecast and the room's limits in; a target or "off" per 15-minute period
out. No I/O. ``cooling.CoolingAutomation`` applies the result with the same
rules as the fixed thresholds.

The optimizer precools in cheap periods, lets the room warm towards the
maximum in expensive ones and switches the unit off when that is cheapest:

* The room follows the learned first-order model; the unit is its own
  thermostat and works only while the room is above the setpoint. The
  share of a period it works, at ``COOL_KW``, is its energy; a unit that
  is on also runs its fan at ``FAN_KW``. These nominal powers only rank
  plans against each other, so the estimated costs are relative.
* Setpoints run from the precooling temperature to the maximum in
  ``SETPOINT_STEP_C`` steps, so the unit never cools below the precooling
  temperature. "Off" is allowed only where the fixed rule allows it: the
  room may switch the pump (``cool_power``) and the price is at or above
  the high price. Once off, the unit stays off for the minimum off time.
* A room above the maximum costs ``OVER_MAX_PENALTY`` cents per °C·h, far
  more than any energy, so the plan stays at or below the maximum whenever
  the unit can hold it.
* The cold left in the room at the end of the known prices is valued at
  the median price of the horizon, so the plan neither spends it nor
  hoards it just before the prices run out.

Dynamic programming over a 0.1 °C room grid finds the cheapest plan; the
chosen plan is then simulated without the grid. The same simulation runs
the fixed-threshold plan (card F4.1), and the optimizer returns the fixed
plan whenever its own plan does not score at least as well. So it never
costs more than the fixed thresholds under the learned model.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Callable, Sequence

from . import learning

PERIOD_SECONDS = 900
COOL_KW = 1.0                # nominal electric power while the compressor runs
FAN_KW = 0.02                # nominal fan power while the unit is on
GRID_C = 0.1
SETPOINT_STEP_C = 0.5
OVER_MAX_PENALTY = 1000.0    # cents per °C·h above the maximum


@dataclass(frozen=True)
class Limits:
    """One room's cooling settings."""

    max_c: float
    precool_c: float
    low_c_kwh: float
    high_c_kwh: float
    off_allowed: bool
    min_off_periods: int


@dataclass(frozen=True)
class Step:
    """The decision for one period and the predicted room at its end."""

    ts_utc: int
    price_c_kwh: float
    off: bool
    target_c: float
    room_c: float
    cooling: float           # share of the period the compressor works


@dataclass(frozen=True)
class Result:
    steps: list[Step]
    cost_c: float            # energy cost at the nominal powers
    over_max_c_h: float      # °C·h above the maximum
    end_c: float

    @property
    def max_room_c(self) -> float:
        return max(s.room_c for s in self.steps)

    def off_runs(self) -> list[int]:
        """Lengths of the off stretches, in periods."""
        runs, n = [], 0
        for s in self.steps:
            if s.off:
                n += 1
            elif n:
                runs.append(n)
                n = 0
        if n:
            runs.append(n)
        return runs


@dataclass(frozen=True)
class Plan:
    """The plan to apply, with both plans' scores for the app."""

    steps: list[Step]
    optimized: bool          # False: the fixed plan scored at least as well
    cost_c: float
    fixed_cost_c: float
    over_max_c_h: float
    fixed_over_max_c_h: float


def period_step(model: learning.RoomModel, room: float,
                outdoor_c: float | None, off: bool, setpoint: float,
                seconds: int = PERIOD_SECONDS) -> tuple[float, float]:
    """(room at the end, share of the period cooling); as learning.step."""
    hours = seconds / 3600
    free = room + model.passive_rate(room, outdoor_c) * hours
    if off or free <= setpoint:
        return free, 0.0
    capacity = model.cool_c_per_h * hours
    drop = min(free - setpoint, capacity)
    return free - drop, drop / capacity


def setpoints(limits: Limits) -> list[float]:
    """Candidate setpoints, warmest first."""
    out, sp = [], limits.max_c
    while sp > limits.precool_c + 1e-9:
        out.append(round(sp, 2))
        sp -= SETPOINT_STEP_C
    out.append(limits.precool_c)
    return out


def _energy_cost(price: float, off: bool, cooling: float) -> float:
    hours = PERIOD_SECONDS / 3600
    return (cooling * COOL_KW + (0.0 if off else FAN_KW)) * hours * price


def _stage_cost(price: float, off: bool, cooling: float, room: float,
                limits: Limits) -> float:
    over = max(0.0, room - limits.max_c) * PERIOD_SECONDS / 3600
    return _energy_cost(price, off, cooling) + over * OVER_MAX_PENALTY


def _run(model, start_room: float, start_off: int, prices: Sequence[tuple[int, float]],
         outdoors: Sequence[float | None], limits: Limits,
         choose: Callable[[int, float, int], tuple[bool, float]]) -> Result:
    """Simulate a policy; ``choose(i, room, off_periods)`` -> (off, setpoint)."""
    hours = PERIOD_SECONDS / 3600
    steps, room, k, cost, over = [], start_room, start_off, 0.0, 0.0
    for i, (ts, price) in enumerate(prices):
        off, sp = choose(i, room, k)
        room, share = period_step(model, room, outdoors[i], off, sp)
        cost += _energy_cost(price, off, share)
        over += max(0.0, room - limits.max_c) * hours
        k = k + 1 if off else 0
        steps.append(Step(ts, price, off, sp, room, share))
    return Result(steps, cost, over, room)


def fixed_policy(fixed: Sequence[tuple[bool, float]], limits: Limits):
    """The fixed-threshold rules of cooling.CoolingAutomation as a policy.

    ``fixed`` is the fixed plan's (off, target) per period. A unit off in
    an expensive stretch comes back once the minimum off time is over and
    the room has reached the maximum, and then holds the maximum until the
    stretch ends.
    """
    need = max(1, limits.min_off_periods)
    held = {"on": False}

    def choose(i, room, k):
        off, target = fixed[i]
        if 0 < k < need:
            return True, target
        if not off:
            held["on"] = False
            return False, target
        if held["on"]:
            return False, target
        if k and room >= limits.max_c:
            held["on"] = True
            return False, target
        return True, target
    return choose


def _score(result: Result, ref_end: float, cold_value: float) -> float:
    return (result.cost_c + result.over_max_c_h * OVER_MAX_PENALTY
            + (result.end_c - ref_end) * cold_value)


def optimize(model: learning.RoomModel, start_room: float, start_off: int,
             prices: Sequence[tuple[int, float]],
             outdoor: learning.Outdoor, limits: Limits,
             fixed: Sequence[tuple[bool, float]]) -> Plan | None:
    """Cheapest plan from now to the end of the known prices.

    ``prices`` are (ts, c/kWh) from the current period on; ``start_off`` is
    how many periods the unit has been off by the automation (0 when on);
    ``fixed`` is the fixed plan's (off, target) for the same periods.
    Returns None when the room's cooling rate is not learned yet.
    """
    if (not prices or not model.ready or not model.cool_c_per_h
            or start_room is None):
        return None
    outdoors = []
    for ts, _ in prices:
        value = outdoor.at(ts)
        outdoors.append(value if value is not None else outdoor.last_before(ts))
    n, need = len(prices), max(1, limits.min_off_periods)
    start_off = min(start_off, need)

    base = _run(model, start_room, start_off, prices, outdoors, limits,
                fixed_policy(fixed, limits))
    ref_end = base.end_c
    cold_value = COOL_KW * median(p for _, p in prices) / model.cool_c_per_h

    lo = min(limits.precool_c, start_room) - 3.0
    hi = max(limits.max_c, start_room) + 5.0
    bins = int(round((hi - lo) / GRID_C)) + 1
    temps = [lo + b * GRID_C for b in range(bins)]

    def index(t: float) -> int:
        return min(bins - 1, max(0, int(round((t - lo) / GRID_C))))

    sps = setpoints(limits)
    # value[k][b]: cheapest score from period i on, k periods off so far
    # (k == need: free to switch on).
    value = [[(t - ref_end) * cold_value for t in temps] for _ in range(need + 1)]
    tables = [None] * n
    for i in range(n - 1, -1, -1):
        price = prices[i][1]
        may_off = limits.off_allowed and price >= limits.high_c_kwh
        out_c = outdoors[i]
        on_best = [0.0] * bins
        off_next = [0] * bins
        off_cost = [0.0] * bins
        for b, t in enumerate(temps):
            best = None
            for sp in sps:
                room, share = period_step(model, t, out_c, False, sp)
                c = _stage_cost(price, False, share, room, limits) + value[0][index(room)]
                if best is None or c < best:
                    best = c
            on_best[b] = best
            room, _ = period_step(model, t, out_c, True, 0.0)
            off_next[b] = index(room)
            off_cost[b] = _stage_cost(price, True, 0.0, room, limits)
        new = []
        for k in range(need + 1):
            row = []
            for b in range(bins):
                nk = min(k + 1, need)
                off_total = off_cost[b] + value[nk][off_next[b]]
                if 0 < k < need:
                    row.append(off_total)          # minimum off time
                elif may_off:
                    row.append(min(on_best[b], off_total))
                else:
                    row.append(on_best[b])
            new.append(row)
        tables[i] = value                          # value after period i
        value = new

    def choose(i, room, k):
        price = prices[i][1]
        after = tables[i]
        nk = min(k + 1, need) if k else 1
        off_room, _ = period_step(model, room, outdoors[i], True, 0.0)
        off_total = (_stage_cost(price, True, 0.0, off_room, limits)
                     + after[nk][index(off_room)])
        if 0 < k < need:
            return True, limits.max_c
        best, pick = None, None
        for sp in sps:
            r, share = period_step(model, room, outdoors[i], False, sp)
            c = _stage_cost(price, False, share, r, limits) + after[0][index(r)]
            if best is None or c < best - 1e-12:
                best, pick = c, sp
        if limits.off_allowed and price >= limits.high_c_kwh \
                and off_total < best - 1e-12:
            return True, limits.max_c
        return False, pick

    opt = _run(model, start_room, start_off, prices, outdoors,
               limits, choose)
    use_opt = _score(opt, ref_end, cold_value) <= _score(base, ref_end, cold_value) + 1e-9
    chosen = opt if use_opt else base
    # The cost counts the cold left at the end against the fixed plan's.
    return Plan(chosen.steps, use_opt,
                chosen.cost_c + (chosen.end_c - ref_end) * cold_value,
                base.cost_c, chosen.over_max_c_h, base.over_max_c_h)
