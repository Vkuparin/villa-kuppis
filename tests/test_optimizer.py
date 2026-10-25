"""Cooling optimizer (card F5.4): recorded price days and mocked pumps.

The pure tests run the optimizer and the fixed thresholds (card F4.1) on
the recorded Elering days in tests/fixtures, as they are and scaled to
summer price levels, through the same learned room. The service tests use
the mocked MELCloud Home of test_pump_service with a fixed learned model.
"""

from __future__ import annotations

import json
import math
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from common import FIXTURES
from test_pump_service import FakeClient, ServiceTestCase, make_unit_payloads
from villa_kuppis import learning, optimizer
from villa_kuppis.learning import Outdoor, RoomModel
from villa_kuppis.optimizer import Limits, optimize
from villa_kuppis.prices import (INTERVAL_SECONDS, PriceInterval,
                                 helsinki_day_bounds)
from villa_kuppis.settings import SettingsStore
from villa_kuppis.storage import PriceStore

DAYS = ("2026-03-29", "2026-10-20", "2026-10-25")   # 92, 96 and 100 periods
SUMMER = RoomModel(True, loss_per_h=0.08, drift_c_per_h=0.4,
                   cool_c_per_h=2.0, uses_outdoor=True)
STEADY = RoomModel(True, drift_c_per_h=0.6, cool_c_per_h=2.0)


def recorded(day: str, scale: float = 1.0) -> list[tuple[int, float]]:
    data = json.loads((FIXTURES / f"elering-{day}.json").read_text())
    return [(row["timestamp"], row["price"] / 10 * scale)
            for row in data["data"]["fi"]]


def summer_outdoor(start: int) -> Outdoor:
    return Outdoor([(start + h * 3600,
                     24 + 6 * math.sin(((h % 24) - 9) / 24 * 2 * math.pi))
                    for h in range(30)])


