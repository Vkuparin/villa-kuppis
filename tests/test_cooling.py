"""Cooling control of the home pumps (cards F4.1, F6.1, F6.2, audit 2.1):
mocked MELCloud Home."""

from __future__ import annotations

import unittest
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from test_pump_service import FakeClient, ServiceTestCase, make_unit_payloads
from villa_kuppis.cooling import ACTOR
from villa_kuppis.prices import (INTERVAL_SECONDS, PriceInterval,
                                 helsinki_day_bounds)
from villa_kuppis.settings import SettingsStore
from villa_kuppis.storage import PriceStore

DAY = date(2026, 10, 19)          # the FakeClock's Helsinki day


class CoolingTests(ServiceTestCase):
    def start(self, **settings):
        """The service with the given site:home settings stored first."""
        store = SettingsStore(f"{self.tmp}/pumps.db")
        for key, value in settings.items():
            store.set("site:home", key, value, "test")
        self.client = FakeClient(make_unit_payloads())
        service, clock = self._start(self.client)
        self.wait_for(lambda: len(service.view()["units"]) == 3)
        return service, clock

    def set_reading(self, service, unit_id, name, value, field, expected):
        """Change the fake cloud's unit as if done outside this app."""
        for setting in self.client._units[unit_id]["settings"]:
            if setting["name"] == name:
                setting["value"] = value
        self.wait_for(lambda: self._unit(service, unit_id)[field] == expected,
                      message=f"{name} never reached {expected}")

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

    def test_sets_maximum_and_starts_a_hot_unit_that_is_off(self):
        service, _ = self.start()
        self.set_prices(100)                      # 10 c/kWh: normal
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 25.0),
                                            ("unit-2", "power", True)])
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)
        event = service.events()[-1]
        self.assertEqual((event["actor"], event["action"], event["result"]),
                         (ACTOR, "setpoint", "confirmed"))
        # Once on, unit-2 gets the target too; then nothing more to do.
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-2", "setpoint", 25.0)])
        self.assertEqual(self.run_plan(service), [])
        # The automation's own commands never start a manual pause.
        self.assertEqual(service.automation.holds(), {})
        # Fan-mode unit-3 untouched.
        self.assertEqual(self._unit(service, "unit-3")["setpoint"], 23.0)
        self.assertEqual(self._unit(service, "unit-3")["mode"], "Fan")

    def test_precools_when_cheap(self):
        service, _ = self.start()
        self.set_prices(30)                       # 3 c/kWh: below 5
        self.run_plan(service)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 21.0)

    def test_master_switch_off_sends_nothing_and_on_applies_at_once(self):
        service, _ = self.start()
        self.set_prices(100)
        service.automation.set_automation(False, "Ville")
        self.assertEqual(self.run_plan(service), [])
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 23.5)
        self.assertFalse(service.automation_state()["on"])
        service.automation.set_automation(True, "Ville")
        self.assertTrue(service.automation_state()["on"])
        self.run_plan(service)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)

    def test_power_switch_off_leaves_power_alone(self):
        service, _ = self.start(cool_power=False)
        self.set_prices(200)                      # expensive
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-1", "setpoint", 25.0)])
        self.assertFalse(self._unit(service, "unit-2")["power"])

    def test_stops_below_the_target_and_waits_the_minimum_time(self):
        service, clock = self.start(cool_max_c=28.0, away_max_c=30.0)
        self.set_prices(100)
        sent = self.run_plan(service)             # rooms 26/27 <= 27.5
        self.assertEqual(self.brief(sent), [("unit-1", "power", False)])
        self.assertFalse(self._unit(service, "unit-2")["power"])
        self.set_reading(service, "unit-1", "RoomTemperature", "29",
                         "room_temp", 29.0)
        clock.advance(600)
        self.assertEqual(self.run_plan(service), [])   # minimum time
        clock.advance(21 * 60)
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-1", "power", True)])
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-1", "setpoint", 28.0)])

    def test_price_control_off_keeps_the_maximum_when_cheap(self):
        service, _ = self.start(price_control="off")
        self.set_prices(30)                       # 3 c/kWh: would precool
        self.run_plan(service)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)
        self.assertFalse(any(w["off"] for u in service.plan_view()["units"]
                             for w in u["windows"]))

    def test_expensive_targets_the_maximum_and_stops_a_cool_room(self):
        service, clock = self.start(price_control="fixed")
        self.set_prices(200)                      # 20 c/kWh: above 15
        # A room above the maximum keeps cooling to the maximum.
        sent = self.run_plan(service)
        self.assertIn(("unit-1", "setpoint", 25.0), self.brief(sent))
        self.assertTrue(self._unit(service, "unit-1")["power"])
        # Once it is 0.5 °C below the maximum the unit is switched off.
        self.set_reading(service, "unit-1", "RoomTemperature", "24",
                         "room_temp", 24.0)
        clock.advance(31 * 60)
        self.assertIn(("unit-1", "power", False),
                      self.brief(self.run_plan(service)))
        self.assertEqual(self._unit(service, "unit-1")["mode"], "Cool")
        # It comes back above the maximum after the minimum time.
        self.set_reading(service, "unit-1", "RoomTemperature", "25.5",
                         "room_temp", 25.5)
        clock.advance(600)
        self.assertEqual(self.run_plan(service), [])
        clock.advance(21 * 60)
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-1", "power", True)])
        self.assertEqual(service.automation.holds(), {})

    def test_vat_setting_raises_the_price_the_thresholds_see(self):
        service, _ = self.start()
        self.set_prices(130)                      # 13 c/kWh: normal without VAT
        self.assertFalse(any(w["off"] for u in service.plan_view()["units"]
                             for w in u["windows"]))
        SettingsStore(f"{self.tmp}/pumps.db").set(
            "site:home", "vat_enabled", True, "Ville")   # 16.3 c/kWh: above 15
        unit = service.plan_view()["units"][0]
        self.assertEqual([(w["target_c"], w["off"]) for w in unit["windows"]],
                         [(25.0, True)])

    def test_room_override_can_disable_power_control(self):
        service, _ = self.start()
        SettingsStore(service.store.path).set(
            "room:home/unit-1", "cool_power", False, "test")
        self.set_reading(service, "unit-1", "RoomTemperature", "22",
                         "room_temp", 22.0)
        self.set_prices(200)
        sent = self.run_plan(service)
        self.assertIn(("unit-1", "setpoint", 25.0), self.brief(sent))
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
        self.assertEqual(unit["price_control"], "optimized")
        self.run_plan(service)
        start, end = helsinki_day_bounds(DAY)
        rows = service.store.plan_series(
            "unit-1", datetime.fromtimestamp(start, timezone.utc).isoformat(),
            datetime.fromtimestamp(end, timezone.utc).isoformat(), 1000)
        self.assertEqual(len(rows), 96)
        self.assertEqual({v for _, v in rows}, {25.0})

    def test_away_button_cools_only_above_away_maximum_and_restores(self):
        service, _ = self.start(cool_power=False)
        self.set_prices(30)                       # cheap: normally precools
        self.run_plan(service)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 21.0)
        service.set_away(True, "Ville")
        self.assertTrue(service.plan_view()["away"])
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 28.0)])
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 28.0)
        self.assertEqual(self.run_plan(service), [])
        service.set_away(False, "Ville")
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 21.0)])
        self.assertFalse(service.plan_view()["away"])

    def test_night_mode_quiet_fan_applies_and_lifts(self):
        service, clock = self.start(cool_power=False)
        store = SettingsStore(service.store.path)
        store.set("room:home/unit-1", "night_enabled", True, "test")
        # 12:00 UTC = 15:00 Helsinki: outside 22:00-07:00, no fan command.
        self.assertNotIn("fan", [s["action"] for s in self.run_plan(service)])
        self.assertEqual(self._unit(service, "unit-1")["fan"], "Auto")
        # 19:30 UTC = 22:30 Helsinki.
        clock.advance(7.5 * 3600)
        sent = self.run_plan(service)
        self.assertIn(("unit-1", "fan", "One"), self.brief(sent))
        self.assertEqual(self._unit(service, "unit-1")["fan"], "One")
        # Other rooms did not opt in; no repeat command.
        self.assertEqual(self._unit(service, "unit-2")["fan"], "Two")
        self.assertNotIn("fan", [s["action"] for s in self.run_plan(service)])
        # 04:30 UTC = 07:30 Helsinki: lifted, earlier setting back.
        clock.advance(9 * 3600)
        sent = self.run_plan(service)
        self.assertIn(("unit-1", "fan", "Auto"), self.brief(sent))
        self.assertEqual(self._unit(service, "unit-1")["fan"], "Auto")
        self.assertNotIn("fan", [s["action"] for s in self.run_plan(service)])

    def test_night_mode_keeps_fan_the_household_changed(self):
        service, clock = self.start(cool_power=False)
        SettingsStore(service.store.path).set(
            "room:home/unit-1", "night_enabled", True, "test")
        clock.advance(7.5 * 3600)
        self.run_plan(service)
        service.request("unit-1", "fan", "Four", "Ville")
        self.wait_for(lambda: self._unit(service, "unit-1")["fan"] == "Four")
        # A fan change is not a manual pause.
        self.assertEqual(service.automation.holds(), {})
        clock.advance(9 * 3600)
        self.assertNotIn("fan", [s["action"] for s in self.run_plan(service)])
        self.assertEqual(self._unit(service, "unit-1")["fan"], "Four")

    def test_no_prices_keeps_cooling_to_the_maximum_and_plans_nothing(self):
        service, _ = self.start(cool_power=False)
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 25.0)])
        self.assertEqual(self.run_plan(service), [])   # not repeated
        self.assertFalse(service.plan_view()["has_prices"])
        start, end = helsinki_day_bounds(DAY)
        rows = service.store.plan_series(
            "unit-1", datetime.fromtimestamp(start, timezone.utc).isoformat(),
            datetime.fromtimestamp(end, timezone.utc).isoformat(), 1000)
        self.assertEqual(rows, [])

    def test_power_control_works_without_prices(self):
        service, _ = self.start()
        sent = self.run_plan(service)             # rooms 26 and 27 > maximum 25
        self.assertIn(("unit-2", "power", True), self.brief(sent))
        self.assertTrue(self._unit(service, "unit-2")["power"])

    def test_away_uses_the_away_maximum_without_prices(self):
        service, _ = self.start(cool_power=False)
        service.set_away(True, "Ville")
        sent = self.run_plan(service)
        self.assertEqual(self.brief(sent), [("unit-1", "setpoint", 28.0)])
        self.assertTrue(service.plan_view()["away"])
        self.assertFalse(service.plan_view()["has_prices"])

    # -- manual pause --

    def test_a_change_in_the_app_pauses_the_unit_until_the_hold_ends(self):
        service, clock = self.start(cool_power=False)
        self.set_prices(100)
        self.run_plan(service)                    # unit-1 to 25
        service.request("unit-1", "setpoint", 23.0, "Ville")
        self.wait_for(lambda: self._unit(service, "unit-1")["setpoint"] == 23.0)
        self.assertEqual(self.run_plan(service), [])
        until = service.automation.holds()["unit-1"]
        self.assertEqual(datetime.fromisoformat(until),
                         clock() + timedelta(hours=3))
        clock.advance(2 * 3600)
        self.assertEqual(self.run_plan(service), [])
        clock.advance(3600 + 60)
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-1", "setpoint", 25.0)])
        self.assertEqual(service.automation.holds(), {})

    def test_a_change_outside_the_app_pauses_and_resume_ends_it(self):
        service, clock = self.start()
        self.set_prices(100)
        self.run_plan(service)
        self.run_plan(service)                    # unit-2 started, set to 25
        # Somebody switches unit-2 off in MELCloud Home: no event logged.
        self.set_reading(service, "unit-2", "Power", "False", "power", False)
        self.assertEqual(self.run_plan(service), [])
        self.assertIn("unit-2", service.automation.holds())
        self.assertNotIn("unit-1", service.automation.holds())
        service.resume_automation("unit-2")
        self.assertEqual(service.automation.holds(), {})
        self.assertEqual(self.run_plan(service), [])   # minimum time
        clock.advance(31 * 60)
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-2", "power", True)])

    def test_zero_hold_hours_means_no_pause(self):
        service, _ = self.start(cool_power=False, manual_hold_hours=0)
        self.set_prices(100)
        self.run_plan(service)
        service.request("unit-1", "setpoint", 23.0, "Ville")
        self.wait_for(lambda: self._unit(service, "unit-1")["setpoint"] == 23.0)
        self.assertEqual(self.brief(self.run_plan(service)),
                         [("unit-1", "setpoint", 25.0)])

    def test_switching_the_automation_on_ends_every_pause(self):
        service, _ = self.start(cool_power=False)
        self.set_prices(100)
        self.run_plan(service)
        service.request("unit-1", "setpoint", 23.0, "Ville")
        self.wait_for(lambda: self._unit(service, "unit-1")["setpoint"] == 23.0)
        self.run_plan(service)
        self.assertIn("unit-1", service.automation.holds())
        service.automation.set_automation(False, "Ville")
        service.automation.set_automation(True, "Ville")
        self.assertEqual(service.automation.holds(), {})
        self.run_plan(service)
        self.assertEqual(self._unit(service, "unit-1")["setpoint"], 25.0)

    def test_night_state_of_the_old_tables_is_carried_over(self):
        conn = sqlite3.connect(f"{self.tmp}/pumps.db")
        conn.execute("CREATE TABLE night_state (unit_id TEXT PRIMARY KEY, "
                     "saved_fan TEXT)")
        conn.execute("CREATE TABLE plan_state (unit_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO night_state VALUES ('unit-1', 'Three')")
        conn.commit()
        conn.close()
        service, _ = self.start(cool_power=False)
        self.client._units["unit-1"]["settings"][4]["value"] = "One"
        self.wait_for(lambda: self._unit(service, "unit-1")["fan"] == "One")
        # 15:00 Helsinki is outside the night: the saved fan comes back.
        self.assertIn(("unit-1", "fan", "Three"),
                      self.brief(self.run_plan(service)))
        conn = sqlite3.connect(f"{self.tmp}/pumps.db")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        conn.close()
        self.assertFalse({"night_state", "plan_state"} & tables)

if __name__ == "__main__":
    unittest.main()
