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
    HOLD_FACTOR, LEAD_HOURS, VisitError, VisitPlan, VisitStore, hold_factor, in_night,
    lead_hours, plan_start, visit_target,
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


class WeatherAndNightTests(unittest.TestCase):
    def test_lead_grows_with_the_frost(self):
        self.assertEqual(lead_hours(None), LEAD_HOURS)
        self.assertEqual(lead_hours(10.0), 8.0)
        self.assertEqual(lead_hours(5.0), 8.0)
        self.assertEqual(lead_hours(2.5), 10.0)
        self.assertEqual(lead_hours(0.0), 12.0)
        self.assertEqual(lead_hours(-2.5), 18.0)
        self.assertEqual(lead_hours(-20.0), 24.0)

    def test_holding_a_warm_room_costs_more_in_frost(self):
        self.assertEqual(hold_factor(21.0, None), HOLD_FACTOR)
        self.assertEqual(hold_factor(21.0, 6.0), Decimal("0.5"))
        self.assertEqual(hold_factor(21.0, -9.0), Decimal("1.0"))
        self.assertEqual(hold_factor(21.0, -30.0), Decimal("1.0"))
        self.assertEqual(hold_factor(21.0, 25.0), Decimal("0.1"))

    def test_a_cheaper_hold_moves_the_start_earlier_into_cheap_hours(self):
        fixed = ARRIVAL - timedelta(hours=LEAD_HOURS)
        prices = with_price(flat_prices(NOW, 48), fixed - timedelta(hours=2), 2, "8")
        self.assertEqual(plan_start(ARRIVAL, prices, NOW, hold=Decimal("1.0")).start, fixed)
        self.assertEqual(plan_start(ARRIVAL, prices, NOW, hold=Decimal("0.1")).start,
                         fixed - timedelta(hours=2))

    def test_night_hours_are_helsinki_time_and_may_pass_midnight(self):
        night = ("22:00", "06:00")
        helsinki = lambda h, m=0: datetime(2026, 11, 21, h, m, tzinfo=timezone.utc) \
            - timedelta(hours=2)
        self.assertFalse(in_night(helsinki(21, 59), night))
        self.assertTrue(in_night(helsinki(22), night))
        self.assertTrue(in_night(helsinki(3), night))
        self.assertFalse(in_night(helsinki(6), night))
        self.assertTrue(in_night(helsinki(1), ("00:30", "07:00")))
        self.assertFalse(in_night(helsinki(3), None))
        self.assertFalse(in_night(helsinki(3), ("06:00", "06:00")))

    def test_visit_keeps_the_night_target_at_night_after_the_arrival(self):
        night = ("22:00", "06:00")
        with tempfile.TemporaryDirectory() as tmp:
            store = VisitStore(Path(tmp) / "v.db")
            visit = store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW, night_c=18.0)
            store.close()
        plan = VisitPlan(ARRIVAL - timedelta(hours=12), "cheapest", None)
        # The heat-up reaches the target of the arrival (18:00 Helsinki: day).
        self.assertEqual(visit_target(visit, "large", ARRIVAL - timedelta(hours=7), plan, night),
                         21.0)
        self.assertEqual(visit_target(visit, "large", ARRIVAL + timedelta(hours=4), plan, night),
                         18.0)                                 # 22:00
        self.assertEqual(visit_target(visit, "large", ARRIVAL + timedelta(hours=12), plan, night),
                         21.0)                                 # 06:00
        self.assertEqual(visit_target(visit, "large", ARRIVAL + timedelta(hours=4), plan), 21.0)
        late = VisitStoreTests.make_visit(ARRIVAL + timedelta(hours=5), night_c=18.0)   # 23:00
        self.assertEqual(late.arrival_target(night), 18.0)
        self.assertEqual(visit_target(late, "large", ARRIVAL, plan, night), 18.0)