def quartile_limits(prices, min_off=2, off_allowed=True) -> Limits:
    ordered = sorted(p for _, p in prices)
    return Limits(25.0, 21.0, ordered[len(ordered) // 4],
                  ordered[3 * len(ordered) // 4], off_allowed, min_off)


def fixed_rows(prices, limits: Limits):
    """The fixed plan as cooling.build_plan makes it."""
    return [(limits.off_allowed and p >= limits.high_c_kwh,
             limits.precool_c if p < limits.low_c_kwh else limits.max_c)
            for _, p in prices]


class RecordedDayTests(unittest.TestCase):
    def check_limits(self, plan, prices, limits):
        self.assertLessEqual(max(s.room_c for s in plan.steps),
                             limits.max_c + 0.05)
        self.assertGreaterEqual(min(s.target_c for s in plan.steps),
                                limits.precool_c)
        self.assertLessEqual(max(s.target_c for s in plan.steps), limits.max_c)
        # Every off stretch starts in an expensive period and lasts the
        # minimum off time, unless the known prices end first.
        run = 0
        for i, s in enumerate(plan.steps):
            if s.off and run == 0:
                self.assertGreaterEqual(prices[i][1], limits.high_c_kwh)
            if s.off:
                run += 1
            else:
                if run:
                    self.assertGreaterEqual(run, limits.min_off_periods)
                run = 0

    def test_costs_no_more_than_fixed_and_stays_within_limits(self):
        cheaper = 0
        for day in DAYS:
            for scale in (1.0, 40.0):
                for start_room in (22.0, 24.5):
                    with self.subTest(day=day, scale=scale, room=start_room):
                        prices = recorded(day, scale)
                        limits = quartile_limits(prices)
                        plan = optimize(SUMMER, start_room, 0, prices,
                                        summer_outdoor(prices[0][0]), limits,
                                        fixed_rows(prices, limits))
                        self.assertEqual(len(plan.steps), len(prices))
                        self.assertLessEqual(plan.cost_c,
                                             plan.fixed_cost_c + 1e-6)
                        self.assertLessEqual(plan.over_max_c_h,
                                             plan.fixed_over_max_c_h + 1e-9)
                        self.check_limits(plan, prices, limits)
                        cheaper += plan.cost_c < plan.fixed_cost_c - 1e-6
        self.assertGreater(cheaper, 0, "the optimizer never beat the fixed plan")

    def test_respects_settings_that_forbid_switching_off(self):
        prices = recorded("2026-10-20", 40.0)
        limits = quartile_limits(prices, off_allowed=False)
        plan = optimize(SUMMER, 24.0, 0, prices, summer_outdoor(prices[0][0]),
                        limits, fixed_rows(prices, limits))
        self.assertFalse(any(s.off for s in plan.steps))
        self.assertLessEqual(plan.cost_c, plan.fixed_cost_c + 1e-6)

    def test_precools_before_an_expensive_stretch_and_switches_off(self):
        start = 1_800_000_000
        prices = ([(start + i * 900, 10.0) for i in range(8)]
                  + [(start + (8 + i) * 900, 30.0) for i in range(16)]
                  + [(start + (24 + i) * 900, 10.0) for i in range(8)])
        limits = Limits(25.0, 21.0, 5.0, 15.0, True, 2)
        plan = optimize(STEADY, 25.0, 0, prices, Outdoor([]), limits,
                        fixed_rows(prices, limits))
        self.assertTrue(plan.optimized)
        self.assertLess(min(s.target_c for s in plan.steps[:8]), 25.0)
        self.assertTrue(any(s.off for s in plan.steps[8:24]))
        self.assertLess(plan.cost_c, plan.fixed_cost_c)
        self.check_limits(plan, prices, limits)

    def test_minimum_off_time_carries_over_from_now(self):
        start = 1_800_000_000
        prices = [(start + i * 900, 10.0) for i in range(8)]
        limits = Limits(25.0, 21.0, 5.0, 15.0, True, 3)
        plan = optimize(STEADY, 22.0, 1, prices, Outdoor([]), limits,
                        fixed_rows(prices, limits))
        self.assertEqual([s.off for s in plan.steps[:3]], [True, True, False])

    def test_not_learned_yet_falls_back(self):
        prices = recorded("2026-10-20")
        limits = quartile_limits(prices)
        for model in (RoomModel(False, "too_little_data"),
                      RoomModel(True, drift_c_per_h=0.3)):
            self.assertIsNone(optimize(model, 24.0, 0, prices, Outdoor([]),
                                       limits, fixed_rows(prices, limits)))

    def test_period_step_matches_learning_step(self):
        for room, sp in ((26.0, 22.0), (22.5, 22.0), (21.0, 23.0)):
            ours, _share = optimizer.period_step(SUMMER, room, 28.0, False, sp)
            theirs = learning.step(SUMMER, room, 28.0, True, "Cool", sp, 900)
            self.assertAlmostEqual(ours, theirs)


DAY = date(2026, 10, 19)          # the FakeClock's Helsinki day (15:00 local)


class OptimizerServiceTests(ServiceTestCase):
    def start(self, model=STEADY, **settings):
        store = SettingsStore(f"{self.tmp}/pumps.db")
        for key, value in settings.items():
            store.set("site:home", key, value, "test")
        service, clock = self._start(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: len(service.view()["units"]) == 3)
        models = {"unit-1": model, "unit-2": model}
        service.learned_models = lambda site="home": (
            models, (Outdoor([]), Outdoor([])), [])
        return service, clock

    def set_prices(self, expensive_from_utc=14, expensive_to_utc=18):
        """10 c/kWh, and 30 c/kWh between the given UTC hours."""
        start, end = helsinki_day_bounds(DAY)
        intervals = []
        for ts in range(start, end, INTERVAL_SECONDS):
            hour = datetime.fromtimestamp(ts, timezone.utc).hour
            raw = 300 if expensive_from_utc <= hour < expensive_to_utc else 100
            intervals.append(PriceInterval(ts, Decimal(raw)))
        store = PriceStore(f"{self.tmp}/prices.db")
        try:
            store.replace_day(DAY.isoformat(), intervals,
                              datetime(2026, 10, 18, tzinfo=timezone.utc))
        finally:
            store.close()

    def run_plan(self, service):
        self.wait_for(lambda: all(u["fresh"] for u in service.view()["units"]),
                      message="units never became fresh")
        sent = service.automation.run_once()
        self.wait_for(lambda: not any(u["pending"]
                                      for u in service.view()["units"]))
        # unit-2 is off in a hot room, so it is switched on; only unit-1 matters here.
        return [(s["unit"], s["action"], s["value"]) for s in sent
                if s["unit"] == "unit-1"]

    def test_precools_ahead_of_the_expensive_stretch(self):
        service, _ = self.start()
        self.set_prices()
        sent = self.run_plan(service)
        self.assertEqual([(u, a) for u, a, _ in sent], [("unit-1", "setpoint")])
        view = next(u for u in service.plan_view()["units"]
                    if u["id"] == "unit-1")
        self.assertTrue(view["optimizer"]["optimized"])
        # The fixed plan holds 25 °C until 14:00 UTC; the optimizer goes lower.
        before = [w for w in view["windows"]
                  if "T12:00" <= w["start"][10:16] < "T14:00" and not w["off"]]
        self.assertLess(min(w["target_c"] for w in before), 25.0)
        self.assertLessEqual(view["optimizer"]["cost_c"],
                             view["optimizer"]["fixed_cost_c"])
        self.assertTrue(any(w["off"] for w in view["windows"]))
        # The same period is not planned or commanded again.
        self.assertEqual(self.run_plan(service), [])

    def test_room_setting_turns_the_optimizer_off(self):
        service, _ = self.start()
        SettingsStore(service.store.path).set(
            "room:home/unit-1", "price_control", "fixed", "test")
        self.set_prices()
        self.assertEqual(self.run_plan(service), [("unit-1", "setpoint", 25.0)])
        view = next(u for u in service.plan_view()["units"]
                    if u["id"] == "unit-1")
        self.assertIsNone(view["optimizer"])

    def test_away_mode_uses_the_away_maximum(self):
        service, _ = self.start(cool_power=False)
        self.set_prices(0, 0)
        service.set_away(True, "Ville")
        self.assertEqual(self.run_plan(service), [("unit-1", "setpoint", 28.0)])

    def test_unlearned_room_keeps_fixed_thresholds(self):
        service, _ = self.start(model=RoomModel(False, "too_little_data"))
        self.set_prices()
        self.assertEqual(self.run_plan(service), [("unit-1", "setpoint", 25.0)])

    def test_fixed_plan_keeps_a_hot_room_at_the_maximum_when_expensive(self):
        service, clock = self.start(price_control="fixed")
        self.set_prices(0, 24)                 # expensive all day
        # The room (26 °C) is above the maximum: no switching off.
        self.assertEqual(self.run_plan(service), [("unit-1", "setpoint", 25.0)])
        clock.advance(31 * 60)
        self.assertEqual(self.run_plan(service), [])
        self.assertTrue(self._unit(service, "unit-1")["power"])


if __name__ == "__main__":
    unittest.main()
