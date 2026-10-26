"""Tests for in-app settings (card F3.5): store, validation, plan effect, API."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from villa_kuppis import settings as S
from villa_kuppis.planning import Period, plan_day
from villa_kuppis.pump_app import PumpAppServer
from villa_kuppis.pump_probe import FakeClient
from villa_kuppis.pump_service import PumpService
from test_pump_app import (SERVICE_KWARGS, FakeClock, http, http_json,
                                 make_unit_payloads)


def _periods(prices):
    return [Period(i * 900, Decimal(str(p))) for i, p in enumerate(prices)]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vk-settings-")
        self.store = S.SettingsStore(Path(self._tmp.name) / "pumps.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_and_precedence(self):
        res = S.resolve(self.store.overrides(), "home", "r1")
        self.assertEqual(res["cool_max_c"], {"value": 25.0, "source": "default"})
        self.assertEqual(res["min_off_minutes"]["value"], 30)
        self.assertEqual(res["away_max_c"]["value"], 28.0)
        self.assertEqual(res["night_start"]["value"], "22:00")
        self.assertEqual(res["night_end"]["value"], "07:00")
        self.assertEqual(res["price_control"]["value"], "optimized")
        self.assertIs(res["cool_power"]["value"], True)
        self.assertEqual(res["manual_hold_hours"]["value"], 3.0)
        self.assertNotIn("kind", res)
        self.store.set("site:home", "cool_max_c", 26, "Ville")
        self.store.set("room:home/r1", "cool_max_c", 24.5, "Ville")
        o = self.store.overrides()
        self.assertEqual(S.resolve(o, "home", "r2")["cool_max_c"],
                         {"value": 26.0, "source": "site"})
        self.assertEqual(S.resolve(o, "home", "r1")["cool_max_c"],
                         {"value": 24.5, "source": "room"})
        self.assertEqual(self.store.audit()["site:home"]["cool_max_c"]["updated_by"],
                         "Ville")

    def test_persists_and_reset(self):
        self.store.set("site:home", "precool_c", 20, "a")
        again = S.SettingsStore(Path(self._tmp.name) / "pumps.db")
        self.assertEqual(again.overrides()["site:home"]["precool_c"], 20.0)
        again.reset("site:home", "precool_c")
        self.assertEqual(again.overrides(), {})

    def test_rejects_bad_input(self):
        for scope, key, value in [
            ("site:home", "nope", 1), ("site:mars", "cool_max_c", 25),
            ("site:home", "cool_max_c", "hot"), ("site:home", "cool_max_c", True),
            ("site:home", "cool_max_c", 99), ("site:home", "cool_max_c", float("nan")),
            ("site:home", "min_off_minutes", 1.5), ("site:home", "night_start", "25:00"),
            ("site:home", "night_enabled", 1), ("site:home", "kind", "steam"),
            ("site:home", "price_control", "maybe"),
            ("site:home", "manual_hold_hours", 25),
        ]:
            with self.assertRaises(ValueError, msg=(scope, key, value)):
                self.store.set(scope, key, value, "x")
        self.assertEqual(self.store.overrides(), {})

    def test_cross_field_rules_and_reset_consistency(self):
        with self.assertRaises(ValueError):
            self.store.set("site:home", "precool_c", 26, "x")   # above max 25
        with self.assertRaises(ValueError):
            self.store.set("site:home", "low_price_c_kwh", 20, "x")  # above high
        self.store.set("site:home", "cool_max_c", 27, "x")
        self.store.set("site:home", "precool_c", 26, "x")
        self.store.set("room:home/r1", "cool_max_c", 28, "x")
        with self.assertRaises(ValueError):
            self.store.set("room:home/r1", "cool_max_c", 25, "x")  # below precool
        with self.assertRaises(ValueError):
            self.store.reset("site:home", "cool_max_c")   # 25 < precool 26

    def test_changed_setting_takes_effect_in_next_plan(self):
        periods = _periods([3, 9, 20])
        rooms = ["r1", "r2"]
        before = plan_day(periods, S.plan_settings(self.store.overrides(),
                                                   "home", rooms))
        # Defaults: cool to 21 below 5 c/kWh, otherwise the 25 maximum.
        self.assertEqual([p.temperature_c for p in before["r1"]],
                         [Decimal("21.0"), Decimal("25.0"), Decimal("25.0")])
        self.store.set("site:home", "low_price_c_kwh", 10, "Ville")
        self.store.set("room:home/r2", "precool_c", 19, "Ville")
        after = plan_day(periods, S.plan_settings(self.store.overrides(),
                                                  "home", rooms))
        self.assertEqual([p.temperature_c for p in after["r1"]],
                         [Decimal("21.0"), Decimal("21.0"), Decimal("25.0")])
        self.assertEqual([p.temperature_c for p in after["r2"]],
                         [Decimal("19.0"), Decimal("19.0"), Decimal("25.0")])

    def test_price_control_off_plans_only_the_maximum(self):
        self.store.set("site:home", "price_control", "off", "Ville")
        plan = plan_day(_periods([3, 20]), S.plan_settings(
            self.store.overrides(), "home", ["r1"]))["r1"]
        self.assertEqual([p.temperature_c for p in plan],
                         [Decimal("25.0"), Decimal("25.0")])

    def test_cottage_defaults_heat(self):
        plan = plan_day(_periods([4, 6, 9]),
                        S.plan_settings({}, "cottage", ["sauna"]))["sauna"]
        self.assertEqual([p.temperature_c for p in plan],
                         [Decimal("12.0"), Decimal("8.0"), Decimal("5.0")])

    def test_cottage_heating_texts_match_what_is_built(self):
        # The thresholds drive the cottage; smart heating is hidden until built.
        for key in ("heat_base_c", "heat_rule1_price_c_kwh", "heat_rule1_c",
                    "heat_rule2_price_c_kwh", "heat_rule2_c"):
            self.assertNotIn("ei vielä", S.BY_KEY[key].help.lower())
            self.assertEqual(S.BY_KEY[key].kinds, ("heat",))
        for key in ("smart_enabled", "smart_min_c", "smart_max_c"):
            self.assertEqual(S.BY_KEY[key].kinds, ())


class MigrationTests(unittest.TestCase):
    """Overrides of the keys replaced on 2026-10-25 (audit 2.2)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vk-settings-mig-")
        self.path = Path(self._tmp.name) / "pumps.db"
        S.SettingsStore(self.path)               # creates the table

    def tearDown(self):
        self._tmp.cleanup()

    def migrate(self, rows):
        conn = sqlite3.connect(self.path)
        conn.executemany(
            "INSERT INTO setting VALUES (?, ?, ?, '2026-10-20T10:00:00+00:00', 'Ville')",
            [(scope, key, json.dumps(value)) for scope, key, value in rows])
        conn.commit()
        conn.close()
        store = S.SettingsStore(self.path)
        conn = sqlite3.connect(self.path)
        keys = {r[0] for r in conn.execute("SELECT key FROM setting")}
        conn.close()
        self.assertFalse(keys & set(S.LEGACY_KEYS))
        return store

    def effective(self, store, room=None):
        values = S.resolve(store.overrides(), "home", room)
        return values["price_control"]["value"], values["cool_power"]["value"]

    def test_heating_season_settings(self):
        # Precooling off and the temperature switch on (the owner's setup).
        store = self.migrate([
            ("site:home", "precool_enabled", False),
            ("site:home", "cool_manage_power", True),
            ("site:home", "cool_max_c", 25.0),
            ("site:cottage", "kind", "heat")])
        self.assertEqual(self.effective(store), ("off", True))
        self.assertEqual(store.overrides()["site:home"]["cool_max_c"], 25.0)
        self.assertEqual(store.audit()["site:home"]["price_control"]["updated_by"],
                         S.MIGRATION_ACTOR)

    def test_precooling_off_drops_the_expensive_switch_off(self):
        store = self.migrate([("site:home", "precool_enabled", False)])
        self.assertEqual(self.effective(store), ("off", False))

    def test_fixed_thresholds_and_room_overrides(self):
        store = self.migrate([
            ("site:home", "cool_optimizer", False),
            ("room:home/r1", "cool_off_when_expensive", False),
            ("room:home/r2", "cool_optimizer", True)])
        self.assertEqual(self.effective(store), ("fixed", True))
        self.assertEqual(self.effective(store, "r1"), ("fixed", False))
        self.assertEqual(self.effective(store, "r2"), ("optimized", True))
        self.assertEqual(self.effective(store, "r3"), ("fixed", True))

    def test_defaults_need_no_override(self):
        store = self.migrate([("site:home", "cool_off_when_expensive", True),
                              ("site:home", "kind", "cool")])
        self.assertEqual(store.overrides(), {})
        # A second start finds nothing to migrate and changes nothing.
        S.SettingsStore(self.path).set("site:home", "cool_power", False, "Ville")
        self.assertEqual(self.effective(S.SettingsStore(self.path)),
                         ("optimized", False))


class ApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vk-settings-app-")
        self.service = PumpService(Path(self._tmp.name),
                                   client=FakeClient(make_unit_payloads()),
                                   now=FakeClock(), **SERVICE_KWARGS)
        self.service.start()
        self.server = PumpAppServer(self.service, host="127.0.0.1", port=0)
        threading.Thread(target=self.server.serve_forever,
                         kwargs={"poll_interval": 0.1}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self._tmp.cleanup()

    def url(self, path="/api/settings"):
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def wait_units(self):
        import time
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if self.service.view()["units"]:
                return
            time.sleep(0.02)
        self.fail("no units")

    def test_get_set_reset_over_http(self):
        self.wait_units()
        status, _, raw = http("GET", self.url())
        self.assertEqual(status, 200)
        data = json.loads(raw)
        ids = [s["id"] for s in data["scopes"]]
        self.assertEqual(ids[:2], ["site:home", "site:cottage"])
        room = next(i for i in ids if i.startswith("room:home/"))
        self.assertEqual(data["effective"]["site:home"]["cool_max_c"]["value"], 25.0)
        self.assertEqual({s["id"]: s["kind"] for s in data["scopes"]}["site:cottage"],
                         "heat")
        precool = next(d for d in data["definitions"] if d["key"] == "precool_c")
        self.assertEqual(precool["shown_if"],
                         {"key": "price_control", "values": ["fixed", "optimized"]})

        status, body = http_json("POST", self.url(),
                                 {"scope": room, "key": "cool_max_c", "value": 27})
        self.assertEqual(status, 200, body)
        data = json.loads(http("GET", self.url())[2])
        self.assertEqual(data["effective"][room]["cool_max_c"],
                         {"value": 27.0, "source": "room"})
        self.assertEqual(data["effective"]["site:home"]["cool_max_c"]["source"],
                         "default")
        self.assertEqual(data["audit"][room]["cool_max_c"]["updated_by"],
                         "Paikallinen käyttäjä")

        status, _ = http_json("POST", self.url(),
                              {"scope": room, "key": "cool_max_c", "reset": True})
        self.assertEqual(status, 200)
        data = json.loads(http("GET", self.url())[2])
        self.assertEqual(data["overrides"], {})

    def test_errors(self):
        self.wait_units()
        for body, code in [
            ({"scope": "site:home", "key": "cool_max_c", "value": 99}, 400),
            ({"scope": "site:home", "key": "cool_max_c", "value": "x"}, 400),
            ({"scope": "site:home", "key": "nope", "value": 1}, 400),
            ({"scope": "room:home/ghost", "key": "cool_max_c", "value": 26}, 404),
            ({"scope": "site:home", "key": "cool_max_c"}, 400),
            ({"scope": 5, "key": "cool_max_c", "value": 1}, 400),
        ]:
            status, payload = http_json("POST", self.url(), body)
            self.assertEqual(status, code, (body, payload))
        status, _, _ = http("PUT", self.url(), b"{}")
        self.assertIn(status, (404, 405, 501))
        self.assertEqual(json.loads(http("GET", self.url())[2])["overrides"], {})

    def test_page_has_settings_section(self):
        page = http("GET", self.url("/"))[2].decode("utf-8")
        for needle in ("Asetukset", 'id="set-scope"', "Palauta oletus",
                       "Muutos tulee voimaan seuraavassa suunnitelmassa"):
            self.assertIn(needle, page)
        self.assertNotIn("/*SETTINGS", page)
        self.assertNotIn("<!--SETTINGS", page)


if __name__ == "__main__":
    unittest.main()