class VisitStoreTests(unittest.TestCase):
    @staticmethod
    def make_visit(arrival=ARRIVAL, night_c=None):
        with tempfile.TemporaryDirectory() as tmp:
            store = VisitStore(Path(tmp) / "v.db")
            visit = store.start(arrival, 21.0, ["large"], "Ville", NOW, night_c=night_c)
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
            (NOW - timedelta(hours=25), 21.0, ["large"]),
            (NOW + timedelta(days=20), 21.0, ["large"]),
            (ARRIVAL, 30.0, ["large"]),
            (ARRIVAL, 21.0, []),
            (ARRIVAL.replace(tzinfo=None), 21.0, ["large"]),
        ]:
            with self.subTest(arrival=arrival, target=target, rooms=rooms):
                with self.assertRaises(VisitError):
                    self.store.start(arrival, target, rooms, "Ville", NOW)

    def test_departure_is_stored_and_checked(self):
        departure = ARRIVAL + timedelta(days=2)
        visit = self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW, departure)
        self.assertEqual(visit.departure, departure)
        self.assertIsNone(self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW).departure)
        for bad in (ARRIVAL, ARRIVAL - timedelta(hours=1), ARRIVAL + timedelta(days=31),
                    departure.replace(tzinfo=None)):
            with self.subTest(departure=bad), self.assertRaises(VisitError):
                self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW, bad)

    def test_night_target_is_stored_changed_and_checked(self):
        visit = self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW, night_c=18.0)
        self.assertEqual(self.store.active().night_c, 18.0)
        self.assertEqual(self.store.update(NOW, target_c=22.0).night_c, 18.0)   # kept
        self.assertEqual(self.store.update(NOW, night_c=17.5).night_c, 17.5)
        self.assertIsNone(self.store.update(NOW, night_c=None).night_c)
        self.assertEqual(self.store.active().visit_id, visit.visit_id)
        with self.assertRaises(VisitError):
            self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW, night_c=30.0)
        with self.assertRaises(VisitError):
            self.store.update(NOW, night_c=2.0)

    def test_notices_are_remembered_per_visit(self):
        first = self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW)
        self.assertFalse(self.store.notice_sent(first.visit_id, "warm"))
        self.store.mark_notice(first.visit_id, "warm", NOW)
        self.store.mark_notice(first.visit_id, "warm", NOW + timedelta(hours=1))
        self.assertTrue(self.store.notice_sent(first.visit_id, "warm"))
        self.assertFalse(self.store.notice_sent(first.visit_id, "late"))
        second = self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW)
        self.assertFalse(self.store.notice_sent(second.visit_id, "warm"))

    def test_a_visit_started_on_site_may_have_arrived(self):
        visit = self.store.start(NOW - timedelta(hours=2), 21.0, ["large"], "Ville", NOW)
        self.assertEqual(visit.arrival, NOW - timedelta(hours=2))
        with self.assertRaises(VisitError):     # a departure already past
            self.store.start(NOW - timedelta(hours=2), 21.0, ["large"], "Ville", NOW,
                             NOW - timedelta(hours=1))

    def test_update_changes_the_active_visit(self):
        visit = self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW)
        self.store.mark_started(visit.visit_id, NOW)
        self.store.mark_notice(visit.visit_id, "late", NOW)
        self.store.mark_notice(visit.visit_id, "home_away", NOW)
        later = NOW + timedelta(days=3)          # on site, after the arrival
        departure = later + timedelta(hours=5)
        updated = self.store.update(later, departure=departure)
        self.assertEqual((updated.visit_id, updated.departure, updated.heat_start),
                         (visit.visit_id, departure, NOW))
        self.assertTrue(self.store.notice_sent(visit.visit_id, "late"))
        updated = self.store.update(later, target_c=22.0, rooms=["large", "small"])
        self.assertEqual((updated.target_c, updated.rooms), (22.0, ("large", "small")))
        self.assertFalse(self.store.notice_sent(visit.visit_id, "late"))   # a new goal
        self.assertTrue(self.store.notice_sent(visit.visit_id, "home_away"))
        self.assertIsNone(self.store.update(later, departure=None).departure)
        for bad in ({"departure": later - timedelta(minutes=1)}, {"target_c": 40.0},
                    {"rooms": []}, {"arrival": ARRIVAL.replace(tzinfo=None)}):
            with self.subTest(bad=bad), self.assertRaises(VisitError):
                self.store.update(later, **bad)

    def test_a_new_arrival_plans_the_heat_up_again(self):
        visit = self.store.start(ARRIVAL, 21.0, ["large"], "Ville", NOW)
        self.store.mark_started(visit.visit_id, NOW)
        moved = self.store.update(NOW, arrival=ARRIVAL + timedelta(days=1))
        self.assertEqual(moved.arrival, ARRIVAL + timedelta(days=1))
        self.assertIsNone(moved.heat_start)
        with self.assertRaises(VisitError):
            self.store.update(NOW, arrival=NOW - timedelta(days=2))

    def test_update_needs_an_active_visit(self):
        with self.assertRaises(VisitError):
            self.store.update(NOW, target_c=20.0)

    def test_visits_stored_before_departures_still_load(self):
        import sqlite3
        path = Path(self.tmp.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE cottage_visit (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "arrival_utc TEXT NOT NULL, target_c REAL NOT NULL, rooms_json TEXT NOT NULL, "
            "created_by TEXT NOT NULL, created_at_utc TEXT NOT NULL, heat_start_utc TEXT, "
            "ended_at_utc TEXT);")
        conn.execute("INSERT INTO cottage_visit (arrival_utc, target_c, rooms_json, created_by, "
                     "created_at_utc) VALUES (?, 21.0, '[\"large\"]', 'Ville', ?)",
                     (ARRIVAL.isoformat(), NOW.isoformat()))
        conn.commit()
        conn.close()
        store = VisitStore(path)
        self.addCleanup(store.close)
        visit = store.active()
        self.assertEqual(visit.arrival, ARRIVAL)
        self.assertIsNone(visit.departure)
        self.assertIsNone(visit.night_c)
        self.assertEqual(store.update(NOW, night_c=18.0).night_c, 18.0)


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
