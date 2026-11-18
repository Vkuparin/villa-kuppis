"""A cottage room's heat balance from its measured heating power.

Pure functions: hourly room temperatures, hourly heater power and the
outdoor temperature in; the room's heat capacity and heat loss out. No I/O.
``CottageSite.thermal_inputs`` reads the hours from the cottage Home
Assistant's long-term statistics and ``PumpService.cottage_thermal_models``
hands the result to the heating optimizer (``heat_optimizer``).

The room is one heat store, per hour::

    C * dT/dt = P - UA * (T - T_out) + Q

* ``C`` (kWh/°C) is the heat the room stores per degree: its air, furniture
  and the inner layers of its walls and floor,
* ``UA`` (kW/°C) is its heat loss per degree of difference to outdoors,
* ``P`` (kW) is the electric power of its heaters, as the heaters measure it,
* ``Q`` (kW) is every other steady gain or loss (sun, ground, the next room).

Why not ``learning.fit`` (card F5.1): that model learns only from hours in
which the heaters run flat out or stay off, and a room kept at a few
degrees by modulating heaters has almost none, so its optimizer never
started (review 10.11.2026: five days of readings, no model). The power is
measured every hour, so every hour is evidence here.

The fit is a linear regression with a prior, on consecutive hours::

    T[k+1] - T[k] = g * P - a * (T - T_out) + b,   g = 1/C, a = UA/C, b = Q/C

with ``P`` and ``T - T_out`` as the mean of the two hours. The prior is the
room's size: ``C_PER_M2`` and ``UA_PER_M2`` per square metre of floor, the
values the two cottage rooms showed on 5.-9.11.2026 (Mökki 35 m²:
1.3 kWh/°C and 100 W/°C; Kammari 8 m²: 0.25 kWh/°C and 22 W/°C). So a room
has a usable model before its first reading, and the readings take over
within hours. ``Q`` is kept near zero by its prior: while a room is held
at one temperature the loss and a constant gain cannot be told apart.

Hours that fit badly (a fireplace, an open door, people) are left out:
after a first fit, rows whose error is over ``OUTLIER_SIGMAS`` times the
typical error are dropped and the fit is repeated.

A heat-up from cold has its own capacity (``heat_c_kwh_k``). A room is two
stores, not one: the air and the surfaces near the sensor, and the walls
behind them. Holding and cooling hours see both, so their ``C`` is the
heavy one, but a heater flat out in a cold room warms the light one first.
Kammari, 17.-18.11.2026, 1000 W from 6.5 °C: 4.8, 3.6, 2.8, 2.2, 1.7 and
1.4 °C per hour, 22 °C in 5 h 20 min, while the fit on the days before
(two days of slow cooling from 12 °C) said 0.66-0.95 kWh/°C and 14-19 h.
Those full-power hours are also the ones the outlier filter drops. So the
heat-up gain is fitted apart, on the hours with the heaters at
``HARD_SHARE`` of their power or more and the room rising (a heater flat
out that only holds a cold room is the heavy store again), against the loss and gain of the
main fit; without such hours it is the faster of the main fit and the
room's size. Only the visit lead (``cottage_targets.thermal_lead``) uses
it: storing heat for hours, as the optimizer does, fills the walls too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from . import learning

HOUR = 3600
C_PER_M2 = 0.035            # kWh/°C per m² of floor
UA_PER_M2 = 0.0028          # kW/°C per m² of floor
PRIOR_REL_SD = 0.5          # how far a room may be from its size's values
DRIFT_SD_C_H = 0.03         # other gains: at most a few hundredths of a °C/h
NOISE_C_H = 0.3             # typical error of one hour before it is measured
MIN_NOISE_C_H = 0.12
OUTLIER_SIGMAS = 3.0
MIN_ROWS_FOR_NOISE = 12
LIMIT_FACTOR = 5.0          # the result stays within this factor of the prior
LEARN_DAYS = 28
HARD_SHARE = 0.7            # a heat-up hour: heaters at this share of their power
HEATUP_MIN_RISE_C = 0.3     # ... and the room rising at least this much in the hour


@dataclass(frozen=True)
class Prior:
    """What a room of this size is expected to be."""

    c_kwh_k: float
    ua_kw_k: float

    @classmethod
    def for_area(cls, area_m2: float) -> "Prior":
        return cls(C_PER_M2 * area_m2, UA_PER_M2 * area_m2)


@dataclass(frozen=True)
class Thermal:
    """A room's fitted heat balance."""

    c_kwh_k: float            # heat stored per °C
    ua_kw_k: float            # loss per °C to outdoors
    gain_kw: float            # other steady gains (negative: losses)
    hours: int = 0            # measured hours behind the fit (0: the prior alone)
    error_c_h: float | None = None   # typical error of one predicted hour
    heat_c_kwh_k: float | None = None   # heat per °C in a heat-up (None: c_kwh_k)
    heat_hours: int = 0       # full-power hours behind heat_c_kwh_k

    @property
    def heatup_c_kwh_k(self) -> float:
        return self.heat_c_kwh_k or self.c_kwh_k

    @property
    def tau_h(self) -> float:
        """Hours in which a difference to outdoors falls to 37 %."""
        return self.c_kwh_k / self.ua_kw_k

    def room_model(self, heat_kw: float) -> learning.RoomModel:
        """The room as the optimizers read it, with ``heat_kw`` of heaters."""
        return learning.RoomModel(
            True, "", loss_per_h=self.ua_kw_k / self.c_kwh_k,
            drift_c_per_h=self.gain_kw / self.c_kwh_k,
            heat_c_per_h=heat_kw / self.c_kwh_k if heat_kw > 0 else None,
            heatup_c_per_h=heat_kw / self.heatup_c_kwh_k if heat_kw > 0 else None,
            uses_outdoor=True, passive_steps=self.hours, heat_steps=self.hours)

    def hold_kw(self, room_c: float, outdoor_c: float) -> float:
        """Power that keeps the room at ``room_c``."""
        return max(0.0, self.ua_kw_k * (room_c - outdoor_c) - self.gain_kw)

    def public(self) -> dict:
        return {"c_kwh_k": round(self.c_kwh_k, 3), "ua_w_k": round(self.ua_kw_k * 1000, 1),
                "gain_w": round(self.gain_kw * 1000), "tau_h": round(self.tau_h, 1),
                "hours": self.hours,
                "heat_c_kwh_k": round(self.heatup_c_kwh_k, 3), "heat_hours": self.heat_hours,
                "error_c_h": None if self.error_c_h is None else round(self.error_c_h, 2)}


