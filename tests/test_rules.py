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
        self.assertEqual((event["triggers"][0]["value"], event["max_price"]), (25.0, 10.0))

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
        self.pending = False
        self.commands = {}

    def raw_states(self):
        return self.states

    def devices_by_key(self):
        return self.devices

    def command(self, key, action, value, actor):
        if self.fail:
            return {"state": "failed", "detail": "Home Assistant is not connected"}
        self.sent.append((key, action, value, actor))
        request_id = f"r{len(self.sent)}"
        result = {"state": "pending" if self.pending else "confirmed", "request_id": request_id}
        self.commands[request_id] = result
        return result

    def command_result(self, request_id):
        return self.commands.get(request_id)


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

    def test_pending_change_waits_for_confirmation_and_preserves_manual_changes(self):
        self.add(FESTIVE)
        self.catalog.pending = True
        result = self.engine.tick()[0]
        self.assertIsNone(self.store.rules("home")[0]["last_state"])
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)
        self.catalog.commands[result["request_id"]]["state"] = "confirmed"
        self.engine.tick()
        self.assertEqual(self.store.rules("home")[0]["last_state"], 0)
        self.catalog.devices["plug"]["on"] = True   # a later manual change holds
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)

    def test_asynchronous_failure_and_unknown_result_retry_after_delay(self):
        self.add(FESTIVE)
        self.catalog.pending = True
        for state in ("failed", "unknown"):
            with self.subTest(state=state):
                result = self.engine.tick()[0]
                count = len(self.catalog.sent)
                self.catalog.commands[result["request_id"]].update(state=state, detail="No answer")
                self.engine.tick()
                stored = self.store.rules("home")[0]
                self.assertEqual((stored["last_result"], stored["last_detail"]), (state, "No answer"))
                self.assertIsNone(stored["last_state"])
                self.now += timedelta(minutes=1)
                self.engine.tick()
                self.assertEqual(len(self.catalog.sent), count)
                self.now += timedelta(minutes=5)

    def test_pending_result_is_recorded_while_automation_is_off(self):
        self.add(FESTIVE)
        self.catalog.pending = True
        result = self.engine.tick()[0]
        self.catalog.commands[result["request_id"]].update(state="failed", detail="Refused")
        self.auto = False
        self.engine.tick()
        self.assertEqual(self.store.rules("home")[0]["last_result"], "failed")
        self.now += timedelta(minutes=6)
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)
        self.auto = True
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 2)

    def test_pending_old_target_does_not_hide_the_next_change_point(self):
        self.add(FESTIVE)
        self.catalog.pending = True
        result = self.engine.tick()[0]
        self.now = local(2026, 11, 20, 15, 31)
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)   # do not overlap device commands
        self.catalog.commands[result["request_id"]]["state"] = "confirmed"
        self.engine.tick()
        self.assertEqual([c[2] for c in self.catalog.sent], [False, True])

    def test_old_confirmation_does_not_apply_to_an_edited_rule(self):
        rule_id = self.add(FESTIVE)
        self.catalog.pending = True
        result = self.engine.tick()[0]
        self.store.save_rule("home", rule_id, validate({
            **FESTIVE, "start": {"type": "time", "at": "10:00"}}), self.now)
        self.catalog.commands[result["request_id"]]["state"] = "confirmed"
        self.engine.tick()
        self.assertIsNone(self.store.rules("home")[0]["last_state"])
        self.assertEqual([c[2] for c in self.catalog.sent], [False, True])

    def test_missing_command_history_is_retried(self):
        self.add(FESTIVE)
        self.catalog.pending = True
        result = self.engine.tick()[0]
        del self.catalog.commands[result["request_id"]]
        self.engine.tick()
        self.assertEqual(self.store.rules("home")[0]["last_result"], "unknown")
        self.now += timedelta(minutes=5)
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 2)

    def test_threshold_does_not_fire_on_recovery_from_missing_readings(self):
        self.add({"kind": "event", "trigger": {"type": "above", "key": "temp", "value": 25},
                  "actions": [{"key": "plug", "action": "power", "value": True}]})
        for missing in ("unavailable", "removed"):
            with self.subTest(missing=missing):
                self.catalog.devices["temp"] = {"available": True, "value": 20.0}
                self.engine.tick()
                if missing == "removed":
                    del self.catalog.devices["temp"]
                else:
                    self.catalog.devices["temp"]["available"] = False
                self.engine.tick()
                self.catalog.devices["temp"] = {"available": True, "value": 26.0}
                self.engine.tick()
                self.assertEqual(self.catalog.sent, [])
        self.catalog.devices["temp"]["value"] = 24.0
        self.engine.tick()
        self.catalog.devices["temp"]["value"] = 26.0
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)

    def test_presence_unknown_is_not_last_person_leaving(self):
        self.add({"kind": "event", "trigger": {"type": "leave"},
                  "actions": [{"key": "plug", "action": "power", "value": False}]})
        self.catalog.states["person.one"] = {"state": "home"}
        self.catalog.states["person.two"] = {"state": "not_home"}
        self.engine.tick()
        self.catalog.states["person.one"]["state"] = "unavailable"
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [])
        self.catalog.states["person.one"]["state"] = "not_home"
        self.engine.tick()   # recovery establishes a baseline
        self.assertEqual(self.catalog.sent, [])
        self.catalog.states["person.one"]["state"] = "home"
        self.engine.tick()
        self.catalog.states["person.one"]["state"] = "not_home"
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


