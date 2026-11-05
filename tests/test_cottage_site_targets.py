"""Price targets and visits wired into the cottage site (cards H2.4, F4.3, H2.5).

A fake cottage Home Assistant on localhost (FakeCottageHA) behind the real
gateway; the site's target tick is called by hand. The visit API runs on a
localhost HTTP server over the offline demo service.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from tests.common import elering_payload
from tests.test_cottage_targets import DAY, PATTERN, FakeCottageHA, with_rules
from tests.test_ha_gateway import TOKEN, Clock, eventually
from villa_kuppis.cottage import CottageSite
from villa_kuppis.ha_gateway import BindingStore, HomeAssistantGateway
from villa_kuppis.prices import INTERVAL_SECONDS, helsinki_day_bounds, validate_day
from villa_kuppis.pump_app import PumpAppServer, make_demo_service, render_page
from villa_kuppis.storage import PriceStore


class SiteTargetTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        start, end = helsinki_day_bounds(DAY)
        count = (end - start) // INTERVAL_SECONDS
        intervals = validate_day(elering_payload(DAY, [PATTERN[i % 4] for i in range(count)]), DAY)
        with PriceStore(self.data / "prices.db") as store:
            store.replace_day(DAY.isoformat(), intervals, datetime.now(timezone.utc))
        self.clock = Clock()
        self.clock.now = datetime.fromtimestamp(start, timezone.utc) + timedelta(minutes=1)
        self.ha = FakeCottageHA(self.clock)
        await self.ha.start()
        for room in ("large", "small"):
            self.ha.set_state(f"sensor.villa_kuppis_{room}_target", "7.0",
                              {"mode": "fallback", "revision": None}, push=False)
        self.bindings = BindingStore(self.data / "sites.db")
        self.bindings.bind("cottage", "large-status", "sensor.villa_kuppis_large_target",
                           "sensor", "", "large")
        self.bindings.bind("cottage", "small-status", "sensor.villa_kuppis_small_target",
                           "sensor", "", "small")
        self.gateway = HomeAssistantGateway(
            "cottage", self.ha.url, lambda: TOKEN, self.bindings, now=self.clock,
            command_timeout=0.5, confirm_timeout=0.5, retry_base=0.05, retry_max=0.2,
            site_stale_after=1e9)
        self.task = asyncio.create_task(self.gateway.run())
        await eventually(lambda: self.gateway.health()["state"] == "ok")
        self.automation = True
        self.site = CottageSite(self.data, self.gateway, lambda: self.bindings.bindings("cottage"),
                                now=self.clock)
        self.site.configure_targets(prices_db=self.data / "prices.db", overrides=lambda: with_rules({}),
                                    automation_on=lambda: self.automation)

    async def asyncTearDown(self):
        self.gateway.request_stop()
        await asyncio.wait_for(self.task, 3.0)
        await self.ha.stop()
        self.site._store.close()
        self.site._visits.close()
        self.bindings.close()
        self.tmp.cleanup()

    async def settle(self, sent):
        for cmd in sent:
            if cmd["state"] == "pending":
                await self.gateway.wait(cmd["request_id"], 3.0)

    def room(self, name):
        return {r["id"]: r for r in self.site.view()["rooms"]}[name]

    async def test_tick_sends_acknowledges_and_shows_the_plan(self):
        self.assertIsNone(self.room("large")["plan"])
        sent = self.site.tick_targets()
        self.assertEqual(len(sent), 2)
        await self.settle(sent)
        self.assertEqual({t["room"]: t["target"] for t in self.ha.targets},
                         {"large": 12.0, "small": 12.0})
        plan = self.room("large")["plan"]
        self.assertEqual((plan["target"], plan["reason"], plan["reason_text"]),
                         (12.0, "threshold", "Hintaraja"))
        self.assertEqual(plan["price"], 2.0)
        self.assertIsNotNone(plan["acked_at"])
        self.assertTrue(self.site.view()["automation_on"])
        # Confirmed writes never reach the event log.
        self.assertEqual(self.site.events(), [])

    async def test_automation_off_releases_once_then_sends_nothing_but_shows_the_plan(self):
        self.automation = False
        # The cottage may still hold a plan from before a restart: one release.
        released = self.site.tick_targets()
        self.assertEqual(len(released), 2)
        await self.settle(released)
        self.assertEqual({t["target"] for t in self.ha.targets}, {0.0})
        calls = len(self.ha.calls)
        self.assertEqual(self.site.tick_targets(), [])
        self.assertEqual(len(self.ha.calls), calls)
        view = self.site.view()
        self.assertFalse(view["automation_on"])
        self.assertEqual(self.room("large")["plan"]["target"], 12.0)

    async def test_optimized_rooms_with_a_learning_restart_still_get_their_thresholds(self):
        # Ville's settings on 2026-11-04: Optimoitu, below 15 c -> 8 °C, below 8 c -> 12 °C.
        # The rooms are not learned yet, so the thresholds are the plan. Reading
        # the learning history after a restart once crashed every tick.
        overrides = {"site:cottage": {"heat_price_control": "optimized", "heat_base_c": 5,
                                      "smart_max_c": 12,
                                      "heat_rule1_price_c_kwh": 15, "heat_rule1_c": 8,
                                      "heat_rule2_price_c_kwh": 8, "heat_rule2_c": 12}}
        for room in ("large", "small"):
            self.bindings.bind("cottage", f"{room}-temperature",
                               f"sensor.{room}_temperature", "sensor", "", room)
            self.bindings.bind("cottage", f"{room}-heater", f"climate.{room}_heater",
                               "climate", "", room)
            self.site.restart_learning(room, "test")
        self.clock.advance(3600)
        read = []

        def models():
            end = self.clock()
            for room in ("large", "small"):
                read.append(self.site.learning_readings(room, end - timedelta(days=7), end))
            return {}, None
        self.site._targets_cfg.update(overrides=lambda: overrides, models=models)
        self.site._sender_map = None          # rebuild the sender with the new settings
        sent = self.site.tick_targets()
        await self.settle(sent)
        self.assertEqual(len(read), 2)
        targets = {t["room"]: t for t in self.ha.targets}
        # 03:00 local at 2 c/kWh: below both thresholds, the higher target wins.
        self.assertEqual({room: t["target"] for room, t in targets.items()},
                         {"large": 12.0, "small": 12.0})
        steps = [t.target_c for t in self.site._sender.schedules["small"]]
        self.assertEqual(set(steps), {12.0, 8.0, 5.0})   # 6 c -> 12, 9 c -> 8, 15 c -> 5
        self.assertEqual(self.room("small")["plan"]["reason"], "threshold")

    async def test_room_without_a_status_binding_is_skipped(self):
        self.bindings.unbind("cottage", "small-status")
        with self.assertLogs(level="WARNING") as logs:
            sent = self.site.tick_targets()
        self.assertIn("small", logs.output[0])
        await self.settle(sent)
        self.assertEqual([t["room"] for t in self.ha.targets], ["large"])
        self.assertIsNone(self.room("small")["plan"])
        self.site.tick_targets()   # logged once only: no new warning needed

    async def test_unknown_write_is_logged_once_as_the_plan(self):
        self.ha.hang = True
        sent = self.site.tick_targets()
        await self.settle(sent)
        self.site.tick_targets()
        events = self.site.events()
        self.assertEqual({e["actor"] for e in events}, {"Suunnitelma"})
        self.assertEqual(len(events), 2)    # one per room
        self.assertEqual(events[0]["action"], "target")
        self.assertIn("ei voitu varmistaa", events[0]["detail"])
        self.clock.advance(61)
        self.ha.hang = False
        await self.settle(self.site.tick_targets())
        self.assertEqual(len(self.site.events()), 2)

    async def test_visit_start_replaces_targets_and_shows_in_the_plan(self):
        arrival = self.clock() + timedelta(hours=13)
        result = await asyncio.to_thread(
            self.site.start_visit, arrival.isoformat(), 21, ["large"], "Ville")
        self.assertEqual(result["visit"]["rooms"], ["large"])
        self.assertEqual(result["plan"]["reason"], "cheapest")
        await self.settle(self.site.tick_targets())
        self.assertEqual(self.room("large")["plan"]["reason"], "threshold")
        self.clock.advance(3600 * 5)
        await self.settle(self.site.tick_targets())
        self.assertEqual(self.room("large")["plan"]["reason"], "visit")
        self.assertEqual(self.room("large")["plan"]["reason_text"], "Vierailu")
        self.assertEqual(self.room("large")["plan"]["target"], 21.0)
        self.assertEqual(self.site.visit_view()["plan"]["reason"], "started")
        self.site.end_visit()
        self.assertIsNone(self.site.visit_view()["visit"])


class VisitApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = make_demo_service(self.tmp.name)
        self.service.start()
        self.server = PumpAppServer(self.service, host="127.0.0.1", port=0)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.tmp.cleanup()

    def call(self, path, body=None, method=None):
        url = f"http://127.0.0.1:{self.server.server_address[1]}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, method=method or ("POST" if data is not None else "GET"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def arrival(self, hours):
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    def test_visit_flow_in_the_demo(self):
        self.assertEqual(self.call("/api/visit"), (200, {"visit": None, "plan": None}))
        status, body = self.call("/api/visit", {"arrival": self.arrival(3), "target": 21,
                                                "rooms": ["large", "small"],
                                                "departure": self.arrival(51), "night": 18})
        self.assertEqual(status, 200)
        self.assertEqual(body["visit"]["target"], 21.0)
        self.assertEqual(body["visit"]["night"], 18.0)
        self.assertEqual(body["visit"]["night_hours"], ["22:00", "06:00"])
        self.assertTrue(body["visit"]["departure"])
        self.assertIn("progress", body)
        self.assertTrue(body["visit"]["created_by"])
        self.assertEqual(body["plan"]["reason"], "started")   # under 12 h away: heat now
        # The demo cottage acknowledges the visit target once the tick runs.
        self.service.cottage.tick_targets()
        status, cottage = self.call("/api/cottage")
        rooms = {r["id"]: r for r in cottage["rooms"]}
        self.assertEqual(rooms["small"]["plan"]["reason_text"], "Vierailu")
        # 18 when the arrival falls in the night hours
        self.assertEqual(rooms["small"]["status"]["requested_target"],
                         body["visit"]["arrival_target"])
        self.assertEqual(rooms["small"]["status"]["mode"], "follow")
        self.assertIsNotNone(rooms["small"]["plan"]["acked_at"])
        self.assertEqual(cottage["air_limits"], {"humidity_pct": 80.0, "co2_ppm": 1200})
        status, body = self.call("/api/visit")
        self.assertIsNotNone(body["visit"]["heat_start"])
        status, body = self.call("/api/visit/end", {})
        self.assertEqual((status, body["visit"]), (200, None))

    def test_invalid_input_gets_a_finnish_400(self):
        good = {"arrival": self.arrival(5), "target": 21, "rooms": ["large"]}
        for change, text in (
                ({"arrival": self.arrival(-25)}, "vuorokauden"),
                ({"arrival": self.arrival(-2), "departure": self.arrival(-1)}, "tulevaisuudessa"),
                ({"arrival": self.arrival(24 * 30)}, "14 päivän"),
                ({"arrival": "2026-11-01T10:00:00"}, "aikavyöhyke"),
                ({"arrival": "pian"}, "kelvollinen"),
                ({"arrival": None}, "puuttuu"),
                ({"target": 40}, "5–25"),
                ({"target": "kuuma"}, "luku"),
                ({"night": 3}, "Yölämpötilan on oltava 5–25"),
                ({"night": "viileä"}, "Yölämpötilan on oltava luku"),
                ({"rooms": []}, "vähintään yksi"),
                ({"rooms": ["sauna"]}, "Tuntematon"),
                ({"departure": self.arrival(4)}, "saapumisen jälkeen"),
                ({"departure": self.arrival(24 * 40)}, "30 päivän"),
                ({"departure": "huomenna"}, "Lähtöaika")):
            status, body = self.call("/api/visit", {**good, **change})
            self.assertEqual(status, 400, change)
            self.assertIn(text, body["detail"])
        self.assertIsNone(self.call("/api/visit")[1]["visit"])
        self.assertEqual(self.call("/api/visit/end", method="GET")[0], 405)

    def test_a_visit_started_on_site_can_be_changed(self):
        status, body = self.call("/api/visit", {"arrival": self.arrival(-0.2), "target": 21,
                                                "rooms": ["large"]})
        self.assertEqual(status, 200)
        self.assertEqual(body["plan"]["reason"], "started")       # heats at once
        self.assertFalse(any(p["late"] for p in body["progress"].values()))
        status, body = self.call("/api/visit/update", {"departure": self.arrival(5)})
        self.assertEqual(status, 200)
        self.assertTrue(body["visit"]["departure"])
        status, body = self.call("/api/visit/update", {"target": 22, "rooms": ["large", "small"]})
        self.assertEqual((body["visit"]["target"], body["visit"]["rooms"]), (22.0, ["large", "small"]))
        status, body = self.call("/api/visit/update", {"departure": None})
        self.assertIsNone(body["visit"]["departure"])
        self.assertIsNone(body["visit"]["night"])
        status, body = self.call("/api/visit/update", {"night": 17.5})
        self.assertEqual((body["visit"]["night"], body["visit"]["target"]), (17.5, 22.0))
        status, body = self.call("/api/visit/update", {"night": None})
        self.assertIsNone(body["visit"]["night"])
        for change, text in (({"departure": "huomenna"}, "Lähtöaika"),
                             ({"departure": self.arrival(-0.1)}, "tulevaisuudessa"),
                             ({"target": 40}, "5–25"), ({"rooms": []}, "vähintään yksi"),
                             ({"night": 30}, "Yölämpötilan")):
            status, body = self.call("/api/visit/update", change)
            self.assertEqual(status, 400, change)
            self.assertIn(text, body["detail"])
        self.assertEqual(self.call("/api/visit/update", method="GET")[0], 405)
        self.call("/api/visit/end", {})
        status, body = self.call("/api/visit/update", {"target": 20})
        self.assertEqual(status, 400)
        self.assertIn("ei ole käynnissä", body["detail"])

    def test_hand_target_and_visit_newest_choice_wins(self):
        def rooms():
            return {r["id"]: r for r in self.call("/api/cottage")[1]["rooms"]}
        self.call("/api/cottage/room_target", {"room": "large", "target": 15})
        self.call("/api/cottage/room_target", {"room": "small", "target": 16})
        # A visit with Mökki takes it over; Kammari keeps its hand target.
        status, body = self.call("/api/visit", {"arrival": self.arrival(-0.2), "target": 21,
                                                "rooms": ["large"]})
        self.assertEqual(status, 200)
        self.service.cottage.tick_targets()
        r = rooms()
        self.assertIsNone(r["large"]["hand"])
        self.assertEqual(r["large"]["plan"]["reason"], "visit")
        self.assertEqual((r["small"]["hand"]["target"], r["small"]["plan"]["reason"]), (16.0, "hand"))
        # A hand target set during the visit wins and leaves the visit's progress.
        self.call("/api/cottage/room_target", {"room": "large", "target": 12})
        r = rooms()
        self.assertEqual((r["large"]["plan"]["target"], r["large"]["plan"]["reason"]), (12.0, "hand"))
        self.assertNotIn("large", self.call("/api/visit")[1]["progress"])
        self.assertEqual(self.service.cottage.visit_notices(), [])
        # Changing only the departure keeps it; a new visit target takes the room back.
        self.call("/api/visit/update", {"departure": self.arrival(5)})
        self.assertEqual(rooms()["large"]["hand"]["target"], 12.0)
        self.call("/api/visit/update", {"target": 22})
        self.assertIsNone(rooms()["large"]["hand"])
        _, events = self.call("/api/events")
        self.assertIn("clear_hand", [e["action"] for e in events])
        # Ending the visit leaves Kammari's hand target alone.
        self.call("/api/visit/end", {})
        self.assertEqual(rooms()["small"]["hand"]["target"], 16.0)

    def test_without_a_cottage_the_visit_paths_are_404(self):
        with tempfile.TemporaryDirectory() as temp:
            from villa_kuppis.pump_probe import FakeClient, _unit_payload
            from villa_kuppis.pump_service import PumpService
            service = PumpService(temp, client=FakeClient([_unit_payload("u1", "Y")]))
            server = PumpAppServer(service, host="127.0.0.1", port=0)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                             daemon=True).start()
            self.addCleanup(service.close)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            for path, data in (("/api/visit", None), ("/api/visit", b"{}"),
                               ("/api/visit/update", b"{}"), ("/api/visit/end", b"{}")):
                request = urllib.request.Request(
                    base + path, data=data, headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, 404)


class PageTests(unittest.TestCase):
    def test_page_has_the_visit_card_in_finnish(self):
        page = render_page(demo=True)
        for text in ("Aloita vierailu", "Lopeta vierailu", "Lämmitys alkaa", "Lämmitys alkoi",
                     "Saapuminen", "Tavoite saavutettu", "12 tuntia", "\"api/visit\"",
                     "Yöllä (°C)", "id=\"visit-night\"",
                     "Suunniteltu tavoite", "Mökki ei ole vahvistanut tavoitetta"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
