"""A room's heat balance from its measured heating power (thermal.py).

Synthetic rooms: a known heat capacity and loss, a thermostat or a price
plan driving the heaters, and the hourly means Home Assistant would keep.
"""

from __future__ import annotations

import math
import random
import unittest
from dataclasses import replace

from villa_kuppis import thermal
from villa_kuppis.learning import Outdoor

HOUR = 3600
T0 = 1_793_664_000      # a whole hour


def simulate(c, ua, hours, *, target, heat_kw, outdoor_c, gain=0.0, noise=0.0, seed=1,
             extra=None):
    """Hourly mean room °C and heater kW of a room with a thermostat.

    ``target(h)`` and ``outdoor_c(h)`` give the hour's values; ``extra(h)``
    is unmeasured heat (kW), such as a fireplace.
    """
    rng = random.Random(seed)
    room, temps, power = target(0), {}, {}
    steps = 60
    for h in range(hours):
        t_sum = p_sum = 0.0
        for _ in range(steps):
            kw = heat_kw if room < target(h) else 0.0
            other = gain + (extra(h) if extra else 0.0)
            room += (kw + other - ua * (room - outdoor_c(h))) / c / steps
            t_sum += room
            p_sum += kw
        temps[T0 + h * HOUR] = t_sum / steps + rng.gauss(0, noise)
        power[T0 + h * HOUR] = p_sum / steps
    return temps, power


def weather(hours, fn):
    return Outdoor([(T0 + h * HOUR, fn(h)) for h in range(-1, hours + 2)])


class PriorTests(unittest.TestCase):
    def test_a_room_without_hours_gets_its_sizes_values(self):
        fit = thermal.estimate({}, {}, Outdoor([]), thermal.Prior.for_area(35.0))
        self.assertAlmostEqual(fit.c_kwh_k, 1.225)
        self.assertAlmostEqual(fit.ua_kw_k, 0.098)
        self.assertEqual((fit.hours, fit.gain_kw, fit.error_c_h), (0, 0.0, None))
        self.assertAlmostEqual(fit.tau_h, 12.5)
        # 5 °C against -10 °C outdoors: 1.47 kW of the room's 2.5 kW.
        self.assertAlmostEqual(fit.hold_kw(5.0, -10.0), 1.47)

    def test_room_model_is_what_the_optimizer_reads(self):
        model = thermal.Prior.for_area(8.0)
        fit = thermal.Thermal(model.c_kwh_k, model.ua_kw_k, 0.0)
        room = fit.room_model(1.0)
        self.assertTrue(room.ready and room.uses_outdoor)
        self.assertAlmostEqual(room.loss_per_h, 0.08)
        self.assertAlmostEqual(room.heat_c_per_h, 1 / 0.28)
        self.assertAlmostEqual(room.passive_rate(5.0, -5.0), -0.8)
        self.assertIsNone(fit.room_model(0.0).heat_c_per_h)    # no heater to plan with


