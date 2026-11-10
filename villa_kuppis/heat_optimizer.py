"""Smart maintenance heating for the cottage rooms (card F5.2).

Pure functions: prices, a learned room (learning.RoomModel), the outdoor
forecast and the room's storage band in; a room target per 15-minute period
out. No I/O. ``cottage_targets`` sends the target of the current period like
the fixed thresholds' target.

The optimizer stores heat in cheap periods and coasts through expensive
ones, keeping the room between the minimum and maximum storage temperature:

* The room follows a first-order model: the room's measured heat balance
  (``thermal``) for the maintenance plan, the learned rates
  (``learning``) for a visit heat-up. The cottage controller and the Mill
  heaters are the room's thermostat at the target: they heat only while
  the room is below it. The share of a period they heat, at ``heat_kw``
  (the heaters' power; ``HEAT_KW`` when unknown, which only ranks plans
  against each other), is its energy.
* Targets run from the minimum to the maximum in ``TARGET_STEP_C`` steps.
* A room below the minimum costs ``UNDER_MIN_PENALTY`` cents per °C·h, far
  more than any energy, so the plan keeps the minimum whenever the heaters
  can.
* The heat left in the room at the end of the known prices is valued at the
  median price of the horizon, so the plan neither spends it nor stores it
  just before the prices run out.
* Heat stored above what the floor needs is charged ``store_margin`` more
  than its price, so the plan stores only when the saving is clear of the
  model's error.

Dynamic programming over a 0.1 °C room grid, read between its points,
finds the cheapest plan; the chosen plan is then simulated without the
grid. The same simulation runs
the fixed-threshold plan (card F4.1), and the optimizer returns the fixed
plan whenever its own plan does not score at least as well. So it never
costs more than the baseline under the model. The room's floor does not
depend on the model at all: the target is never below the minimum, and the
heaters hold the target whatever the plan expected.
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
STORE_MARGIN = 0.05          # stored heat must pay back this much more than it costs


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


def _energy_cost(price: float, heating: float, heat_kw: float = HEAT_KW) -> float:
    return heating * heat_kw * PERIOD_SECONDS / 3600 * price


def _stage_cost(price: float, heating: float, room: float, floor: float,
                heat_kw: float = HEAT_KW) -> float:
    under = max(0.0, floor - room) * PERIOD_SECONDS / 3600
    return _energy_cost(price, heating, heat_kw) + under * UNDER_MIN_PENALTY


def _run(model, start_room: float, prices: Sequence[tuple[int, float]],
         outdoors: Sequence[float | None], floors: Sequence[float],
         choose: Callable[[int, float], float], heat_kw: float = HEAT_KW) -> HeatResult:
    """Simulate a policy; ``choose(i, room)`` -> target."""
    hours = PERIOD_SECONDS / 3600
    steps, room, cost, under = [], start_room, 0.0, 0.0
    for i, (ts, price) in enumerate(prices):
        target = choose(i, room)
        room, share = period_step(model, room, outdoors[i], target)
        cost += _energy_cost(price, share, heat_kw)
        under += max(0.0, floors[i] - room) * hours
        steps.append(HeatStep(ts, price, target, room, share))
    return HeatResult(steps, cost, under, room)


def _score(result: HeatResult, ref_end: float, heat_value: float) -> float:
    return (result.cost_c + result.under_min_c_h * UNDER_MIN_PENALTY
            + (ref_end - result.end_c) * heat_value)


def optimize_heating(model: learning.RoomModel, start_room: float | None,
                     prices: Sequence[tuple[int, float]], outdoor: learning.Outdoor,
                     limits: HeatLimits, fixed: Sequence[float],
                     visit: tuple[int, float] | None = None, *,
                     heat_kw: float = HEAT_KW,
                     store_margin: float = 0.0) -> HeatPlan | None:
    """Cheapest plan from now to the end of the known prices.

    ``prices`` are (ts, c/kWh) from the current period on and ``fixed`` the
    baseline's target for the same periods (the fixed thresholds, or the
    fixed-lead visit plan). ``visit`` is (arrival ts, target): from the
    period ending at the arrival on, the target is the visit's and the room counts as too
    cold below it, so the plan reaches it by the arrival (card F5.3); before
    it the plan may also store heat up to the visit target. ``heat_kw`` is
    the heaters' electric power, so the costs are cents; ``store_margin``
    is the extra charged for heat stored above the floor's need. Returns
    None when the room's heating rate is not known, its temperature is
    unknown, or its model needs an outdoor temperature and there is none.
    """
    if (not prices or not model.ready or not model.heat_c_per_h
            or start_room is None):
        return None
    outdoors = []
    for ts, _ in prices:
        value = outdoor.at(ts)
        outdoors.append(value if value is not None else outdoor.last_before(ts))
    if model.uses_outdoor and any(value is None for value in outdoors):
        return None    # without the weather the room would seem to lose nothing
    n = len(prices)
    top = max(limits.max_c, visit[1]) if visit else limits.max_c
    storing = targets(HeatLimits(limits.min_c, top))
    # Warm at the arrival: the period ending at it already holds the target.
    arrived = [visit is not None and ts + PERIOD_SECONDS >= visit[0] for ts, _ in prices]
    floors = [visit[1] if a else limits.min_c for a in arrived]
    choices = [[visit[1]] if a else storing for a in arrived]

    base = _run(model, start_room, prices, outdoors, floors, lambda i, _room: fixed[i],
                heat_kw)
    ref_end = base.end_c
    heat_value = heat_kw * median(p for _, p in prices) / model.heat_c_per_h
    hours = PERIOD_SECONDS / 3600

    def cost(i: int, room: float, share: float, need: float) -> float:
        """Stage cost of ending period i at ``room`` after heating ``share``
        of it, when the floor alone needed ``need``."""
        price = prices[i][1]
        stored = max(0.0, share - need) * heat_kw * hours * abs(price) * store_margin
        return _stage_cost(price, share, room, floors[i], heat_kw) + stored

    lo = min(limits.min_c, start_room) - 5.0
    hi = max(top, start_room) + 3.0
    bins = int(round((hi - lo) / GRID_C)) + 1
    temps = [lo + b * GRID_C for b in range(bins)]

    def at(table: list[float], t: float) -> float:
        """The table between its two nearest bins (no rounding bias)."""
        x = min(bins - 1.0, max(0.0, (t - lo) / GRID_C))
        b = min(bins - 2, int(x))
        w = x - b
        return table[b] * (1 - w) + table[b + 1] * w

    # value[b]: cheapest score from period i on with the room in bin b.
    value = [(ref_end - t) * heat_value for t in temps]
    tables = [None] * n
    for i in range(n - 1, -1, -1):
        price, out_c = prices[i][1], outdoors[i]
        new = []
        for t in temps:
            best = None
            need = period_step(model, t, out_c, floors[i])[1] if store_margin else 0.0
            for target in choices[i]:
                room, share = period_step(model, t, out_c, target)
                c = cost(i, room, share, need) + at(value, room)
                if best is None or c < best:
                    best = c
            new.append(best)
        tables[i] = value                          # value after period i
        value = new

    def choose(i, room):
        # Coldest first, and a tie keeps the colder target: stored heat leaks.
        best, pick = None, None
        need = period_step(model, room, outdoors[i], floors[i])[1] if store_margin else 0.0
        for target in choices[i]:
            r, share = period_step(model, room, outdoors[i], target)
            c = cost(i, r, share, need) + at(tables[i], r)
            if best is None or c < best - 1e-9:
                best, pick = c, target
        return pick

    opt = _run(model, start_room, prices, outdoors, floors, choose, heat_kw)
    use_opt = _score(opt, ref_end, heat_value) <= _score(base, ref_end, heat_value) + 1e-9
    chosen = opt if use_opt else base
    # The cost counts the heat left at the end against the fixed plan's.
    return HeatPlan(chosen.steps, use_opt,
                    chosen.cost_c + (ref_end - chosen.end_c) * heat_value,
                    base.cost_c, chosen.under_min_c_h, base.under_min_c_h)
