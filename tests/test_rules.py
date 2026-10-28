"""Tests for Villa Kuppis device rules (rules.py).

A fake catalog and clock stand in for Home Assistant; the API test runs on
the offline demo service. No network beyond 127.0.0.1.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from villa_kuppis import rules
from villa_kuppis.devices import DeviceStore
from villa_kuppis.rules import RuleEngine, RuleError, between_on, cheapest_plan, validate

UTC = timezone.utc


def local(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=rules.HELSINKI).astimezone(UTC)


SUN = {"entity_id": "sun.sun", "state": "above_horizon", "attributes": {
    "next_setting": local(2026, 11, 20, 15, 30).isoformat(),
    "next_rising": local(2026, 11, 21, 8, 45).isoformat()}}

FESTIVE = {"kind": "between", "name": "Jouluvalot", "key": "plug",
           "start": {"type": "sun", "event": "sunset", "offset": 0},
           "end": {"type": "time", "at": "23:00"}}


class ValidateTest(unittest.TestCase):
    def test_clean_rules(self):
        self.assertEqual(validate(FESTIVE)["start"], {"type": "sun", "event": "sunset", "offset": 0})
        event = validate({"kind": "event", "trigger": {"type": "above", "key": "t", "value": 25},
                          "actions": [{"key": "fan", "action": "power", "value": True}],
                          "only": {"start": "08:00", "end": "22:00"}, "max_price": 10})
        self.assertEqual((event["trigger"]["value"], event["max_price"]), (25.0, 10.0))

    def test_refuses_broken_rules(self):
        for bad in ({"kind": "x"}, {**FESTIVE, "key": ""},
                    {**FESTIVE, "end": {"type": "time", "at": "25:00"}},
                    {"kind": "cheapest", "key": "p", "hours": 0, "start": "22:00", "end": "07:00"},
                    {"kind": "event", "trigger": {"type": "time", "at": "07:00"}, "actions": []}):
            with self.assertRaises(RuleError):
                validate(bad)


class TimeTest(unittest.TestCase):
    def test_between_sunset_and_23(self):
        self.assertFalse(between_on(FESTIVE, local(2026, 11, 20, 15, 0), SUN))
        self.assertTrue(between_on(FESTIVE, local(2026, 11, 20, 16, 0), SUN))
        self.assertFalse(between_on(FESTIVE, local(2026, 11, 20, 23, 0), SUN))
        # The next day's sunset comes from the same time of day.
        self.assertTrue(between_on(FESTIVE, local(2026, 11, 21, 15, 35), SUN))
        self.assertIsNone(between_on(FESTIVE, local(2026, 11, 20, 16, 0), None))

    def test_window_across_midnight(self):
        rule = {"kind": "between", "key": "k", "start": {"type": "time", "at": "22:00"},
                "end": {"type": "time", "at": "06:00"}}
        self.assertTrue(between_on(rule, local(2026, 11, 20, 2, 0), None))
        self.assertTrue(between_on(rule, local(2026, 11, 20, 22, 30), None))
        self.assertFalse(between_on(rule, local(2026, 11, 20, 12, 0), None))

    def test_cheapest_periods_in_window(self):
        start = int(local(2026, 11, 20, 22, 0).timestamp())
        prices = {start + i * 900: float(40 - i) for i in range(36)}   # cheaper later in the night
        rule = {"key": "k", "hours": 1, "start": "22:00", "end": "07:00"}

        def by_day(day):
            return [(ts, p) for ts, p in prices.items()
                    if datetime.fromtimestamp(ts, UTC).astimezone(rules.HELSINKI).date() == day]
        on, chosen, _ = cheapest_plan(rule, local(2026, 11, 20, 23, 0), by_day)
        self.assertFalse(on)
        self.assertEqual(chosen, [start + i * 900 for i in range(32, 36)])
        self.assertTrue(cheapest_plan(rule, local(2026, 11, 21, 6, 15), by_day)[0])
        # Without prices the first hours of the window are used.
        on, chosen, _ = cheapest_plan(rule, local(2026, 11, 20, 22, 30), lambda day: [])
        self.assertTrue(on)
        self.assertEqual(chosen[0], start)


class FakeCatalog:
    def __init__(self):
        self.devices = {"plug": {"key": "plug", "available": True, "on": False, "caps": {"power": True}},
                        "temp": {"key": "temp", "available": True, "value": 20.0}}
        self.states = {"sun.sun": SUN}
        self.sent = []
        self.fail = False

    def raw_states(self):
        return self.states

    def devices_by_key(self):
        return self.devices

    def command(self, key, action, value, actor):
        if self.fail:
            return {"state": "failed", "detail": "Home Assistant is not connected"}
        self.sent.append((key, action, value, actor))
        return {"state": "confirmed", "request_id": "r"}


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DeviceStore(Path(self.tmp.name) / "pumps.db")
        self.catalog = FakeCatalog()
        self.now = local(2026, 11, 20, 12, 0)
        self.auto = True
        self.prices = []
        self.engine = RuleEngine("home", self.catalog, self.store, automation_on=lambda: self.auto,
                                 prices=lambda day: self.prices, now=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def add(self, rule):
        return self.store.save_rule("home", None, validate(rule), self.now)

    def test_between_acts_on_change_points_only(self):
        self.add(FESTIVE)
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [("plug", "power", False, "Sääntö: Jouluvalot")])
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)          # a manual change holds
        self.now = local(2026, 11, 20, 15, 31)
        self.engine.tick()
        self.assertEqual(self.catalog.sent[-1][2], True)
        self.now = local(2026, 11, 20, 23, 1)
        self.engine.tick()
        self.assertEqual(self.catalog.sent[-1][2], False)

    def test_automation_off_waits_and_failed_change_retries_later(self):
        self.add(FESTIVE)
        self.auto = False
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [])
        self.auto = True
        self.catalog.fail = True
        self.engine.tick()
        self.catalog.fail = False
        self.now += timedelta(minutes=1)
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [])               # waits before retrying
        self.now += timedelta(minutes=5)
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)
        self.assertEqual(self.store.rules("home")[0]["last_result"], "confirmed")

    def test_time_event_once_a_day_with_price_limit(self):
        self.add({"kind": "event", "name": "Aamu", "trigger": {"type": "time", "at": "07:00"},
                  "max_price": 10, "actions": [{"key": "plug", "action": "power", "value": True}]})
        self.now = local(2026, 11, 21, 7, 1)
        cur = int(self.now.timestamp()) // 900 * 900
        self.prices = [(cur, 12.0)]
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [])               # too expensive: skipped today
        self.prices = [(cur, 5.0)]
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [])               # the day's moment is used up
        self.now = local(2026, 11, 22, 7, 2)
        cur = int(self.now.timestamp()) // 900 * 900
        self.prices = [(cur, 5.0)]
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)

    def test_threshold_fires_on_crossing(self):
        self.add({"kind": "event", "trigger": {"type": "above", "key": "temp", "value": 25},
                  "actions": [{"key": "plug", "action": "power", "value": True}]})
        self.engine.tick()
        self.catalog.devices["temp"]["value"] = 26.0
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)
        self.catalog.devices["temp"]["value"] = 24.0
        self.engine.tick()
        self.catalog.devices["temp"]["value"] = 25.5
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 2)


class RuleApiTest(unittest.TestCase):
    def setUp(self):
        from villa_kuppis.pump_app import PumpAppServer, make_demo_service
        self.tmp = tempfile.TemporaryDirectory()
        self.service = make_demo_service(self.tmp.name)
        self.server = PumpAppServer(self.service, port=0, demo=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.tmp.cleanup()

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/devices/rules", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def rules(self):
        with urllib.request.urlopen(self.base + "/api/devices?site=home", timeout=5) as r:
            return json.loads(r.read())["rules"]

    def test_save_toggle_and_delete(self):
        rule = {**FESTIVE, "key": "demo-switch.jouluvalot"}
        status, saved = self.post({"site": "home", "rule": rule})
        self.assertEqual(status, 200)
        listed = self.rules()
        self.assertEqual((listed[0]["rule"]["name"], listed[0]["enabled"]), ("Jouluvalot", True))
        self.assertIn("now_on", listed[0]["status"])
        self.assertEqual(self.post({"site": "home", "id": saved["id"], "enabled": False})[0], 200)
        self.assertFalse(self.rules()[0]["enabled"])
        status, err = self.post({"site": "home", "rule": {**rule, "start": {"type": "moon"}}})
        self.assertEqual((status, err["error"]), (400, "bad_body"))
        self.assertEqual(self.post({"site": "home", "id": saved["id"], "delete": True})[0], 200)
        self.assertEqual(self.rules(), [])
        self.assertEqual(self.post({"site": "home", "id": saved["id"], "delete": True})[0], 400)


if __name__ == "__main__":
    unittest.main()
