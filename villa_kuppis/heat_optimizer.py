"""Smart maintenance heating for the cottage rooms (card F5.2).

Pure functions: prices, a learned room (learning.RoomModel), the outdoor
forecast and the room's storage band in; a room target per 15-minute period
out. No I/O. ``cottage_targets`` sends the target of the current period like
the fixed thresholds' target.

The optimizer stores heat in cheap periods and coasts through expensive
ones, keeping the room between the minimum and maximum storage temperature:

* The room follows the learned first-order model. The cottage controller
  and the Mill heaters are the room's thermostat at the target: they heat
  only while the room is below it. The share of a period they heat, at
  ``HEAT_KW``, is its energy; this nominal power only ranks plans against
  each other, so the estimated costs are relative.
* Targets run from the minimum to the maximum in ``TARGET_STEP_C`` steps.
* A room below the minimum costs ``UNDER_MIN_PENALTY`` cents per °C·h, far
  more than any energy, so the plan keeps the minimum whenever the heaters
  can.
* The heat left in the room at the end of the known prices is valued at the
  median price of the horizon, so the plan neither spends it nor stores it
  just before the prices run out.

Dynamic programming over a 0.1 °C room grid, read between its points,
finds the cheapest plan; the chosen plan is then simulated without the
grid. The same simulation runs
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
HEAT_KW = 1.0                # nominal electric power while the heaters work
GRID_C = 0.1
TARGET_STEP_C = 0.5
UNDER_MIN_PENALTY = 1000.0   # cents per °C·h below the minimum


@dataclass(frozen=True)
class HeatLimits:
    """One room's storage band."""

    min_c: float
    max_c: float


@dataclass(frozen=True)
class HeatStep:
    """The target for one period and the predicted room at its end."""

    ts_utc: int
    price_c_kwh: float
    target_c: float
    room_c: float
    heating: float           # share of the period the heaters work


@dataclass(frozen=True)
class HeatResult:
    steps: list[HeatStep]
    cost_c: float            # energy cost at the nominal power
    under_min_c_h: float     # °C·h below the minimum
    end_c: float


@dataclass(frozen=True)
class HeatPlan:
    """The plan to apply, with both plans' scores for the app."""

    steps: list[HeatStep]
    optimized: bool          # False: the fixed plan scored at least as well
    cost_c: float
    fixed_cost_c: float
    under_min_c_h: float
    fixed_under_min_c_h: float


def period_step(model: learning.RoomModel, room: float, outdoor_c: float | None,
                target: float, seconds: int = PERIOD_SECONDS) -> tuple[float, float]:
    """(room at the end, share of the period heating); as learning.step."""
    hours = seconds / 3600
    free = room + model.passive_rate(room, outdoor_c) * hours
    if free >= target:
        return free, 0.0
    capacity = model.heat_c_per_h * hours
    rise = min(target - free, capacity)
    return free + rise, rise / capacity


def targets(limits: HeatLimits) -> list[float]:
    """Candidate targets, coldest first."""
    out, t = [], limits.min_c
    while t < limits.max_c - 1e-9:
        out.append(round(t, 2))
        t += TARGET_STEP_C
    out.append(limits.max_c)
    return out


def _energy_cost(price: float, heating: float) -> float:
    return heating * HEAT_KW * PERIOD_SECONDS / 3600 * price


def _stage_cost(price: float, heating: float, room: float, limits: HeatLimits) -> float:
    under = max(0.0, limits.min_c - room) * PERIOD_SECONDS / 3600
    return _energy_cost(price, heating) + under * UNDER_MIN_PENALTY


def _run(model, start_room: float, prices: Sequence[tuple[int, float]],
         outdoors: Sequence[float | None], limits: HeatLimits,
         choose: Callable[[int, float], float]) -> HeatResult:
    """Simulate a policy; ``choose(i, room)`` -> target."""
    hours = PERIOD_SECONDS / 3600
    steps, room, cost, under = [], start_room, 0.0, 0.0
    for i, (ts, price) in enumerate(prices):
        target = choose(i, room)
        room, share = period_step(model, room, outdoors[i], target)
        cost += _energy_cost(price, share)
        under += max(0.0, limits.min_c - room) * hours
        steps.append(HeatStep(ts, price, target, room, share))
    return HeatResult(steps, cost, under, room)


def _score(result: HeatResult, ref_end: float, heat_value: float) -> float:
    return (result.cost_c + result.under_min_c_h * UNDER_MIN_PENALTY
            + (ref_end - result.end_c) * heat_value)


def optimize_heating(model: learning.RoomModel, start_room: float | None,
                     prices: Sequence[tuple[int, float]], outdoor: learning.Outdoor,
                     limits: HeatLimits, fixed: Sequence[float]) -> HeatPlan | None:
    """Cheapest plan from now to the end of the known prices.

    ``prices`` are (ts, c/kWh) from the current period on and ``fixed`` the
    fixed thresholds' target for the same periods. Returns None when the
    room's heating rate is not learned yet or its temperature is unknown.
    """
    if (not prices or not model.ready or not model.heat_c_per_h
            or start_room is None):
        return None
    outdoors = []
    for ts, _ in prices:
        value = outdoor.at(ts)
        outdoors.append(value if value is not None else outdoor.last_before(ts))
    n = len(prices)

    base = _run(model, start_room, prices, outdoors, limits, lambda i, _room: fixed[i])
    ref_end = base.end_c
    heat_value = HEAT_KW * median(p for _, p in prices) / model.heat_c_per_h

    lo = min(limits.min_c, start_room) - 5.0
    hi = max(limits.max_c, start_room) + 3.0
    bins = int(round((hi - lo) / GRID_C)) + 1
    temps = [lo + b * GRID_C for b in range(bins)]

    def at(table: list[float], t: float) -> float:
        """The table between its two nearest bins (no rounding bias)."""
        x = min(bins - 1.0, max(0.0, (t - lo) / GRID_C))
        b = min(bins - 2, int(x))
        w = x - b
        return table[b] * (1 - w) + table[b + 1] * w

    candidates = targets(limits)
    # value[b]: cheapest score from period i on with the room in bin b.
    value = [(ref_end - t) * heat_value for t in temps]
    tables = [None] * n
    for i in range(n - 1, -1, -1):
        price, out_c = prices[i][1], outdoors[i]
        new = []
        for t in temps:
            best = None
            for target in candidates:
                room, share = period_step(model, t, out_c, target)
                c = _stage_cost(price, share, room, limits) + at(value, room)
                if best is None or c < best:
                    best = c
            new.append(best)
        tables[i] = value                          # value after period i
        value = new

    def choose(i, room):
        # Coldest first, and a tie keeps the colder target: stored heat leaks.
        best, pick = None, None
        for target in candidates:
            r, share = period_step(model, room, outdoors[i], target)
            c = _stage_cost(prices[i][1], share, r, limits) + at(tables[i], r)
            if best is None or c < best - 1e-9:
                best, pick = c, target
        return pick

    opt = _run(model, start_room, prices, outdoors, limits, choose)
    use_opt = _score(opt, ref_end, heat_value) <= _score(base, ref_end, heat_value) + 1e-9
    chosen = opt if use_opt else base
    # The cost counts the heat left at the end against the fixed plan's.
    return HeatPlan(chosen.steps, use_opt,
                    chosen.cost_c + (ref_end - chosen.end_c) * heat_value,
                    base.cost_c, chosen.under_min_c_h, base.under_min_c_h)
