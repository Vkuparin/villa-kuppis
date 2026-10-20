"""Visit heating (card F4.3): start planning, the visit store, and a
simulated visit that must reach its target by the arrival time."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from villa_kuppis.planning import Period
from villa_kuppis.prices import INTERVAL_SECONDS
from villa_kuppis.visit import (
    LEAD_HOURS, VisitError, VisitPlan, VisitStore, plan_start, visit_target,
)

from tests.test_cottage_control import Room, SimHeater, control

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
ARRIVAL = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)   # Friday 18:00 Helsinki


def flat_prices(start: datetime, hours: int, price: str = "10") -> list[Period]:
    ts = int(start.timestamp())
    return [Period(ts + i * INTERVAL_SECONDS, Decimal(price)) for i in range(hours * 4)]


def with_price(periods, start: datetime, hours: float, price: str) -> list[Period]:
    lo = int(start.timestamp())
    hi = lo + int(hours * 3600)
    return [Period(p.ts_utc, Decimal(price)) if lo <= p.ts_utc < hi else p for p in periods]


class PlanStartTests(unittest.TestCase):
    def test_flat_prices_start_at_the_fixed_lead(self):
        plan = plan_start(ARRIVAL, flat_prices(NOW, 48), NOW)
        self.assertEqual(plan.start, ARRIVAL - timedelta(hours=LEAD_HOURS))
        self.assertEqual(plan.reason, "cheapest")

    def test_a_cheap_night_moves_the_start_earlier(self):
        fixed = ARRIVAL - timedelta(hours=LEAD_HOURS)
        prices = with_price(flat_prices(NOW, 48), fixed - timedelta(hours=3), 3, "1")
        plan = plan_start(ARRIVAL, prices, NOW)
        self.assertEqual(plan.start, fixed - timedelta(hours=3))

    def test_expensive_hours_after_the_fixed_start_do_not_move_it_later(self):
        fixed = ARRIVAL - timedelta(hours=LEAD_HOURS)
        prices = with_price(flat_prices(NOW, 48), fixed, 2, "50")
        plan = plan_start(ARRIVAL, prices, NOW)
        self.assertLessEqual(plan.start, fixed)

    def test_unknown_prices_give_the_fixed_start(self):
        plan = plan_start(ARRIVAL, flat_prices(NOW, 10), NOW)
        self.assertEqual(plan.reason, "fixed")
        self.assertEqual(plan.start, ARRIVAL - timedelta(hours=LEAD_HOURS))

    def test_short_notice_starts_now(self):
        now = ARRIVAL - timedelta(hours=5)
        plan = plan_start(ARRIVAL, flat_prices(now, 10), now)
        self.assertEqual(plan.reason, "fixed")
        self.assertEqual(plan.start, now)

    def test_visit_target_applies_from_the_start_to_its_rooms_only(self):
        store_visit = VisitStoreTests.make_visit()
        plan = VisitPlan(ARRIVAL - timedelta(hours=12), "cheapest", None)
        before = plan.start - timedelta(minutes=1)
        self.assertIsNone(visit_target(store_visit, "large", before, plan))
        self.assertEqual(visit_target(store_visit, "large", plan.start, plan), 21.0)
        self.assertIsNone(visit_target(store_visit, "small", plan.start, plan))


class VisitStoreTests(unittest.TestCase):
    @staticmethod
    def make_visit():
        with tempfile.TemporaryDirectory() as tmp:
            store = VisitStore(Path(tmp) / "v.db")
            visit = store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW)
            store.close()
        return visit

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = VisitStore(Path(self.tmp.name) / "v.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_start_mark_and_end(self):
        visit = self.store.start(ARRIVAL, 21.0, ["large", "small"], "Ville", NOW)
        self.assertEqual(visit.rooms, ("large", "small"))
        self.assertIsNone(visit.heat_start)
        self.store.mark_started(visit.visit_id, NOW + timedelta(hours=20))
        self.store.mark_started(visit.visit_id, NOW + timedelta(hours=21))   # first one stays
        self.assertEqual(self.store.active().heat_start, NOW + timedelta(hours=20))
        self.assertTrue(self.store.end(NOW + timedelta(days=2)))
        self.assertIsNone(self.store.active())
        self.assertFalse(self.store.end(NOW + timedelta(days=2)))

    def test_new_visit_replaces_the_active_one(self):
        self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW)
        second = self.store.start(ARRIVAL + timedelta(days=7), 20.0, ["small"], "Ville", NOW)
        self.assertEqual(self.store.active(), second)

    def test_invalid_visits_are_refused(self):
        for arrival, target, rooms in [
            (NOW - timedelta(hours=1), 21.0, ["large"]),
            (NOW + timedelta(days=20), 21.0, ["large"]),
            (ARRIVAL, 30.0, ["large"]),
            (ARRIVAL, 21.0, []),
            (ARRIVAL.replace(tzinfo=None), 21.0, ["large"]),
        ]:
            with self.subTest(arrival=arrival, target=target, rooms=rooms):
                with self.assertRaises(VisitError):
                    self.store.start(arrival, target, rooms, "Ville", NOW)


class SimulatedVisitTests(unittest.TestCase):
    def run_visit(self, outdoor: float) -> tuple[float, datetime]:
        heaters = [SimHeater("climate.mill_1", 1050), SimHeater("climate.mill_2", 1050),
                   SimHeater("climate.mill_3", 500)]
        room = Room(heaters, temp=6.0, outdoor=outdoor)
        now = NOW
        prices = with_price(flat_prices(NOW, 48), ARRIVAL - timedelta(hours=15), 2, "1")
        plan = plan_start(ARRIVAL, prices, now)
        memory = control.RoomMemory()
        while now < ARRIVAL:
            target = 21.0 if now >= plan.start else 7.0
            decision = control.decide(
                now, control.Target(target, 1, now + timedelta(minutes=45)),
                control.Sensor(round(room.temp, 1), now), [h.view() for h in heaters], memory)
            room.apply(decision.commands)
            room.step(30)
            room.step(30)
            now += timedelta(minutes=1)
        return room.temp, plan.start

    def test_visit_reaches_its_target_by_arrival(self):
        temp, start = self.run_visit(outdoor=-10.0)
        self.assertGreaterEqual(temp, 20.5)
        self.assertLessEqual(start, ARRIVAL - timedelta(hours=LEAD_HOURS))

    def test_colder_day_is_still_on_time_with_the_twelve_hour_lead(self):
        temp, _ = self.run_visit(outdoor=-15.0)
        self.assertGreaterEqual(temp, 20.5)

    def test_the_fixed_lead_is_only_an_assumption(self):
        # In this model the heaters cannot hold 21 °C at -25 °C at all; the
        # plan must not be reported as on time just because it started.
        temp, _ = self.run_visit(outdoor=-25.0)
        self.assertLess(temp, 20.5)


if __name__ == "__main__":
    unittest.main()