class EstimateTests(unittest.TestCase):
    def test_a_week_of_price_driven_heating_finds_the_room(self):
        # A room twice as leaky and a third heavier than its size suggests.
        prior = thermal.Prior.for_area(35.0)
        c, ua = prior.c_kwh_k * 1.35, prior.ua_kw_k * 2.0
        out = lambda h: 2.0 + 5.0 * math.sin(h / 24 * 2 * math.pi)
        temps, power = simulate(c, ua, 168, target=lambda h: 8.0 if h % 24 < 6 else 5.0,
                                heat_kw=2.5, outdoor_c=out, noise=0.03)
        fit = thermal.estimate(temps, power, weather(168, out), prior)
        self.assertAlmostEqual(fit.ua_kw_k / ua, 1.0, delta=0.15)
        self.assertAlmostEqual(fit.c_kwh_k / c, 1.0, delta=0.25)
        self.assertGreater(fit.hours, 150)
        self.assertLess(fit.error_c_h, 0.2)

    def test_a_room_held_at_one_temperature_still_gives_its_loss(self):
        # No temperature changes at all: the regimes of learning.fit have
        # nothing here, the measured power still says what the room loses.
        prior = thermal.Prior.for_area(8.0)
        ua = prior.ua_kw_k * 1.6
        out = lambda h: -3.0 + 4.0 * math.sin(h / 24 * 2 * math.pi)
        temps, power = simulate(prior.c_kwh_k, ua, 96, target=lambda h: 5.0, heat_kw=1.0,
                                outdoor_c=out)
        fit = thermal.estimate(temps, power, weather(96, out), prior)
        self.assertAlmostEqual(fit.ua_kw_k / ua, 1.0, delta=0.2)

    def test_a_few_hours_move_from_the_prior_toward_the_room(self):
        prior = thermal.Prior.for_area(35.0)
        out = lambda h: 0.0
        temps, power = simulate(prior.c_kwh_k * 3, prior.ua_kw_k * 3, 4, target=lambda h: 5.0,
                                heat_kw=2.5, outdoor_c=out)
        fit = thermal.estimate(temps, power, weather(4, out), prior)
        self.assertGreater(fit.ua_kw_k, prior.ua_kw_k)
        self.assertLess(fit.ua_kw_k, prior.ua_kw_k * 3.0)
        self.assertEqual(fit.hours, 3)

    def test_fireplace_hours_are_left_out(self):
        prior = thermal.Prior.for_area(35.0)
        out = lambda h: 0.0
        kwargs = dict(target=lambda h: 8.0 if h % 24 < 6 else 5.0, heat_kw=2.5, outdoor_c=out,
                      noise=0.02)
        plain = simulate(prior.c_kwh_k, prior.ua_kw_k, 120, **kwargs)
        fire = simulate(prior.c_kwh_k, prior.ua_kw_k, 120,
                        extra=lambda h: 6.0 if 60 <= h < 63 else 0.0, **kwargs)
        a = thermal.estimate(*plain, weather(120, out), prior)
        b = thermal.estimate(*fire, weather(120, out), prior)
        self.assertLess(b.hours, a.hours)                      # the fire's hours dropped
        self.assertAlmostEqual(b.ua_kw_k / a.ua_kw_k, 1.0, delta=0.15)

    def test_result_stays_within_reach_of_the_prior(self):
        prior = thermal.Prior.for_area(8.0)
        # Nonsense readings: the room warms fast with no power at all.
        temps = {T0 + h * HOUR: 5.0 + 3.0 * h for h in range(40)}
        power = {ts: 0.0 for ts in temps}
        fit = thermal.estimate(temps, power, weather(40, lambda h: 0.0), prior)
        self.assertGreaterEqual(fit.c_kwh_k, prior.c_kwh_k / thermal.LIMIT_FACTOR - 1e-9)
        self.assertLessEqual(fit.c_kwh_k, prior.c_kwh_k * thermal.LIMIT_FACTOR + 1e-9)
        self.assertGreater(fit.ua_kw_k, 0.0)

    def test_hours_without_outdoor_or_power_are_skipped(self):
        temps = {T0 + h * HOUR: 5.0 for h in range(6)}
        power = {T0 + h * HOUR: 0.2 for h in (0, 1, 2, 4, 5)}
        rows = thermal.rows(temps, power, weather(6, lambda h: 0.0))
        self.assertEqual(len(rows), 3)       # 0-1, 1-2 and 4-5
        self.assertEqual(thermal.rows(temps, power, Outdoor([])), [])