def _solve3(m: list[list[float]], v: list[float]) -> list[float] | None:
    """Solve a 3x3 system by elimination; None when singular."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    n = len(a)
    for col in range(n):
        piv = max(range(col, n), key=lambda k: abs(a[k][col]))
        if abs(a[piv][col]) < 1e-12:
            return None
        a[col], a[piv] = a[piv], a[col]
        for k in range(n):
            if k != col:
                f = a[k][col] / a[col][col]
                for j in range(col, n + 1):
                    a[k][j] -= f * a[col][j]
    return [a[i][n] / a[i][i] for i in range(n)]


def rows(temps: Mapping[int, float], power: Mapping[int, float],
         outdoor: learning.Outdoor) -> list[tuple[float, float, float]]:
    """(kW, room minus outdoor, °C change) for each pair of consecutive
    hours that has a room temperature, a heater power and an outdoor
    temperature. ``temps`` and ``power`` map an hour's start to its mean."""
    out = []
    for ts in sorted(temps):
        nxt = ts + HOUR
        if nxt not in temps or ts not in power or nxt not in power:
            continue
        o0, o1 = outdoor.at(ts + HOUR // 2), outdoor.at(nxt + HOUR // 2)
        if o0 is None or o1 is None:
            continue
        out.append(((power[ts] + power[nxt]) / 2,
                    (temps[ts] + temps[nxt]) / 2 - (o0 + o1) / 2,
                    temps[nxt] - temps[ts]))
    return out


def _posterior(data: Sequence[tuple[float, float, float]], mean: Sequence[float],
               sd: Sequence[float], noise: float) -> list[float]:
    """The most likely (g, a, b) given the rows and the prior."""
    w = 1.0 / (noise * noise)
    m = [[0.0] * 3 for _ in range(3)]
    v = [0.0] * 3
    for kw, diff, change in data:
        x = (kw, -diff, 1.0)
        for i in range(3):
            v[i] += w * x[i] * change
            for j in range(3):
                m[i][j] += w * x[i] * x[j]
    for i in range(3):
        precision = 1.0 / (sd[i] * sd[i])
        m[i][i] += precision
        v[i] += precision * mean[i]
    return _solve3(m, v) or list(mean)


def _heatup_gain(data, a: float, b: float, mean: float, noise: float,
                 heat_kw: float | None) -> tuple[float, int]:
    """(°C/h per kW in a heat-up, hours behind it) from the rows with the
    heaters at ``HARD_SHARE`` of ``heat_kw`` (of the most measured without
    it) and the room rising, weighed against ``mean``."""
    top = heat_kw if heat_kw and heat_kw > 0 else max((kw for kw, _d, _c in data), default=0.0)
    hard = [(kw, change + a * diff - b) for kw, diff, change in data
            if top > 0 and kw >= HARD_SHARE * top and change >= HEATUP_MIN_RISE_C]
    precision = 1.0 / (PRIOR_REL_SD * mean) ** 2
    w = 1.0 / (noise * noise)
    num = precision * mean + w * sum(kw * rise for kw, rise in hard)
    den = precision + w * sum(kw * kw for kw, _rise in hard)
    return num / den, len(hard)


def estimate(temps: Mapping[int, float], power: Mapping[int, float],
             outdoor: learning.Outdoor, prior: Prior,
             heat_kw: float | None = None) -> Thermal:
    """The room's heat balance from its hours; the prior alone without any.
    ``heat_kw`` is the power of the room's heaters, for the heat-up hours."""
    g0, a0 = 1.0 / prior.c_kwh_k, prior.ua_kw_k / prior.c_kwh_k
    mean = (g0, a0, 0.0)
    sd = (PRIOR_REL_SD * g0, PRIOR_REL_SD * a0, DRIFT_SD_C_H)
    data = rows(temps, power, outdoor)
    if not data:
        return Thermal(prior.c_kwh_k, prior.ua_kw_k, 0.0)

    def errors(theta, used):
        return [change - (theta[0] * kw - theta[1] * diff + theta[2])
                for kw, diff, change in used]

    def spread(values) -> float:
        return (sum(e * e for e in values) / len(values)) ** 0.5

    theta = _posterior(data, mean, sd, NOISE_C_H)
    noise = NOISE_C_H
    used = data
    if len(data) >= MIN_ROWS_FOR_NOISE:
        first = errors(theta, data)
        # The median error is not moved by the few hours that do not fit.
        typical = sorted(abs(e) for e in first)[len(first) // 2] * 1.4826
        noise = max(MIN_NOISE_C_H, typical)
        used = [row for row, e in zip(data, first) if abs(e) <= OUTLIER_SIGMAS * noise]
        theta = _posterior(used, mean, sd, noise)
    g = min(max(theta[0], g0 / LIMIT_FACTOR), g0 * LIMIT_FACTOR)
    a = min(max(theta[1], a0 / LIMIT_FACTOR), a0 * LIMIT_FACTOR)
    c = 1.0 / g
    gh, hard = _heatup_gain(data, a, theta[2], max(g, g0), noise, heat_kw)
    gh = min(max(gh, g0 / LIMIT_FACTOR), g0 * LIMIT_FACTOR)
    return Thermal(c, a * c, theta[2] * c, len(used), spread(errors((g, a, theta[2]), used)),
                   1.0 / gh, hard)
