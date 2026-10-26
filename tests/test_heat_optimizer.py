"""Smart maintenance heating (card F5.2): recorded price days, a learned cottage room.

The optimizer and the fixed heating thresholds (card F4.1, default
settings) run on the recorded Elering days in tests/fixtures, as they are
and scaled to winter price levels, through the same learned room and a
cold outdoor curve.
"""

from __future__ import annotations

import json
import math
import unittest
from decimal import Decimal

from common import FIXTURES
from villa_kuppis import learning, settings
from villa_kuppis.heat_optimizer import HeatLimits, optimize_heating, period_step
from villa_kuppis.learning import Outdoor, RoomModel
from villa_kuppis.planning import Period, plan_day

DAYS = ("2026-03-29", "2026-10-20", "2026-10-25")   # 92, 96 and 100 periods
COTTAGE = RoomModel(True, loss_per_h=0.04, heat_c_per_h=2.0, uses_outdoor=True)
STEADY = RoomModel(True, drift_c_per_h=-0.5, heat_c_per_h=2.0)
LIMITS = HeatLimits(5.0, 12.0)


def recorded(day: str, scale: float = 1.0) -> list[tuple[int, float]]:
    data = json.loads((FIXTURES / f"elering-{day}.json").read_text())
    return [(row["timestamp"], row["price"] / 10 * scale)
            for row in data["data"]["fi"]]


def winter_outdoor(start: int) -> Outdoor:
    return Outdoor([(start + h * 3600,
                     -10 + 4 * math.sin(((h % 24) - 9) / 24 * 2 * math.pi))
                    for h in range(30)])


def fixed_targets(prices) -> list[float]:
    """The fixed plan with the default cottage settings, as cottage_targets makes it."""
    periods = [Period(ts, Decimal(str(round(p, 4)))) for ts, p in prices]
    plan = plan_day(periods, settings.plan_settings({}, "cottage", ["large"]))["large"]
    return [float(row.temperature_c) for row in plan]


class RecordedDayTests(unittest.TestCase):
    def check_limits(self, plan, limits):
        self.assertGreaterEqual(min(s.target_c for s in plan.steps), limits.min_c)
        self.assertLessEqual(max(s.target_c for s in plan.steps), limits.max_c)
        self.assertLessEqual(max(s.room_c for s in plan.steps), limits.max_c + 0.05)

    def test_costs_no_more_than_fixed_and_stays_within_limits(self):
        cheaper = 0
        for day in DAYS:
            for scale in (1.0, 3.0):
                for start_room in (5.0, 9.0):
                    with self.subTest(day=day, scale=scale, room=start_room):
                        prices = recorded(day, scale)
                        plan = optimize_heating(COTTAGE, start_room, prices,
                                                winter_outdoor(prices[0][0]), LIMITS,
                                                fixed_targets(prices))
                        self.assertEqual(len(plan.steps), len(prices))
                        self.assertLessEqual(plan.cost_c, plan.fixed_cost_c + 1e-6)
                        self.assertLessEqual(plan.under_min_c_h,
                                             plan.fixed_under_min_c_h + 1e-9)
                        self.assertEqual(plan.under_min_c_h, 0.0)
                        if plan.optimized:
                            self.check_limits(plan, LIMITS)
                        cheaper += plan.cost_c < plan.fixed_cost_c - 1e-6
        self.assertGreater(cheaper, 0, "the optimizer never beat the fixed plan")

    def test_stores_heat_before_an_expensive_stretch(self):
        start = 1_800_000_000
        prices = ([(start + i * 900, 2.0) for i in range(8)]
                  + [(start + (8 + i) * 900, 30.0) for i in range(16)]
                  + [(start + (24 + i) * 900, 10.0) for i in range(8)])
        plan = optimize_heating(STEADY, 5.0, prices, Outdoor([]), LIMITS, [5.0] * 32)
        self.assertTrue(plan.optimized)
        self.assertGreater(max(s.target_c for s in plan.steps[:8]), LIMITS.min_c)
        expensive = sum(s.heating for s in plan.steps[8:24])
        self.assertLess(expensive, 16 * 0.5 / 2.0)   # the fixed plan's heating
        self.assertLess(plan.cost_c, plan.fixed_cost_c)
        self.check_limits(plan, LIMITS)

    def test_brings_a_cold_room_up_to_the_minimum(self):
        start = 1_800_000_000
        prices = [(start + i * 900, 40.0) for i in range(8)]
        plan = optimize_heating(STEADY, 3.0, prices, Outdoor([]), LIMITS, [5.0] * 8)
        # Full power until the minimum (net +0.375 °C a period), then it holds.
        self.assertEqual([s.heating for s in plan.steps[:5]], [1.0] * 5)
        self.assertAlmostEqual(plan.steps[-1].room_c, LIMITS.min_c)
        self.assertLessEqual(plan.under_min_c_h, plan.fixed_under_min_c_h + 1e-9)
        self.check_limits(plan, LIMITS)

    def test_not_learned_yet_falls_back(self):
        prices = recorded("2026-10-20")
        fixed = fixed_targets(prices)
        for model, room in ((RoomModel(False, "too_little_data"), 6.0),
                            (RoomModel(True, drift_c_per_h=-0.3), 6.0),
                            (COTTAGE, None)):
            self.assertIsNone(optimize_heating(model, room, prices, Outdoor([]),
                                               LIMITS, fixed))

    def test_period_step_matches_learning_step(self):
        for room, target in ((4.0, 8.0), (7.8, 8.0), (10.0, 6.0)):
            ours, _share = period_step(COTTAGE, room, -12.0, target)
            theirs = learning.step(COTTAGE, room, -12.0, True, "Heat", target, 900)
            self.assertAlmostEqual(ours, theirs)


if __name__ == "__main__":
    unittest.main()