class FlexibleRuleTest(EngineTest):
    """Several devices, weekdays, conditions, sensor rules and "off after"."""

    def setUp(self):
        super().setUp()
        for key in ("plug2", "light"):
            self.catalog.devices[key] = {"key": key, "available": True, "on": False, "caps": {"power": True}}

    def test_old_single_device_rules_still_load(self):
        self.store.save_rule("home", None, {**FESTIVE}, self.now)   # stored before keys/triggers
        self.store.save_rule("home", None, {"kind": "event", "trigger": {"type": "time", "at": "12:00"},
                                            "actions": [{"key": "plug", "action": "power", "value": True}]},
                             self.now)
        loaded = [r["rule"] for r in self.store.rules("home")]
        self.assertEqual((loaded[0]["keys"], loaded[1]["triggers"][0]["at"]), (["plug"], "12:00"))
        self.engine.tick()
        self.assertEqual([c[:3] for c in self.catalog.sent], [("plug", "power", False), ("plug", "power", True)])

    def test_two_plugs_on_weekday_mornings(self):
        self.add({"kind": "between", "keys": ["plug", "plug2"], "days": [0, 1, 2, 3, 4],
                  "start": {"type": "time", "at": "07:00"}, "end": {"type": "time", "at": "09:00"}})
        self.now = local(2026, 11, 20, 7, 5)          # a Friday
        self.engine.tick()
        self.assertEqual([c[:3] for c in self.catalog.sent], [("plug", "power", True), ("plug2", "power", True)])
        self.now = local(2026, 11, 21, 7, 5)          # Saturday: off
        self.engine.tick()
        self.assertEqual([c[2] for c in self.catalog.sent[2:]], [False, False])

    def test_one_failed_device_is_retried_alone(self):
        self.add({"kind": "between", "keys": ["plug", "plug2"],
                  "start": {"type": "time", "at": "07:00"}, "end": {"type": "time", "at": "09:00"}})
        self.catalog.devices["plug2"]["available"] = False
        real = self.catalog.command

        def command(key, action, value, actor):
            if not self.catalog.devices[key]["available"]:
                raise ValueError("Laite ei ole saatavilla")
            return real(key, action, value, actor)
        self.catalog.command = command
        self.engine.tick()
        stored = self.store.rules("home")[0]
        self.assertEqual((stored["last_result"], stored["last_state"]), ("failed", None))
        self.catalog.devices["plug2"]["available"] = True
        self.now += timedelta(minutes=6)
        self.engine.tick()
        self.assertEqual([c[0] for c in self.catalog.sent], ["plug", "plug2"])
        self.assertEqual(self.store.rules("home")[0]["last_state"], 0)

    def test_level_rule_keeps_a_dead_band_and_its_conditions(self):
        self.add({"kind": "level", "keys": ["plug"], "sensor": "temp", "mode": "below",
                  "on_at": 18, "off_at": 19, "when": [{"type": "home"}]})
        self.catalog.states["person.one"] = {"state": "home"}
        self.catalog.devices["temp"]["value"] = 18.5
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [])          # in the band without a state: wait
        self.catalog.devices["temp"]["value"] = 17.5
        self.engine.tick()
        self.catalog.devices["temp"]["value"] = 18.8
        self.engine.tick()
        self.assertEqual([c[2] for c in self.catalog.sent], [True])
        self.catalog.states["person.one"]["state"] = "not_home"   # condition fails: off
        self.engine.tick()
        self.catalog.states["person.one"]["state"] = "home"
        self.catalog.devices["temp"]["value"] = 19.5
        self.engine.tick()
        self.assertEqual([c[2] for c in self.catalog.sent], [True, False])

    def test_any_trigger_fires_and_off_after_switches_off(self):
        self.add({"kind": "event", "triggers": [{"type": "time", "at": "12:00"},
                                                {"type": "turns_on", "key": "light"}],
                  "actions": [{"key": "plug", "action": "power", "value": True},
                              {"key": "plug2", "action": "power", "value": True}],
                  "off_after": 10, "when": [{"type": "below", "key": "temp", "value": 25}]})
        self.now = local(2026, 11, 20, 12, 1)
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 2)
        self.assertIsNotNone(self.store.rules("home")[0]["off_due"])
        self.catalog.devices["light"]["on"] = True          # the second trigger restarts the timer
        self.now += timedelta(minutes=8)
        self.engine.tick()
        self.now += timedelta(minutes=8)
        self.engine.tick()
        self.assertEqual([c[2] for c in self.catalog.sent], [True, True, True, True])
        self.auto = False                                  # off-after runs even with Automaatio off
        self.now += timedelta(minutes=3)
        self.engine.tick()
        self.assertEqual([c[:3] for c in self.catalog.sent[4:]], [("plug", "power", False), ("plug2", "power", False)])
        self.assertIsNone(self.store.rules("home")[0]["off_due"])

    def test_condition_blocks_event(self):
        self.add({"kind": "event", "triggers": [{"type": "time", "at": "12:00"}],
                  "actions": [{"key": "plug", "action": "power", "value": True}],
                  "when": [{"type": "is_on", "key": "light"}]})
        self.now = local(2026, 11, 20, 12, 1)
        self.engine.tick()
        self.assertEqual(self.catalog.sent, [])

    def test_two_close_time_triggers_each_run_once(self):
        self.add({"kind": "event", "triggers": [{"type": "time", "at": "12:00"}, {"type": "time", "at": "12:10"}],
                  "actions": [{"key": "plug", "action": "power", "value": True}]})
        for minute in (1, 5, 11, 15, 20):
            self.now = local(2026, 11, 20, 12, minute)
            self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 2)

    def test_price_trigger_on_crossing(self):
        self.add({"kind": "event", "triggers": [{"type": "price_below", "value": 3}],
                  "actions": [{"key": "plug", "action": "power", "value": True}]})
        cur = int(self.now.timestamp()) // 900 * 900
        self.prices = [(cur, 5.0)]
        self.engine.tick()
        self.prices = [(cur, 2.0)]
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.catalog.sent), 1)

    def test_validation_of_new_fields(self):
        for bad in ({"kind": "level", "keys": ["p"], "sensor": "t", "mode": "below", "on_at": 19, "off_at": 18},
                    {**FESTIVE, "days": []}, {**FESTIVE, "days": [7]},
                    {**FESTIVE, "keys": [f"k{i}" for i in range(11)]},
                    {"kind": "event", "triggers": [], "actions": [{"key": "p", "action": "power", "value": True}]},
                    {"kind": "event", "triggers": [{"type": "leave"}],
                     "actions": [{"key": "p", "action": "power", "value": True}], "off_after": 0},
                    {**FESTIVE, "when": [{"type": "above", "key": "t"}]}):
            with self.assertRaises(RuleError):
                validate(bad)
        self.assertNotIn("days", validate({**FESTIVE, "days": list(range(7))}))
        self.assertEqual(validate({**FESTIVE, "keys": ["a", "a", "b"]})["keys"], ["a", "b"])

    def test_store_adds_new_column_to_an_old_database(self):
        import sqlite3
        path = Path(self.tmp.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(rules.SCHEMA)
        conn.close()
        DeviceStore(path).close()
        conn = sqlite3.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(device_rule)")}
        conn.close()
        self.assertIn("off_due_utc", cols)


class SharedRoomSensorTest(unittest.TestCase):
    def test_one_sensor_serves_two_pumps(self):
        from villa_kuppis.devices import DeviceCatalog, FakeDeviceSource
        with tempfile.TemporaryDirectory() as tmp:
            store = DeviceStore(Path(tmp) / "pumps.db")
            catalog = DeviceCatalog("home", FakeDeviceSource("home"), store,
                                    rooms=lambda: [("olo", "Olohuone"), ("eteinen", "Eteinen"), ("iso", "Iso huone")])
            key = "demo-sensor.ruokahuone_alpstuga_temperature"
            for room in ("olo", "eteinen"):
                catalog.set_room_sensor({"room": room, "key": key})
            self.assertIsNotNone(catalog.room_temperature("olo"))
            self.assertEqual(catalog.room_temperature("olo"), catalog.room_temperature("eteinen"))
            self.assertIsNone(catalog.room_temperature("iso"))
            self.assertEqual(catalog.room_sensor_names(), {"olo": "Ruokahuone ALPSTUGA lämpötila",
                                                           "eteinen": "Ruokahuone ALPSTUGA lämpötila"})
            store.close()


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

    def test_rules_run_with_automaatio_off(self):
        # Automaatio is cooling (home) and heating (cottage) only: rules still switch devices.
        self.service.set_automation(False, "test")
        self.assertFalse(self.service.automation.automation_on())
        status, saved = self.post({"site": "home", "rule": {
            "kind": "between", "name": "Jouluvalot", "keys": ["demo-switch.jouluvalot"],
            "start": {"type": "time", "at": "00:00"}, "end": {"type": "time", "at": "00:00"}}})
        self.assertEqual(status, 200)
        sent = self.service.devices.tick_rules()
        self.assertEqual([(s["key"], s["state"]) for s in sent], [("demo-switch.jouluvalot", "confirmed")])
        plug = [d for a in self.service.devices.view("home")["areas"] for d in a["devices"]
                if d["key"] == "demo-switch.jouluvalot"][0]
        self.assertTrue(plug["on"])

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