class HeatUpTests(unittest.TestCase):
    """A room of two stores: light air and surfaces, heavy walls (Kammari
    17.-18.11.2026: a cool-down said 14-19 h to 22 °C, it took 5 h 20 min)."""

    @staticmethod
    def two_stores(schedule, outdoor_c=5.0, air=0.2, wall=0.9, link=0.04, ua=0.024):
        """Hourly means of a room whose 1 kW heater follows ``schedule[h]``:
        kW, or a str holding that °C by thermostat; ``air`` and ``wall`` in
        kWh/°C, ``link`` and ``ua`` in kW/°C."""
        t_air = t_wall = 12.0
        temps, power = {}, {}
        steps = 60
        for h, step in enumerate(schedule):
            t_sum = p_sum = 0.0
            for _ in range(steps):
                kw = (1.0 if t_air < float(step) else 0.0) if isinstance(step, str) else step
                flow = link * (t_air - t_wall)
                t_air += (kw - flow - ua * 0.5 * (t_air - outdoor_c)) / air / steps
                t_wall += (flow - ua * 0.5 * (t_wall - outdoor_c)) / wall / steps
                t_sum += t_air
                p_sum += kw
            temps[T0 + h * HOUR] = t_sum / steps
            power[T0 + h * HOUR] = p_sum / steps
        return temps, power

    def test_a_heat_up_from_cold_is_learned_apart_from_the_cool_down(self):
        from villa_kuppis.cottage_targets import thermal_lead
        # Two days held at 12 °C, four days of free cooling, then flat out.
        schedule = ["12"] * 48 + [0.0] * 96 + [1.0] * 6
        temps, power = self.two_stores(schedule)
        out = weather(len(schedule), lambda h: 5.0)
        prior = thermal.Prior.for_area(8.0)
        before = thermal.estimate({t: v for t, v in temps.items() if t < T0 + 144 * HOUR},
                                  power, out, prior, heat_kw=1.0)
        fit = thermal.estimate(temps, power, out, prior, heat_kw=1.0)
        self.assertEqual(before.heat_hours, 0)
        # The cool-down after holding 12 °C makes the room look heavy ...
        self.assertGreater(before.c_kwh_k, 1.4 * prior.c_kwh_k)
        # ... but without heat-up hours the room's size sets the heat-up pace.
        self.assertAlmostEqual(before.heatup_c_kwh_k, prior.c_kwh_k)
        model = before.room_model(1.0)
        self.assertLess(thermal_lead(model, 5.7, 20.0, 5.0),
                        0.7 * thermal_lead(replace(model, heatup_c_per_h=None), 5.7, 20.0, 5.0))
        # The heat-up hours are kept and give the light store.
        self.assertGreaterEqual(fit.heat_hours, 4)
        self.assertLess(fit.heat_c_kwh_k, 0.8 * before.c_kwh_k)

    def test_the_visit_lead_follows_the_heat_up(self):
        from villa_kuppis.cottage_targets import THERMAL_LEAD_FACTOR, thermal_lead
        schedule = ["12"] * 48 + [0.0] * 96 + [1.0] * 6 + [0.0] * 40 + [1.0] * 30
        temps, power = self.two_stores(schedule)
        start = T0 + 190 * HOUR
        h = next(h for h in range(30) if temps[start + (h + 1) * HOUR] >= 18.0)
        t0, t1 = temps[start + h * HOUR], temps[start + (h + 1) * HOUR]
        real = h + (18.0 - t0) / (t1 - t0)                    # hours, from the hourly means
        out = weather(len(schedule), lambda h: 5.0)
        fit = thermal.estimate({t: v for t, v in temps.items() if t < start}, power, out,
                               thermal.Prior.for_area(8.0), heat_kw=1.0)
        lead = thermal_lead(fit.room_model(1.0), temps[start], 18.0, 5.0)
        self.assertGreater(lead, real)                        # warm by the arrival
        self.assertLess(lead, 1.5 * THERMAL_LEAD_FACTOR * real)

    def test_a_heater_flat_out_that_only_holds_the_room_is_no_heat_up(self):
        temps = {T0 + h * HOUR: 5.0 for h in range(48)}
        power = {T0 + h * HOUR: 1.0 for h in range(48)}
        fit = thermal.estimate(temps, power, weather(48, lambda h: -30.0),
                               thermal.Prior.for_area(8.0), heat_kw=1.0)
        self.assertEqual(fit.heat_hours, 0)


if __name__ == "__main__":
    unittest.main()
