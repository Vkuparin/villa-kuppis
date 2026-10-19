"""Fixed price thresholds for pump cooling (card F4.1): mocked MELCloud Home."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from test_pump_service import FakeClient, ServiceTestCase, make_unit_payloads
from villa_kuppis.cooling import ACTOR
from villa_kuppis.prices import (INTERVAL_SECONDS, PriceInterval,
                                 helsinki_day_bounds)
from villa_kuppis.settings import SettingsStore
from villa_kuppis.storage import PriceStore

DAY = date(2026, 10, 19)          # the FakeClock's Helsinki day


class CoolingTests(ServiceTestCase):
    def start(self):
        service, clock = self._start(FakeClient(make_unit_payloads()))
        self.wait_for(lambda: len(service.view()["units"]) == 3)
        return service, clock

    def set_prices(self, raw=100):
        """One whole day at `raw` EUR/MWh (100 = 10 c/kWh)."""
        start, end = helsinki_day_bounds(DAY)
        intervals = [PriceInterval(ts, Decimal(raw))
                     for ts in range(start, end, INTERVAL_SECONDS)]
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
        return sent

    @staticmethod
    def brief(sent):
        return [(s["unit"], s["action"], s["value"]) for s in sent]

    def test_sets_maximum_only_on_cooling_units_and_logs_it(self):
        service, _ = self.start()
        self.set_prices(100)                      # 10 c/kWh: normal
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 25.0)])
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)
        event = service.events()[0]
        self.assertEqual((event["actor"], event["action"], event["result"]),
                         (ACTOR, "setpoint", "confirmed"))
        # Fan-mode unit-3 and the household's switched-off unit-2 untouched.
        self.assertEqual(self._unit(service, "unit-3")["setpoint"], 23.0)
        self.assertEqual(self._unit(service, "unit-3")["mode"], "Fan")
        self.assertEqual(self._unit(service, "unit-2")["setpoint"], 22.0)
        self.assertFalse(self._unit(service, "unit-2")["power"])
        # A pass without a plan change does not command again.
        self.assertEqual(self.run_plan(service), [])

    def test_precools_when_cheap(self):
        service, _ = self.start()
        self.set_prices(30)                       # 3 c/kWh: below 5
        self.run_plan(service)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 21.0)

    def test_expensive_switches_off_and_min_off_time_holds(self):
        service, clock = self.start()
        self.set_prices(200)                      # 20 c/kWh: above 15
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "power", False)])
        self.assertFalse(self._unit(service, "unit-1")["power"])
        self.assertEqual(self._unit(service, "unit-1")["mode"], "Cool")
        # Price normal again after 10 minutes: still inside the 30 min off time.
        self.set_prices(100)
        clock.advance(600)
        self.assertEqual(self.run_plan(service), [])
        self.assertFalse(self._unit(service, "unit-1")["power"])
        # After 31 minutes it returns, then the next pass sets the setpoint.
        clock.advance(21 * 60)
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "power", True)])
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 25.0)])
        # The household's off unit-2 was never switched on by the plan.
        self.assertFalse(self._unit(service, "unit-2")["power"])

    def test_room_override_can_disable_switching_off(self):
        service, _ = self.start()
        SettingsStore(service.store.path).set(
            "room:home/unit-1", "cool_off_when_expensive", False, "test")
        self.set_prices(200)
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 25.0)])
        self.assertTrue(self._unit(service, "unit-1")["power"])

    def test_day_plan_is_shown_and_targets_recorded(self):
        service, _ = self.start()
        self.assertFalse(service.plan_view()["has_prices"])
        self.set_prices(100)
        view = service.plan_view()
        self.assertTrue(view["has_prices"])
        self.assertFalse(view["enabled"])
        unit = next(u for u in view["units"] if u["id"] == "unit-1")
        self.assertEqual([(w["target_c"], w["off"]) for w in unit["windows"]],
                         [(25.0, False)])
        self.run_plan(service)
        start, end = helsinki_day_bounds(DAY)
        rows = service.store.plan_series(
            "unit-1", datetime.fromtimestamp(start, timezone.utc).isoformat(),
            datetime.fromtimestamp(end, timezone.utc).isoformat(), 1000)
        self.assertEqual(len(rows), 96)
        self.assertEqual({v for _, v in rows}, {25.0})

    def test_no_prices_means_no_commands(self):
        service, _ = self.start()
        self.assertEqual(self.run_plan(service), [])


if __name__ == "__main__":
    unittest.main()
